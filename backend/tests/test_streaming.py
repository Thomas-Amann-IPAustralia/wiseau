"""Streaming conversions, the progress side channel, and `/usage` (ADR-058)."""

from __future__ import annotations

import asyncio
import datetime as dt
import io
import json
import time
import urllib.error

import pymupdf
import pytest
from fastapi.testclient import TestClient

import main
import usage
from parsers import file_parser, progress


@pytest.fixture()
def client() -> TestClient:
    return TestClient(main.app)


def _text_pdf(*lines: str) -> bytes:
    doc = pymupdf.open()
    for line in lines:
        page = doc.new_page()
        page.insert_text((72, 72), line)
        page.insert_text((72, 96), "A born-digital page, not a scan.")
    data = doc.tobytes()
    doc.close()
    return data


def _scanned_page() -> bytes:
    src = pymupdf.open()
    src.new_page().insert_text((72, 120), "SCANNED", fontsize=44)
    pix = src[0].get_pixmap(dpi=72)
    src.close()
    scan = pymupdf.open()
    page = scan.new_page(width=pix.width, height=pix.height)
    page.insert_image(page.rect, pixmap=pix)
    data = scan.tobytes()
    scan.close()
    return data


def _join(*parts: bytes) -> bytes:
    out = pymupdf.open()
    for part in parts:
        with pymupdf.open(stream=part, filetype="pdf") as doc:
            out.insert_pdf(doc)
    data = out.tobytes()
    out.close()
    return data


class _FakeOcr:
    name = "fake"

    def ocr_page(self, page, *, dpi, language):
        return f"local OCR of page {page.number}"


def _events(response) -> list[dict]:
    assert response.headers["content-type"].startswith("application/x-ndjson")
    return [json.loads(line) for line in response.text.splitlines() if line.strip()]


# --- The side channel ----------------------------------------------------------
def test_report_without_listener_is_a_noop():
    assert not progress.active()
    progress.report("stage", stage="inspect")  # nothing to assert but "no error"


def test_listener_hears_reports_and_a_failing_listener_is_ignored():
    heard: list[dict] = []
    with progress.listening(heard.append):
        assert progress.active()
        progress.report("page", page=1, pages=2)
    assert heard == [{"event": "page", "page": 1, "pages": 2}]
    assert not progress.active()

    def broken(event):
        raise RuntimeError("listener bug")

    with progress.listening(broken):
        progress.report("stage", stage="ocr")  # must not raise


def test_listener_reaches_worker_threads():
    heard: list[dict] = []

    async def run():
        with progress.listening(heard.append):
            await asyncio.to_thread(progress.report, "stage", stage="text_layer")

    asyncio.run(run())
    assert heard == [{"event": "stage", "stage": "text_layer"}]


def test_remote_failure_reports_a_fallback():
    class Unavailable(Exception):
        pass

    class Client:
        def convert_document(self, data, filename):
            raise Unavailable("cold")

    heard: list[dict] = []
    with progress.listening(heard.append):
        assert file_parser._call_remote("docling", Client(), Unavailable, b"%PDF", "x.pdf") is None
    assert {"event": "stage", "stage": "docling"} in heard
    assert {"event": "fallback", "engine": "docling", "reason": "Unavailable"} in heard


# --- /convert/file/stream --------------------------------------------------------
def test_file_stream_reports_stages_and_ends_with_the_plain_routes_result(client):
    pdf = _text_pdf("Stream page one", "Stream page two")
    plain = client.post("/convert/file", files={"file": ("doc.pdf", pdf, "application/pdf")})
    streamed = client.post("/convert/file/stream", files={"file": ("doc.pdf", pdf, "application/pdf")})
    assert streamed.status_code == 200
    events = _events(streamed)
    kinds = [event["event"] for event in events]
    assert kinds[0] == "started"
    assert kinds[-1] == "result"
    stages = [event["stage"] for event in events if event["event"] == "stage"]
    assert stages[:2] == ["inspect", "text_layer"]
    assert "normalize" in stages
    pages = [event for event in events if event["event"] == "page"]
    assert [(p["page"], p["pages"], p["method"]) for p in pages] == [(1, 2, "text"), (2, 2, "text")]
    final = events[-1]
    assert final["result"] == plain.json()  # byte-for-byte the same conversion
    assert final["elapsed_s"] >= 0 and final["cpu_s"] >= 0


def test_file_stream_counts_ocr_pages(client, monkeypatch):
    monkeypatch.setattr(file_parser, "_engine", lambda: _FakeOcr())
    pdf = _join(_scanned_page(), _text_pdf("A text page in between"), _scanned_page())
    events = _events(client.post("/convert/file/stream", files={"file": ("mixed.pdf", pdf, "application/pdf")}))
    ocr_stage = next(e for e in events if e["event"] == "stage" and e["stage"] == "ocr")
    assert ocr_stage["pages"] == 3 and ocr_stage["ocr_pages"] == 2
    pages = [(e["page"], e["method"], e["ocr_done"]) for e in events if e["event"] == "page"]
    assert pages == [(1, "ocr", 1), (2, "text", 1), (3, "ocr", 2)]
    assert events[-1]["event"] == "result"
    assert "local OCR of page 0" in events[-1]["result"]["markdown"]


def test_file_stream_reports_a_failure_during_the_work_as_an_error_event(client):
    response = client.post("/convert/file/stream", files={"file": ("notes.txt", b"hello", "text/plain")})
    assert response.status_code == 200  # the stream had started
    final = _events(response)[-1]
    assert final == {"event": "error", "status": 415, "detail": final["detail"]}
    assert "Unsupported file type" in final["detail"]


def test_file_stream_refuses_before_streaming_with_an_http_error(client, monkeypatch):
    assert client.post("/convert/file/stream", files={"file": ("e.pdf", b"", "application/pdf")}).status_code == 400
    monkeypatch.setattr(main, "MAX_UPLOAD_BYTES", 8)
    big = client.post("/convert/file/stream", files={"file": ("b.pdf", b"x" * 64, "application/pdf")})
    assert big.status_code == 413
    bad_engine = client.post("/convert/file/stream", data={"engine": "nope"},
                             files={"file": ("d.pdf", b"x", "application/pdf")})
    assert bad_engine.status_code == 400


def test_file_stream_reports_chapters_stage_when_asked(client):
    pdf = _text_pdf("Chapter 1 One", "Chapter 2 Two")
    events = _events(client.post("/convert/file/stream", data={"split_chapters": "true"},
                                 files={"file": ("book.pdf", pdf, "application/pdf")}))
    assert {"event": "stage", "stage": "chapters"} in events
    assert "chapter_detection" in events[-1]["result"]


def test_stream_sends_heartbeats_while_quiet(client, monkeypatch):
    monkeypatch.setattr(main, "STREAM_HEARTBEAT_SECONDS", 0.05)

    def slow(url, engine=None):
        time.sleep(0.3)
        return "# Slow\n"

    monkeypatch.setattr(main, "url_to_markdown", slow)
    events = _events(client.post("/convert/url/stream", json={"url": "https://example.com"}))
    assert any(event["event"] == "heartbeat" for event in events)
    assert events[-1]["event"] == "result"
    assert events[-1]["result"]["markdown"] == "# Slow\n"


def test_url_stream_relays_the_workers_reports(client, monkeypatch):
    def worker(url, engine=None):
        progress.report("stage", stage="render")
        progress.report("stage", stage="extract")
        return "# Page\n"

    monkeypatch.setattr(main, "url_to_markdown", worker)
    events = _events(client.post("/convert/url/stream", json={"url": "https://example.com"}))
    stages = [event["stage"] for event in events if event["event"] == "stage"]
    assert stages == ["render", "extract"]
    assert events[-1]["result"]["source"] == "https://example.com/"


def test_url_stream_reports_worker_failure_as_502_event(client, monkeypatch):
    def boom(url, engine=None):
        raise RuntimeError("render exploded")

    monkeypatch.setattr(main, "url_to_markdown", boom)
    final = _events(client.post("/convert/url/stream", json={"url": "https://example.com"}))[-1]
    assert final["event"] == "error" and final["status"] == 502


# --- /convert/batch/stream -------------------------------------------------------
def test_batch_stream_announces_each_document_and_matches_the_plain_route(client):
    files = [
        ("files", ("a.pdf", _text_pdf("Alpha document"), "application/pdf")),
        ("files", ("b.txt", b"nope", "text/plain")),
        ("files", ("c.pdf", _text_pdf("Gamma document"), "application/pdf")),
    ]
    plain = client.post("/convert/batch", files=files).json()
    events = _events(client.post("/convert/batch/stream", files=files))
    documents = [(e["index"], e["count"], e["source"]) for e in events if e["event"] == "document"]
    assert documents == [(1, 3, "a.pdf"), (2, 3, "b.txt"), (3, 3, "c.pdf")]
    assert events[-1]["event"] == "result"
    assert events[-1]["result"] == plain


def test_batch_stream_keeps_the_batch_rules(client, monkeypatch):
    one = [("files", ("a.pdf", _text_pdf("Alpha"), "application/pdf"))]
    refused = client.post("/convert/batch/stream", data={"split_chapters": "true"}, files=one)
    assert refused.status_code == 400
    monkeypatch.setattr(main, "MAX_BATCH_FILES", 1)
    two = one + [("files", ("b.pdf", _text_pdf("Beta"), "application/pdf"))]
    assert client.post("/convert/batch/stream", files=two).status_code == 400


# --- /usage -------------------------------------------------------------------
def test_usage_off_cloud_run_reports_the_process_and_why_not_the_deployment(client, monkeypatch):
    monkeypatch.delenv("K_SERVICE", raising=False)
    monkeypatch.setattr(usage, "deployment_usage", usage.DeploymentUsage())
    body = client.get("/usage").json()
    assert body["deployment"]["source"] is None
    assert "Cloud Run" in body["deployment"]["reason"]
    assert body["process"]["cpu_seconds"] >= 0
    assert set(body["process"]) == {"cpu_seconds", "busy_seconds", "uptime_seconds", "jobs"}
    assert body["allowance_vcpu_seconds"] == 180_000
    assert body["period_start"].endswith("-01T00:00:00Z")


def test_process_usage_counts_busy_time_once_for_overlapping_jobs():
    counter = usage.ProcessUsage()
    counter.job_started()
    counter.job_started()
    time.sleep(0.05)
    counter.job_finished()
    counter.job_finished()
    snapshot = counter.snapshot()
    assert snapshot["jobs"] == 2
    assert 0.04 <= snapshot["busy_seconds"] < 1


class _FakeGoogle:
    """The metadata server and the Monitoring API, as `fetch` sees them."""

    def __init__(self, monitoring_status: int = 200):
        self.monitoring_status = monitoring_status
        self.calls: list[str] = []

    def __call__(self, request):
        url = request.full_url
        self.calls.append(url)
        if url.endswith("project/project-id"):
            assert request.get_header("Metadata-flavor") == "Google"
            return b"demo-project"
        if url.endswith("service-accounts/default/token"):
            return json.dumps({"access_token": "tok"}).encode()
        assert "monitoring.googleapis.com/v3/projects/demo-project/timeSeries" in url
        assert request.get_header("Authorization") == "Bearer tok"
        assert "billable_instance_time" in url and "wiseau-backend" in url
        if self.monitoring_status != 200:
            raise urllib.error.HTTPError(url, self.monitoring_status, "denied", {}, io.BytesIO(b""))
        return json.dumps({"timeSeries": [{"points": [{"value": {"doubleValue": 1200.5}}]},
                                          {"points": [{"value": {"doubleValue": 300}}]}]}).encode()


def test_deployment_usage_reads_billable_time_from_monitoring(monkeypatch):
    monkeypatch.setenv("K_SERVICE", "wiseau-backend")
    monkeypatch.setenv("WISEAU_VCPUS", "2")
    google = _FakeGoogle()
    clock = [0.0]
    reader = usage.DeploymentUsage(fetch=google, clock=lambda: clock[0])
    now = dt.datetime(2026, 11, 12, 3, 0, tzinfo=dt.timezone.utc)
    result = reader.read(now)
    assert result["source"] == "cloud_monitoring"
    assert result["billable_instance_seconds"] == 1500.5
    assert result["vcpu_seconds"] == 3001.0
    monitoring_url = google.calls[-1]
    assert "2026-11-01T00%3A00%3A00Z" in monitoring_url  # the month so far
    calls = len(google.calls)
    clock[0] += 60
    assert reader.read(now) == result and len(google.calls) == calls  # cached
    clock[0] += 400
    reader.read(now)
    assert len(google.calls) > calls  # refreshed after five minutes


def test_deployment_usage_explains_a_permission_failure(monkeypatch):
    monkeypatch.setenv("K_SERVICE", "wiseau-backend")
    reader = usage.DeploymentUsage(fetch=_FakeGoogle(monitoring_status=403))
    result = reader.read(dt.datetime(2026, 11, 12, tzinfo=dt.timezone.utc))
    assert result["source"] is None
    assert "roles/monitoring.viewer" in result["reason"]


def test_deployment_usage_can_be_turned_off(monkeypatch):
    monkeypatch.setenv("K_SERVICE", "wiseau-backend")
    monkeypatch.setenv("WISEAU_USAGE_MONITORING", "off")
    google = _FakeGoogle()
    result = usage.DeploymentUsage(fetch=google).read()
    assert result["source"] is None and google.calls == []


def test_conversions_count_as_process_jobs(client):
    before = usage.process_usage.snapshot()["jobs"]
    client.post("/convert/file", files={"file": ("doc.pdf", _text_pdf("Counted"), "application/pdf")})
    assert usage.process_usage.snapshot()["jobs"] == before + 1
