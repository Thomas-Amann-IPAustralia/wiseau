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


# --- Chapter splitting (ADR-030) --------------------------------------------
# A chaptered document, small enough to convert instantly. The URL worker is
# mocked in these, so what is under test is the endpoint's plumbing: the flag on
# the way in, the two extra fields on the way out, and the counters.
CHAPTERED = """# The Ledger

## Contents

Chapter 1: The Arrival ....... 3
Chapter 2: The Ledger ........ 45
Chapter 3: The Reckoning ..... 88

## Chapter 1: The Arrival

It began, as these things do, with a misplaced decimal point in a spreadsheet.
The auditors arrived on a Tuesday and stayed until the following spring, and by
then the office had learned to answer the telephone in a particular careful way.

## Chapter 2: The Ledger

Nobody in the office admitted to having touched the ledger that quarter.
By the time anyone checked the archive the paper trail had gone entirely cold,
which is the sort of thing that reads as carelessness and is almost never that.

## Chapter 3: The Reckoning

The report ran to nine hundred pages and named nobody at all.
It was filed on a Friday afternoon and read, as far as anyone knows, by no one,
and the decimal point stayed exactly where somebody had once decided to put it.
"""


def test_a_conversion_returns_no_chapters_unless_asked(client, monkeypatch):
    # The split roughly doubles the response, so an existing client must see
    # exactly the response it always saw (ADR-003: one contract, not a fork).
    monkeypatch.setattr(main, "url_to_markdown", lambda url, engine=None: CHAPTERED)
    body = client.post("/convert/url", json={"url": "https://example.com"}).json()

    assert body["chapters"] is None
    assert body["chapter_detection"] is None


def test_a_url_conversion_can_ask_for_chapters(client, monkeypatch):
    monkeypatch.setattr(main, "url_to_markdown", lambda url, engine=None: CHAPTERED)
    body = client.post(
        "/convert/url", json={"url": "https://example.com", "split_chapters": True}
    ).json()

    assert body["chapter_detection"] == "toc"
    assert [chapter["title"] for chapter in body["chapters"]][1:] == [
        "Chapter 1: The Arrival",
        "Chapter 2: The Ledger",
        "Chapter 3: The Reckoning",
    ]
    # The whole document is still returned; chapters are additional, not instead.
    assert body["markdown"] == CHAPTERED


def test_an_upload_can_ask_for_chapters(client, monkeypatch):
    monkeypatch.setattr(main, "file_to_markdown", lambda data, name, engine=None: CHAPTERED)
    resp = client.post(
        "/convert/file",
        files={"file": ("ledger.pdf", b"%PDF-1.4 stub", "application/pdf")},
        data={"split_chapters": "true"},
    )

    assert resp.status_code == 200
    chapters = resp.json()["chapters"]
    assert len(chapters) == 4
    # Each chapter arrives ready to be written to disk.
    assert chapters[1]["filename"] == "01-chapter-1-the-arrival.md"
    assert chapters[1]["length"] == len(chapters[1]["markdown"])
    assert chapters[1]["level"] == 2


def test_a_document_without_chapters_says_so_rather_than_inventing_them(client, monkeypatch):
    monkeypatch.setattr(main, "url_to_markdown", lambda url, engine=None: "# An Article\n\nOne idea, told once.\n")
    body = client.post(
        "/convert/url", json={"url": "https://example.com", "split_chapters": True}
    ).json()

    assert body["chapters"] == []
    assert body["chapter_detection"] == "none"


def test_chapter_splits_are_counted_by_method(client, fresh_metrics, monkeypatch):
    monkeypatch.setattr(main, "url_to_markdown", lambda url, engine=None: CHAPTERED)
    client.post("/convert/url", json={"url": "https://example.com", "split_chapters": True})
    client.post("/convert/url", json={"url": "https://example.com"})  # not requested

    chapters = client.get("/metrics").json()["chapters"]
    assert chapters["requested"] == 1
    assert chapters["split"] == 1
    assert chapters["sections"] == 4
    assert chapters["by_method"] == {"toc": 1}


def test_the_split_flag_is_documented_in_the_openapi_schema(client):
    schema = client.get("/openapi.json").json()
    assert "split_chapters" in schema["components"]["schemas"]["UrlRequest"]["properties"]
    assert "split_chapters" in schema["components"]["schemas"]["Body_convert_file"]["properties"]
    assert "chapters" in schema["components"]["schemas"]["MarkdownResponse"]["properties"]


def test_a_failing_split_does_not_lose_the_conversion(client, fresh_metrics, monkeypatch):
    # The document converted — possibly after a minute of docling. A bug in the
    # chapter heuristics is not a reason to throw that away, but it must not be
    # silent either: the caller is told, and the traceback is logged.
    monkeypatch.setattr(main, "url_to_markdown", lambda url, engine=None: CHAPTERED)
    monkeypatch.setattr(
        main, "split_into_chapters", lambda markdown: (_ for _ in ()).throw(RuntimeError("boom"))
    )
    resp = client.post("/convert/url", json={"url": "https://example.com", "split_chapters": True})

    assert resp.status_code == 200
    body = resp.json()
    assert body["markdown"] == CHAPTERED
    assert body["chapters"] == []
    assert body["chapter_detection"] == "error"
    assert client.get("/metrics").json()["chapters"]["by_method"] == {"error": 1}


# --- Batch conversion (ADR-031) ---------------------------------------------
# A batch is N independent conversions behind one request. The properties worth
# pinning are the ones that make it usable as "convert these and zip them":
# every document gets an answer, one bad document does not cost the others, the
# files are named uniquely, and it cannot be used to slip past the guards that
# bound a single upload.
def _pdf(text: str) -> bytes:
    """A one-page born-digital PDF carrying `text`.

    The second line is padding, not decoration: a page with fewer than 16
    extractable characters is taken for a scan and sent to OCR (`file_parser`),
    which would quietly make these tests depend on a `tesseract` binary instead
    of on the batch. Short fixture titles stay readable this way.
    """
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 72), text)
    page.insert_text((72, 96), "A born-digital page, not a scan.")
    data = doc.tobytes()
    doc.close()
    return data


def _upload(name: str, text: str = "Body of the document") -> tuple[str, tuple[str, bytes, str]]:
    return ("files", (name, _pdf(text), "application/pdf"))


def test_batch_converts_every_document(client):
    resp = client.post(
        "/convert/batch",
        files=[_upload("one.pdf", "First document"), _upload("two.pdf", "Second document")],
    )
    assert resp.status_code == 200
    body = resp.json()
    assert (body["count"], body["succeeded"], body["failed"]) == (2, 2, 0)
    assert [item["source"] for item in body["results"]] == ["one.pdf", "two.pdf"]
    assert "First document" in body["results"][0]["markdown"]
    assert "Second document" in body["results"][1]["markdown"]


def test_batch_results_keep_the_order_they_were_sent(client):
    names = [f"doc-{index}.pdf" for index in range(6)]
    resp = client.post("/convert/batch", files=[_upload(name, name) for name in names])
    assert [item["source"] for item in resp.json()["results"]] == names


def test_each_document_carries_the_filename_to_save_it_as(client):
    resp = client.post(
        "/convert/batch",
        files=[_upload("Annual Report 2025.pdf"), _upload("Annual Report 2025.pdf")],
    )
    assert [item["filename"] for item in resp.json()["results"]] == [
        "annual-report-2025.md",
        "annual-report-2025-2.md",
    ]


def test_one_bad_document_does_not_fail_the_batch(client):
    resp = client.post(
        "/convert/batch",
        files=[
            _upload("good.pdf", "Readable body"),
            ("files", ("notes.txt", b"plain text", "text/plain")),
            _upload("also-good.pdf", "Another readable body"),
        ],
    )
    assert resp.status_code == 200
    body = resp.json()
    assert (body["count"], body["succeeded"], body["failed"]) == (3, 2, 1)

    failure = body["results"][1]
    assert failure["status"] == "error"
    assert "Unsupported file type" in failure["error"]
    # No filename on a failure: "save everything with a filename" must never
    # write an empty file where a document should have been.
    assert failure["filename"] is None
    assert failure["markdown"] == ""
    assert body["results"][2]["status"] == "ok"


def test_a_conversion_crash_is_contained_to_its_own_document(client, monkeypatch):
    def explode_on_second(data, filename, engine=None):
        if filename == "boom.pdf":
            raise RuntimeError("parser exploded")
        return "# Fine\n"

    monkeypatch.setattr(main, "file_to_markdown", explode_on_second)
    resp = client.post(
        "/convert/batch", files=[_upload("fine.pdf"), _upload("boom.pdf"), _upload("also-fine.pdf")]
    )
    body = resp.json()
    assert [item["status"] for item in body["results"]] == ["ok", "error", "ok"]
    assert "parser exploded" in body["results"][1]["error"]


def test_an_empty_document_in_a_batch_is_reported_not_skipped(client):
    resp = client.post(
        "/convert/batch",
        files=[("files", ("empty.pdf", b"", "application/pdf")), _upload("real.pdf")],
    )
    body = resp.json()
    assert body["count"] == 2
    assert body["results"][0]["error"] == "Empty file upload."


def test_batch_refuses_chapter_splitting(client):
    # Mutually exclusive for now (ADR-031). Refused rather than ignored: a
    # silently dropped flag leaves the caller believing they asked for chapters.
    resp = client.post(
        "/convert/batch", files=[_upload("one.pdf")], data={"split_chapters": "true"}
    )
    assert resp.status_code == 400
    assert "split_chapters" in resp.json()["detail"]


def test_batch_accepts_an_explicit_split_chapters_false(client):
    resp = client.post(
        "/convert/batch", files=[_upload("one.pdf")], data={"split_chapters": "false"}
    )
    assert resp.status_code == 200


def test_batch_never_returns_chapters(client):
    resp = client.post("/convert/batch", files=[_upload("one.pdf")])
    item = resp.json()["results"][0]
    assert item["chapters"] is None
    assert item["chapter_detection"] is None


def test_batch_rejects_an_unknown_engine_with_400(client):
    resp = client.post("/convert/batch", files=[_upload("one.pdf")], data={"engine": "wishful"})
    assert resp.status_code == 400


def test_batch_passes_the_engine_through_to_every_document(client, monkeypatch):
    seen = []

    def record(data, filename, engine=None):
        seen.append(engine)
        return "# Fine\n"

    monkeypatch.setattr(main, "file_to_markdown", record)
    client.post(
        "/convert/batch",
        files=[_upload("one.pdf"), _upload("two.pdf")],
        data={"engine": "pymupdf"},
    )
    assert seen == ["pymupdf", "pymupdf"]


def test_batch_caps_how_many_files_one_request_may_carry(client, monkeypatch):
    # The cap is what stops a batch being the way around the per-IP rate limit:
    # one request must not be able to buy unbounded conversion.
    monkeypatch.setattr(main, "MAX_BATCH_FILES", 3)
    resp = client.post("/convert/batch", files=[_upload(f"{i}.pdf") for i in range(4)])
    assert resp.status_code == 400
    assert "limited to 3 files" in resp.json()["detail"]


def test_batch_caps_its_total_size(client, monkeypatch):
    # Running out of room mid-batch is a request-level refusal, not a per-file
    # one: nothing after this point could have converted either.
    monkeypatch.setattr(main, "MAX_BATCH_BYTES", 4096)
    bulk = b"%PDF-" + b"x" * 3000
    resp = client.post(
        "/convert/batch",
        files=[
            ("files", ("a.pdf", bulk, "application/pdf")),
            ("files", ("b.pdf", bulk, "application/pdf")),
        ],
    )
    assert resp.status_code == 413
    assert "batch exceeds" in resp.json()["detail"]


def test_one_oversized_document_does_not_sink_the_batch(client, monkeypatch):
    # Per-file and per-batch ceilings are different failures: too big *for the
    # batch* means nothing after it could convert either, but one outsized
    # document among ordinary ones is that document's problem alone.
    monkeypatch.setattr(main, "MAX_UPLOAD_BYTES", 2048)
    monkeypatch.setattr(main, "MAX_BATCH_BYTES", 10 * 1024 * 1024)
    resp = client.post(
        "/convert/batch",
        files=[("files", ("huge.pdf", b"x" * 8192, "application/pdf")), _upload("small.pdf")],
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["results"][0]["status"] == "error"
    assert "limit" in body["results"][0]["error"]
    assert body["results"][1]["status"] == "ok"


def test_batch_requires_at_least_one_file(client):
    resp = client.post("/convert/batch", data={"engine": "auto"})
    assert resp.status_code == 422  # the `files` part is required


def test_batch_has_its_own_tighter_rate_limit(client, monkeypatch):
    # A batch buys N conversions per token, so it cannot share the 20/minute a
    # single-document request gets (invariant #4).
    monkeypatch.setattr(main, "file_to_markdown", lambda data, filename, engine=None: "# Fine\n")
    codes = [
        client.post("/convert/batch", files=[_upload("one.pdf")]).status_code for _ in range(6)
    ]
    assert codes[:5] == [200] * 5
    assert codes[5] == 429


def test_a_batch_takes_one_job_slot_per_document(client, fresh_metrics, monkeypatch):
    # Per document, not per batch: holding the ceiling for a whole 20-file run
    # would starve every other caller for minutes.
    monkeypatch.setattr(main, "file_to_markdown", lambda data, filename, engine=None: "# Fine\n")
    client.post("/convert/batch", files=[_upload("a.pdf"), _upload("b.pdf"), _upload("c.pdf")])

    jobs = client.get("/metrics").json()["jobs"]
    assert jobs["duration"]["count"] == 3
    assert jobs["in_flight"] == 0


def test_batch_conversions_are_counted(client, fresh_metrics):
    client.post(
        "/convert/batch",
        files=[_upload("a.pdf"), ("files", ("b.txt", b"x", "text/plain"))],
    )
    body = client.get("/metrics").json()
    assert body["conversions"]["batch.ok"] == 1
    assert body["conversions"]["batch.error"] == 1
    assert body["batches"] == {"requested": 1, "files": 2, "failed": 1, "largest": 2}


def test_batch_is_documented_in_the_openapi_schema(client):
    schema = client.get("/openapi.json").json()
    assert schema["paths"]["/convert/batch"]["post"]["operationId"] == "convert_batch"
    assert "files" in schema["components"]["schemas"]["Body_convert_batch"]["properties"]
    assert "status" in schema["components"]["schemas"]["BatchItem"]["properties"]
