"""Tests for structured logging and the in-process metrics registry.

Everything here is a side channel: it must record faithfully, survive concurrent
writers, and never be able to break a conversion. The registry is a process-wide
singleton, so tests that assert exact counts reset it first.
"""

from __future__ import annotations

import json
import logging
import threading

import pytest

import observability
from observability import JsonLogFormatter, Metrics, _percentile, configure_logging


@pytest.fixture()
def metrics() -> Metrics:
    return Metrics()


def _record(**kwargs) -> logging.LogRecord:
    record = logging.LogRecord(
        name="markdown_engine", level=logging.INFO, pathname=__file__, lineno=1,
        msg=kwargs.pop("msg", "hello %s"), args=kwargs.pop("args", ("world",)), exc_info=None,
    )
    record.__dict__.update(kwargs)
    return record


# --- Structured logging -----------------------------------------------------
def test_json_formatter_emits_one_parseable_object():
    line = JsonLogFormatter().format(_record())
    assert "\n" not in line
    payload = json.loads(line)
    assert payload["level"] == "INFO"
    assert payload["logger"] == "markdown_engine"
    assert payload["msg"] == "hello world"
    assert payload["ts"].endswith("Z")


def test_json_formatter_merges_structured_context():
    line = JsonLogFormatter().format(_record(wiseau={"request_id": "abc123", "status": 200}))
    payload = json.loads(line)
    assert payload["request_id"] == "abc123"
    assert payload["status"] == 200


def test_json_formatter_keeps_other_extras_rather_than_dropping_them():
    payload = json.loads(JsonLogFormatter().format(_record(engine="docling")))
    assert payload["engine"] == "docling"


def test_json_formatter_serializes_unusual_values():
    # A stray object in `extra=` must not turn a log call into a crash.
    payload = json.loads(JsonLogFormatter().format(_record(wiseau={"obj": object()})))
    assert isinstance(payload["obj"], str)


def test_json_formatter_includes_exception_text():
    try:
        raise ValueError("docling exploded")
    except ValueError:
        record = _record()
        import sys

        record.exc_info = sys.exc_info()
    payload = json.loads(JsonLogFormatter().format(record))
    assert "docling exploded" in payload["exception"]


def test_configure_logging_is_idempotent(monkeypatch):
    monkeypatch.delenv("WISEAU_LOG_FORMAT", raising=False)
    root = logging.getLogger()
    original = list(root.handlers)
    try:
        configure_logging()
        configure_logging()
        assert len(root.handlers) == 1  # re-running must not double every line
        assert isinstance(root.handlers[0].formatter, JsonLogFormatter)
    finally:
        for handler in list(root.handlers):
            root.removeHandler(handler)
        for handler in original:
            root.addHandler(handler)


def test_text_format_opts_out_of_json(monkeypatch):
    monkeypatch.setenv("WISEAU_LOG_FORMAT", "text")
    root = logging.getLogger()
    original = list(root.handlers)
    try:
        configure_logging()
        assert not isinstance(root.handlers[0].formatter, JsonLogFormatter)
    finally:
        for handler in list(root.handlers):
            root.removeHandler(handler)
        for handler in original:
            root.addHandler(handler)


# --- Duration series --------------------------------------------------------
def test_percentiles_use_nearest_rank():
    samples = [float(n) for n in range(1, 101)]
    assert _percentile(samples, 50) == 50.0
    assert _percentile(samples, 95) == 95.0
    assert _percentile([], 50) == 0.0


def test_duration_snapshot_reports_count_mean_and_max(metrics):
    for value in (10.0, 20.0, 60.0):
        metrics.record_request("/convert/url", 200, value)
    series = metrics.snapshot()["requests"]["duration"]["/convert/url"]
    assert series["count"] == 3
    assert series["mean_ms"] == 30.0
    assert series["max_ms"] == 60.0


def test_empty_series_reports_only_a_zero_count(metrics):
    assert metrics.snapshot()["jobs"]["duration"] == {"count": 0}


def test_sample_window_is_bounded_but_totals_are_not(metrics):
    for _ in range(observability._SAMPLE_WINDOW + 50):
        metrics.record_request("/ping", 200, 1.0)
    series = metrics.snapshot()["requests"]["duration"]["/ping"]
    assert series["count"] == observability._SAMPLE_WINDOW + 50
    assert len(metrics._request_durations["/ping"].recent) == observability._SAMPLE_WINDOW


# --- Counters ---------------------------------------------------------------
def test_requests_are_counted_by_route_and_status(metrics):
    metrics.record_request("/convert/url", 200, 5.0)
    metrics.record_request("/convert/url", 502, 5.0)
    metrics.record_request("/ping", 200, 1.0)
    snapshot = metrics.snapshot()
    assert snapshot["requests"]["by_route"] == {"/convert/url": 2, "/ping": 1}
    assert snapshot["requests"]["by_status"] == {"200": 2, "502": 1}


def test_job_slots_track_concurrency_high_water_mark(metrics):
    metrics.job_started(0.0)
    metrics.job_started(3.0)
    assert metrics.snapshot()["jobs"]["in_flight"] == 2
    metrics.job_finished(100.0)
    metrics.job_finished(200.0)
    snapshot = metrics.snapshot()
    assert snapshot["jobs"]["in_flight"] == 0
    assert snapshot["jobs"]["max_in_flight"] == 2  # what MAX_CONCURRENT_JOBS is tuned against
    assert snapshot["jobs"]["duration"]["count"] == 2
    assert snapshot["jobs"]["queue_wait"]["max_ms"] == 3.0


def test_conversions_and_engines_are_attributed(metrics):
    metrics.record_conversion("file", "ok")
    metrics.record_conversion("url", "error")
    metrics.record_engine("docling")
    metrics.record_engine("pymupdf")
    metrics.record_engine("docling")
    snapshot = metrics.snapshot()
    assert snapshot["conversions"] == {"file.ok": 1, "url.error": 1}
    assert snapshot["engines"] == {"docling": 2, "pymupdf": 1}


def test_docling_successes_and_fallbacks_are_recorded_apart(metrics):
    metrics.record_docling_attempt(120.0, ok=True)
    metrics.record_docling_attempt(90.0, ok=False, reason="DoclingUnavailable")
    metrics.record_docling_attempt(80.0, ok=False, reason="DoclingBadDocument")
    metrics.record_docling_skipped("not_configured")
    docling = metrics.snapshot()["docling"]
    assert docling["attempts"] == 3
    assert docling["successes"] == 1
    assert docling["fallbacks"] == 2
    assert docling["skipped"] == 1
    assert docling["reasons"] == {
        "DoclingBadDocument": 1,
        "DoclingUnavailable": 1,
        "not_configured": 1,
    }
    assert docling["duration"]["count"] == 3


def test_chapter_splits_are_recorded_by_method(metrics):
    metrics.record_chapter_split("toc", 6)
    metrics.record_chapter_split("headings", 3)
    metrics.record_chapter_split("none", 0)
    chapters = metrics.snapshot()["chapters"]
    assert chapters["requested"] == 3
    # A request that found nothing is still a request; only two produced files.
    assert chapters["split"] == 2
    assert chapters["sections"] == 9
    assert chapters["by_method"] == {"headings": 1, "none": 1, "toc": 1}


def test_snapshot_reports_zeroes_before_anything_happens(metrics):
    snapshot = metrics.snapshot()
    assert snapshot["docling"] == {
        "attempts": 0,
        "successes": 0,
        "fallbacks": 0,
        "skipped": 0,
        "reasons": {},
        "duration": {"count": 0},
    }
    assert snapshot["engines"] == {}
    assert snapshot["chapters"] == {"requested": 0, "split": 0, "sections": 0, "by_method": {}}
    assert snapshot["uptime_seconds"] >= 0


def test_snapshot_is_json_serializable_and_reports_memory(metrics):
    snapshot = metrics.snapshot()
    json.dumps(snapshot)  # must survive the HTTP layer
    assert snapshot["memory"]["peak_rss_mb"] > 0


def test_reset_clears_every_series(metrics):
    metrics.record_request("/ping", 200, 1.0)
    metrics.record_engine("docling")
    metrics.reset()
    snapshot = metrics.snapshot()
    assert snapshot["requests"]["by_route"] == {}
    assert snapshot["engines"] == {}


def test_counters_survive_concurrent_writers(metrics):
    # Heavy work runs in `asyncio.to_thread`, so the parsers record from worker
    # threads while the event loop records requests.
    def worker() -> None:
        for _ in range(200):
            metrics.record_engine("docling")
            metrics.record_request("/convert/file", 200, 1.0)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    snapshot = metrics.snapshot()
    assert snapshot["engines"]["docling"] == 1600
    assert snapshot["requests"]["by_route"]["/convert/file"] == 1600
