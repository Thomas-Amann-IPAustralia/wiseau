"""Tests for the HTTP surface and its validation contract.

These exercise routing, request validation, and error codes without touching a
real browser: the one URL-conversion test monkeypatches the heavy ``url_to_
markdown`` worker so the endpoint's plumbing (semaphore, threading, response
shape) is verified while Chromium is not.
"""

from __future__ import annotations

import io

import pymupdf
import pytest
from fastapi.testclient import TestClient

import main


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
    monkeypatch.setattr(main, "url_to_markdown", lambda url: "# Rendered\n")
    resp = client.post("/convert/url", json={"url": "https://example.com"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["source"] == "https://example.com/"
    assert body["markdown"] == "# Rendered\n"
    assert body["length"] == len("# Rendered\n")


def test_convert_url_surfaces_worker_failure_as_502(client, monkeypatch):
    def boom(url):
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
    monkeypatch.setattr(main, "url_to_markdown", lambda url: "# Rendered\n")
    client.post("/convert/url", json={"url": "https://example.com"})

    body = client.get("/metrics").json()
    assert body["conversions"]["url.ok"] == 1
    assert body["jobs"]["duration"]["count"] == 1
    assert body["jobs"]["max_in_flight"] == 1


def test_a_failed_conversion_is_recorded_as_such(client, fresh_metrics, monkeypatch):
    def boom(url):
        raise RuntimeError("render exploded")

    monkeypatch.setattr(main, "url_to_markdown", boom)
    client.post("/convert/url", json={"url": "https://example.com"})

    body = client.get("/metrics").json()
    assert body["conversions"]["url.error"] == 1
    assert body["requests"]["by_status"]["502"] == 1
    assert body["jobs"]["in_flight"] == 0  # the slot is released even on failure


def test_an_upload_attributes_the_engine_that_served_it(client, fresh_metrics, monkeypatch):
    # No docling base configured (the default), so the deterministic parser
    # serves the upload and the skip is visible as such.
    monkeypatch.delenv("WISEAU_DOCLING_BASE", raising=False)
    doc = pymupdf.open()
    doc.new_page().insert_text((72, 72), "Attribution Body")
    data = doc.tobytes()
    doc.close()

    client.post("/convert/file", files={"file": ("doc.pdf", data, "application/pdf")})

    body = client.get("/metrics").json()
    assert body["engines"]["pymupdf"] == 1
    assert body["docling"]["skipped"] == 1
    assert body["docling"]["reasons"]["not_configured"] == 1
    assert body["conversions"]["file.ok"] == 1


def test_metrics_is_exposed_as_an_openapi_operation(client):
    schema = client.get("/openapi.json").json()
    assert schema["paths"]["/metrics"]["get"]["operationId"] == "metrics"
