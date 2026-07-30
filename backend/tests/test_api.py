"""Tests for the HTTP surface and its validation contract.

These exercise routing, request validation, and error codes without touching a
real browser: the one URL-conversion test monkeypatches the heavy ``url_to_
markdown`` worker so the endpoint's plumbing (semaphore, threading, response
shape) is verified while Chromium is not.
"""

from __future__ import annotations

import asyncio
import io

import pymupdf
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

import main
from parsers import BlockedUrlError


@pytest.fixture()
def client() -> TestClient:
    return TestClient(main.app)


def test_ping_reports_ok(client):
    resp = client.get("/ping")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["service"] == "markdown-ingestion-engine"
    assert body["version"] == main.app.version


def test_convert_url_rejects_invalid_url(client):
    resp = client.post("/convert/url", json={"url": "not-a-url"})
    assert resp.status_code == 422


def test_convert_url_requires_url_field(client):
    resp = client.post("/convert/url", json={})
    assert resp.status_code == 422


def test_convert_url_happy_path_is_mocked(client, monkeypatch):
    monkeypatch.setattr(main, "url_to_markdown", lambda url, engine=None: "# Rendered\n")
    resp = client.post("/convert/url", json={"url": "https://example.com"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["source"] == "https://example.com/"
    assert body["markdown"] == "# Rendered\n"
    assert body["length"] == len("# Rendered\n")


def test_convert_url_surfaces_worker_failure_as_502(client, monkeypatch):
    def boom(url, engine=None):
        raise RuntimeError("render exploded")

    monkeypatch.setattr(main, "url_to_markdown", boom)
    resp = client.post("/convert/url", json={"url": "https://example.com"})
    assert resp.status_code == 502


def test_convert_file_rejects_empty_upload(client):
    resp = client.post("/convert/file", files={"file": ("empty.pdf", b"", "application/pdf")})
    assert resp.status_code == 400


def test_convert_file_rejects_unsupported_type_with_415(client):
    resp = client.post("/convert/file", files={"file": ("notes.txt", b"hello", "text/plain")})
    assert resp.status_code == 415


def test_convert_file_rejects_oversized_upload_with_413(client, monkeypatch):
    monkeypatch.setattr(main, "MAX_UPLOAD_BYTES", 8)
    resp = client.post("/convert/file", files={"file": ("big.pdf", b"x" * 64, "application/pdf")})
    assert resp.status_code == 413


def test_oversized_upload_is_refused_without_being_assembled(monkeypatch):
    """The 413 must land *before* the whole body is joined into one bytes object.

    Reading first and checking after would materialize an arbitrarily large
    upload in the container's RAM to then throw it away, which is how a
    memory-sized free-tier box gets OOM-killed by a single request.
    """

    class _CountingUpload:
        """An upload that serves bytes on demand and records what was taken."""

        def __init__(self, size: int) -> None:
            self.remaining = size
            self.served = 0

        async def read(self, size: int = -1) -> bytes:
            take = self.remaining if size < 0 else min(size, self.remaining)
            self.remaining -= take
            self.served += take
            return b"x" * take

    monkeypatch.setattr(main, "MAX_UPLOAD_BYTES", 1024 * 1024)
    upload = _CountingUpload(64 * 1024 * 1024)

    with pytest.raises(HTTPException) as excinfo:
        asyncio.run(main._read_upload(upload))

    assert excinfo.value.status_code == 413
    # It stopped one chunk past the limit, with most of the body never read.
    assert upload.served <= main.MAX_UPLOAD_BYTES + main._UPLOAD_CHUNK_BYTES
    assert upload.remaining > 0


def test_a_blocked_url_is_a_400_not_a_502(client, monkeypatch):
    # Asking for a host the deployment refuses is a bad request, not a failed
    # render — a 502 would read as "wiseau is broken" (ADR-021).
    def blocked(url, engine=None):
        raise BlockedUrlError("Refusing to fetch 'localhost': non-public address.")

    monkeypatch.setattr(main, "url_to_markdown", blocked)
    resp = client.post("/convert/url", json={"url": "http://localhost/admin"})
    assert resp.status_code == 400
    assert "Refusing to fetch" in resp.json()["detail"]


def test_convert_file_pdf_happy_path(client):
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 72), "Integration Body")
    data = doc.tobytes()
    doc.close()

    resp = client.post("/convert/file", files={"file": ("doc.pdf", data, "application/pdf")})
    assert resp.status_code == 200
    body = resp.json()
    assert "Integration Body" in body["markdown"]
    assert body["length"] == len(body["markdown"])


def test_convert_file_docx_happy_path(client):
    docx = pytest.importorskip("docx", reason="python-docx (dev dependency) is required to synthesize a DOCX")
    document = docx.Document()
    document.add_heading("Contract Heading", level=1)
    document.add_paragraph("Body over the wire.")
    buf = io.BytesIO()
    document.save(buf)

    ctype = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    resp = client.post("/convert/file", files={"file": ("doc.docx", buf.getvalue(), ctype)})
    assert resp.status_code == 200
    body = resp.json()
    assert "# Contract Heading" in body["markdown"]
    assert "Body over the wire." in body["markdown"]
    assert body["length"] == len(body["markdown"])


def test_openapi_schema_exposes_convert_operations(client):
    schema = client.get("/openapi.json").json()
    assert "/convert/url" in schema["paths"]
    assert "/convert/file" in schema["paths"]
    assert "/ping" in schema["paths"]


# --- Observability ----------------------------------------------------------
# The registry is a process-wide singleton, so tests asserting exact counts
# reset it first. See `test_observability.py` for the registry's own unit tests.
@pytest.fixture()
def fresh_metrics():
    from observability import metrics

    metrics.reset()
    yield metrics
    metrics.reset()


def test_metrics_endpoint_reports_the_expected_shape(client, fresh_metrics):
    resp = client.get("/metrics")
    assert resp.status_code == 200
    body = resp.json()
    assert set(body) >= {"uptime_seconds", "requests", "jobs", "conversions", "engines", "docling", "memory"}
    assert body["jobs"]["in_flight"] == 0
    assert body["memory"]["peak_rss_mb"] > 0


def test_every_response_carries_a_correlation_id(client):
    first = client.get("/ping")
    second = client.get("/ping")
    assert first.headers["x-request-id"]
    assert first.headers["x-request-id"] != second.headers["x-request-id"]


def test_requests_are_counted_under_their_route_template(client, fresh_metrics):
    client.get("/ping")
    client.post("/convert/url", json={"url": "not-a-url"})  # 422
    body = client.get("/metrics").json()
    assert body["requests"]["by_route"]["/ping"] == 1
    assert body["requests"]["by_route"]["/convert/url"] == 1
    assert body["requests"]["by_status"]["422"] == 1


def test_unmatched_paths_cannot_inflate_the_route_table(client, fresh_metrics):
    for suffix in range(3):
        client.get(f"/does-not-exist-{suffix}")
    body = client.get("/metrics").json()
    assert body["requests"]["by_route"]["unmatched"] == 3


def test_a_conversion_records_its_job_and_engine(client, fresh_metrics, monkeypatch):
    monkeypatch.setattr(main, "url_to_markdown", lambda url, engine=None: "# Rendered\n")
    client.post("/convert/url", json={"url": "https://example.com"})

    body = client.get("/metrics").json()
    assert body["conversions"]["url.ok"] == 1
    assert body["jobs"]["duration"]["count"] == 1
    assert body["jobs"]["max_in_flight"] == 1


def test_a_failed_conversion_is_recorded_as_such(client, fresh_metrics, monkeypatch):
    def boom(url, engine=None):
        raise RuntimeError("render exploded")

    monkeypatch.setattr(main, "url_to_markdown", boom)
    client.post("/convert/url", json={"url": "https://example.com"})

    body = client.get("/metrics").json()
    assert body["conversions"]["url.error"] == 1
    assert body["requests"]["by_status"]["502"] == 1
    assert body["jobs"]["in_flight"] == 0  # the slot is released even on failure


def test_an_upload_attributes_the_engine_that_served_it(client, fresh_metrics, monkeypatch):
    # The default engine is the deterministic parser (ADR-027), so it serves the
    # upload and the skip is visible as a deliberate one rather than an outage.
    monkeypatch.delenv("WISEAU_PDF_ENGINE", raising=False)
    monkeypatch.delenv("WISEAU_DOCLING_BASE", raising=False)
    doc = pymupdf.open()
    doc.new_page().insert_text((72, 72), "Attribution Body")
    data = doc.tobytes()
    doc.close()

    client.post("/convert/file", files={"file": ("doc.pdf", data, "application/pdf")})

    body = client.get("/metrics").json()
    assert body["engines"]["pymupdf"] == 1
    assert body["docling"]["skipped"] == 1
    assert body["docling"]["reasons"]["engine_not_selected"] == 1
    assert body["conversions"]["file.ok"] == 1


def test_metrics_is_exposed_as_an_openapi_operation(client):
    schema = client.get("/openapi.json").json()
    assert schema["paths"]["/metrics"]["get"]["operationId"] == "metrics"


# --- Fair-use guards (invariant #4) -----------------------------------------
# `Limiter(default_limits=...)` binds *nothing* by itself: slowapi enforces a
# decorator's limit from the decorator, but the defaults only from
# `SlowAPIMiddleware`. Drop that middleware and every undecorated route silently
# goes unlimited while `@limiter.exempt` quietly stops meaning anything — a
# regression no other test would notice, hence these. The autouse
# `reset_rate_limiter` fixture keeps the counters they burn out of their
# neighbours' way.
def test_default_limit_is_enforced_on_undecorated_routes(client):
    # 60/minute is the documented default, so the 61st call in a minute is 429.
    codes = [client.get("/metrics").status_code for _ in range(61)]
    assert codes[:60] == [200] * 60
    assert codes[60] == 429


def test_ping_is_exempt_from_the_default_limit(client):
    # The probe the UI badge and uptime monitors poll must never be throttled.
    codes = {client.get("/ping").status_code for _ in range(65)}
    assert codes == {200}


def test_convert_routes_keep_their_tighter_explicit_limit(client, monkeypatch):
    monkeypatch.setattr(main, "url_to_markdown", lambda url, engine=None: "# Rendered\n")
    codes = [
        client.post("/convert/url", json={"url": "https://example.com"}).status_code
        for _ in range(21)
    ]
    assert codes[:20] == [200] * 20
    assert codes[20] == 429


def test_a_rate_limited_request_is_attributed_to_its_route(client, fresh_metrics):
    # The limit is per-route, so tripping one route leaves /metrics answerable
    # and able to report the rejection. A request rejected by the middleware
    # never reaches the router, so this also pins `_route_label`'s fallback:
    # a real registered path, never the `unmatched` bucket.
    codes = [client.get("/openapi.json").status_code for _ in range(61)]
    assert codes.count(429) == 1

    body = client.get("/metrics").json()
    assert body["requests"]["by_route"]["/openapi.json"] == 61
    assert body["requests"]["by_status"]["429"] == 1
    assert "unmatched" not in body["requests"]["by_route"]


# --- Per-request engine choice (ADR-025) ------------------------------------


def test_ping_advertises_the_selectable_engines(client):
    body = client.get("/ping").json()
    # The UI offers the choice from this list rather than hard-coding it.
    assert body["engines"] == ["docling", "pymupdf"]


def test_ping_reports_the_default_engine(client, monkeypatch):
    # "Auto" resolves server-side, so the UI has to be told what it means here —
    # it is what the progress estimate is sized against (ADR-027).
    monkeypatch.delenv("WISEAU_PDF_ENGINE", raising=False)
    assert client.get("/ping").json()["default_engine"] == "pymupdf"

    monkeypatch.setenv("WISEAU_PDF_ENGINE", "docling")
    assert client.get("/ping").json()["default_engine"] == "docling"


def test_an_upload_can_name_its_engine(client, monkeypatch):
    seen: dict = {}

    def fake_convert(data, filename, engine=None):
        seen["engine"] = engine
        return "# Converted\n"

    monkeypatch.setattr(main, "file_to_markdown", fake_convert)
    resp = client.post(
        "/convert/file",
        files={"file": ("doc.pdf", b"%PDF-1.4 stub", "application/pdf")},
        data={"engine": "docling"},
    )

    assert resp.status_code == 200
    assert seen["engine"] == "docling"


def test_a_url_request_can_name_its_engine(client, monkeypatch):
    seen: dict = {}

    def fake_convert(url, engine=None):
        seen["engine"] = engine
        return "# Converted\n"

    monkeypatch.setattr(main, "url_to_markdown", fake_convert)
    resp = client.post("/convert/url", json={"url": "https://example.com", "engine": "pymupdf"})

    assert resp.status_code == 200
    assert seen["engine"] == "pymupdf"


def test_omitting_the_engine_leaves_the_deployment_default_in_charge(client, monkeypatch):
    seen: dict = {}

    def fake_convert(url, engine=None):
        seen["engine"] = engine
        return "# Converted\n"

    monkeypatch.setattr(main, "url_to_markdown", fake_convert)
    client.post("/convert/url", json={"url": "https://example.com"})

    # `None`, not a guessed engine name: the request pins nothing it didn't ask for.
    assert seen["engine"] is None


def test_an_unknown_engine_is_a_400_not_a_502(client, monkeypatch):
    # Naming an engine this build cannot run is a caller mistake; it must not
    # read as "wiseau is broken", and must not silently convert with another one.
    monkeypatch.setattr(
        main, "url_to_markdown", lambda *a, **k: pytest.fail("no conversion may start")
    )
    resp = client.post("/convert/url", json={"url": "https://example.com", "engine": "magic"})

    assert resp.status_code == 400
    assert "magic" in resp.json()["detail"]


def test_an_unknown_engine_on_an_upload_is_rejected_before_the_file_is_read(client, monkeypatch):
    monkeypatch.setattr(
        main, "file_to_markdown", lambda *a, **k: pytest.fail("no conversion may start")
    )
    resp = client.post(
        "/convert/file",
        files={"file": ("doc.pdf", b"%PDF-1.4 stub", "application/pdf")},
        data={"engine": "magic"},
    )

    assert resp.status_code == 400


def test_the_engine_field_is_documented_in_the_openapi_schema(client):
    schema = client.get("/openapi.json").json()
    url_body = schema["components"]["schemas"]["UrlRequest"]["properties"]
    assert "engine" in url_body
    # The multipart body is emitted as its own component schema.
    form = schema["components"]["schemas"]["Body_convert_file"]["properties"]
    assert "engine" in form
