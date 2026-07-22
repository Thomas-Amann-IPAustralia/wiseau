"""Tests for the autonomous-ingestion monitor (`monitor.py`).

The monitor is a thin HTTP client over the backend, so these tests inject a fake
``fetch`` in place of the real ``POST /convert/url`` call — no live server,
browser, or network. They cover the state machine (new → unchanged → changed),
the error path (drift vs. failure), snapshot persistence, diff determinism, the
request the real fetcher builds, and the bounded watch loop.
"""

from __future__ import annotations

import json

import pytest

import monitor


def _fetcher(markdown: str):
    """A fake converter that always returns the same Markdown."""

    def fetch(url, *, api_base=monitor.API_BASE):
        return {"source": url, "markdown": markdown, "length": len(markdown)}

    return fetch


def _failing_fetcher(message: str):
    def fetch(url, *, api_base=monitor.API_BASE):
        raise monitor.MonitorError(message)

    return fetch


# --- State machine ----------------------------------------------------------
def test_first_sight_saves_baseline_as_new(tmp_path):
    store = monitor.SnapshotStore(tmp_path)
    result = monitor.check_url("https://example.com", store, fetch=_fetcher("# Hello\n"))

    assert result.status == monitor.NEW
    assert result.length == len("# Hello\n")
    assert result.diff == ""
    # The baseline is persisted for the next run.
    assert store.load("https://example.com")["markdown"] == "# Hello\n"


def test_identical_content_is_unchanged(tmp_path):
    store = monitor.SnapshotStore(tmp_path)
    fetch = _fetcher("# Hello\n\nWorld.\n")
    monitor.check_url("https://example.com", store, fetch=fetch)  # baseline
    result = monitor.check_url("https://example.com", store, fetch=fetch)

    assert result.status == monitor.UNCHANGED
    assert result.diff == ""
    assert result.previous_length == result.length


def test_content_drift_is_changed_with_a_diff(tmp_path):
    store = monitor.SnapshotStore(tmp_path)
    monitor.check_url("https://example.com", store, fetch=_fetcher("# Title\n\nold body\n"))
    result = monitor.check_url(
        "https://example.com", store, fetch=_fetcher("# Title\n\nnew body\n")
    )

    assert result.status == monitor.CHANGED
    assert result.changed is True
    # Drift is a normal outcome, not an error.
    assert result.ok is True
    assert "-old body" in result.diff
    assert "+new body" in result.diff
    assert result.previous_length == len("# Title\n\nold body\n")


def test_baseline_advances_so_change_fires_once(tmp_path):
    store = monitor.SnapshotStore(tmp_path)
    monitor.check_url("https://example.com", store, fetch=_fetcher("v1\n"))
    changed = monitor.check_url("https://example.com", store, fetch=_fetcher("v2\n"))
    settled = monitor.check_url("https://example.com", store, fetch=_fetcher("v2\n"))

    assert changed.status == monitor.CHANGED
    # After a change the baseline advances; re-seeing the same content is unchanged.
    assert settled.status == monitor.UNCHANGED


# --- Error handling ---------------------------------------------------------
def test_backend_failure_is_an_error_and_preserves_baseline(tmp_path):
    store = monitor.SnapshotStore(tmp_path)
    monitor.check_url("https://example.com", store, fetch=_fetcher("good\n"))

    result = monitor.check_url(
        "https://example.com", store, fetch=_failing_fetcher("backend error 502: boom")
    )

    assert result.status == monitor.ERROR
    assert result.ok is False
    assert "502" in result.detail
    # The last good snapshot must be untouched so the next check diffs against it.
    assert store.load("https://example.com")["markdown"] == "good\n"


def test_error_on_first_sight_saves_no_snapshot(tmp_path):
    store = monitor.SnapshotStore(tmp_path)
    result = monitor.check_url(
        "https://example.com", store, fetch=_failing_fetcher("could not reach backend")
    )

    assert result.status == monitor.ERROR
    assert store.load("https://example.com") is None


# --- Snapshot store ---------------------------------------------------------
def test_store_returns_none_before_first_save(tmp_path):
    store = monitor.SnapshotStore(tmp_path)
    assert store.load("https://never-seen.example") is None


def test_store_round_trip_records_hash_and_length(tmp_path):
    store = monitor.SnapshotStore(tmp_path)
    snapshot = store.save("https://example.com", "# Body\n")

    assert snapshot["length"] == len("# Body\n")
    assert snapshot["content_hash"] == monitor._content_hash("# Body\n")
    reloaded = store.load("https://example.com")
    assert reloaded["markdown"] == "# Body\n"
    assert reloaded["content_hash"] == snapshot["content_hash"]


def test_distinct_urls_use_distinct_files(tmp_path):
    store = monitor.SnapshotStore(tmp_path)
    store.save("https://a.example", "A\n")
    store.save("https://b.example", "B\n")

    assert store.load("https://a.example")["markdown"] == "A\n"
    assert store.load("https://b.example")["markdown"] == "B\n"


# --- Determinism ------------------------------------------------------------
def test_diff_is_deterministic_for_the_same_pair():
    first = monitor._unified_diff("a\nb\nc\n", "a\nB\nc\n", "https://x.example")
    second = monitor._unified_diff("a\nb\nc\n", "a\nB\nc\n", "https://x.example")
    assert first == second
    assert "-b" in first and "+B" in first


# --- Real fetcher (request building + error surfacing) ----------------------
def test_fetch_markdown_posts_json_to_convert_url(monkeypatch):
    captured: dict = {}

    class _FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self):
            return json.dumps({"source": "u", "markdown": "# ok\n", "length": 5}).encode("utf-8")

    def fake_urlopen(request, timeout=None):
        captured["url"] = request.full_url
        captured["method"] = request.get_method()
        captured["body"] = json.loads(request.data.decode("utf-8"))
        captured["content_type"] = request.headers.get("Content-type")
        return _FakeResponse()

    monkeypatch.setattr(monitor.urllib.request, "urlopen", fake_urlopen)
    result = monitor.fetch_markdown("https://example.com/a", api_base="http://backend:7860")

    assert captured["url"] == "http://backend:7860/convert/url"
    assert captured["method"] == "POST"
    assert captured["body"] == {"url": "https://example.com/a"}
    assert captured["content_type"] == "application/json"
    assert result["markdown"] == "# ok\n"


def test_fetch_markdown_surfaces_backend_detail(monkeypatch):
    import io
    import urllib.error

    def fake_urlopen(request, timeout=None):
        raise urllib.error.HTTPError(
            url=request.full_url,
            code=502,
            msg="Bad Gateway",
            hdrs=None,
            fp=io.BytesIO(json.dumps({"detail": "Failed to convert URL: boom"}).encode("utf-8")),
        )

    monkeypatch.setattr(monitor.urllib.request, "urlopen", fake_urlopen)
    with pytest.raises(monitor.MonitorError) as excinfo:
        monitor.fetch_markdown("https://example.com", api_base="http://backend:7860")

    message = str(excinfo.value)
    assert "502" in message
    assert "Failed to convert URL: boom" in message


def test_fetch_markdown_reports_unreachable_backend(monkeypatch):
    import urllib.error

    def fake_urlopen(request, timeout=None):
        raise urllib.error.URLError("Connection refused")

    monkeypatch.setattr(monitor.urllib.request, "urlopen", fake_urlopen)
    with pytest.raises(monitor.MonitorError) as excinfo:
        monitor.fetch_markdown("https://example.com", api_base="http://backend:7860")
    assert "could not reach backend" in str(excinfo.value)


# --- Watch loop -------------------------------------------------------------
def test_watch_runs_a_bounded_number_of_passes(tmp_path, monkeypatch):
    store = monitor.SnapshotStore(tmp_path)
    sleeps: list[float] = []
    results: list[monitor.CheckResult] = []

    # Content changes each pass so we exercise new → changed → changed.
    versions = iter(["v1\n", "v2\n", "v3\n"])

    def fetch(url, *, api_base=monitor.API_BASE):
        md = next(versions)
        return {"source": url, "markdown": md, "length": len(md)}

    # Patch the module-level fetch used inside run_once/check_url.
    monkeypatch.setattr(monitor, "fetch_markdown", fetch)
    monitor.watch(
        ["https://example.com"],
        store,
        interval=42.0,
        iterations=3,
        on_result=results.append,
        sleep=sleeps.append,
    )

    assert [r.status for r in results] == [monitor.NEW, monitor.CHANGED, monitor.CHANGED]
    # Sleeps happen *between* passes, not after the last one.
    assert sleeps == [42.0, 42.0]


# --- CLI formatting ---------------------------------------------------------
def test_format_result_variants():
    new = monitor.CheckResult("u", monitor.NEW, length=10)
    unchanged = monitor.CheckResult("u", monitor.UNCHANGED, length=10, previous_length=10)
    changed = monitor.CheckResult("u", monitor.CHANGED, length=12, previous_length=10, diff="-a\n+b")
    error = monitor.CheckResult("u", monitor.ERROR, detail="boom")

    assert monitor.format_result(new).startswith("[new]")
    assert monitor.format_result(unchanged).startswith("[unchanged]")
    changed_text = monitor.format_result(changed)
    assert changed_text.startswith("[changed]")
    assert "(+2)" in changed_text
    assert "-a\n+b" in changed_text
    assert monitor.format_result(error).startswith("[error]")


def test_main_single_pass_exit_codes(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(monitor, "fetch_markdown", _fetcher("# ok\n"))
    code = monitor.main(["--store", str(tmp_path), "https://example.com"])
    assert code == 0
    assert "[new]" in capsys.readouterr().out

    monkeypatch.setattr(monitor, "fetch_markdown", _failing_fetcher("backend error 502: boom"))
    code = monitor.main(["--store", str(tmp_path), "https://other.example"])
    assert code == 1  # an error pass exits non-zero
