"""Autonomous ingestion example — scheduled diff-checking of a URL's Markdown.

This is the Phase 4 "autonomous ingestion" surface (roadmap / tech-spec §7): a
small monitor that watches one or more web pages by converting them to Markdown
on a schedule and diffing each fresh conversion against the last one it saw.

Design mirrors ``mcp_server.py``: this is a **thin HTTP client over the running
backend**, not a second engine. Every check is a ``POST /convert/url`` to
``WISEAU_API_BASE`` — the exact route the web UI and the MCP tools use — so the
monitor inherits the backend's fair-use guards (per-IP rate limiting + the global
concurrency ceiling) unchanged. There is no in-process bypass (tech-spec
invariant #4). It uses only the Python standard library so it can run anywhere a
Python interpreter and the backend URL are reachable — no extra dependencies.

**Content drift is expected, not an error.** Determinism is *per input*, not
across time (tech-spec §7): the engine guarantees the same page snapshot yields
byte-identical Markdown, but real pages legitimately change between checks. So a
diff is a normal, reportable outcome — a ``changed`` result — while only a failure
to reach the backend or render the page is an ``error``.

Run a single pass (baseline on first sight, diff thereafter)::

    cd backend
    WISEAU_API_BASE=http://localhost:7860 \
        python monitor.py https://example.com/article

Watch on a schedule (checks every hour until interrupted)::

    python monitor.py --watch --interval 3600 https://example.com/article

Snapshots persist under ``WISEAU_SNAPSHOT_DIR`` (default ``.wiseau-snapshots``),
one JSON file per URL, so state survives across runs.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import difflib
import hashlib
import json
import os
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

# --- Configuration ----------------------------------------------------------
# The monitor's only coupling to the backend is this base URL — mirroring the
# frontend's `MARKDOWN_API_BASE` and the MCP server's `WISEAU_API_BASE`.
API_BASE = os.environ.get("WISEAU_API_BASE", "http://localhost:7860").rstrip("/")

# Where per-URL snapshots live between runs. One JSON file per URL.
DEFAULT_SNAPSHOT_DIR = os.environ.get("WISEAU_SNAPSHOT_DIR", ".wiseau-snapshots")

# Renders can be slow; allow a generous per-request timeout (seconds).
REQUEST_TIMEOUT = float(os.environ.get("WISEAU_MONITOR_TIMEOUT", "120"))

# Result statuses.
NEW = "new"            # first time we've seen this URL — baseline saved, no diff.
UNCHANGED = "unchanged"  # identical Markdown to the previous check.
CHANGED = "changed"    # Markdown differs — content drift; a unified diff is attached.
ERROR = "error"        # backend unreachable or the page failed to render.


class MonitorError(RuntimeError):
    """Raised when a URL cannot be converted (backend error or unreachable).

    Distinct from a *content change*: an error means we could not obtain fresh
    Markdown at all, so there is nothing to diff against the saved snapshot.
    """


# --- HTTP plumbing ----------------------------------------------------------
def fetch_markdown(url: str, *, api_base: str = API_BASE, timeout: float = REQUEST_TIMEOUT) -> dict:
    """Convert ``url`` via the backend and return its ``MarkdownResponse`` dict.

    Uses the public ``POST /convert/url`` route — the same one the UI and MCP
    tools use — so this call is rate-limited and concurrency-capped by the
    backend exactly like any other client (tech-spec invariant #4). The backend's
    ``detail`` message is surfaced verbatim on failure, never a stack trace.

    Args:
        url: Absolute ``http``/``https`` URL to convert.
        api_base: Base URL of the running backend.
        timeout: Per-request timeout in seconds.

    Returns:
        ``{"source": <url>, "markdown": <content>, "length": <int>}``.

    Raises:
        MonitorError: on any HTTP error or if the backend is unreachable.
    """
    payload = json.dumps({"url": url}).encode("utf-8")
    request = urllib.request.Request(
        f"{api_base}/convert/url",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise MonitorError(f"backend error {exc.code}: {_error_detail(exc)}") from exc
    except urllib.error.URLError as exc:
        raise MonitorError(f"could not reach backend at {api_base}: {exc.reason}") from exc


def _error_detail(exc: urllib.error.HTTPError) -> str:
    """Pull the backend's JSON ``detail`` out of an error body, or fall back to text."""
    try:
        body = exc.read().decode("utf-8")
    except Exception:  # noqa: BLE001 - the body may be unreadable; degrade gracefully
        return exc.reason or "unknown error"
    try:
        return str(json.loads(body).get("detail", body))
    except ValueError:  # non-JSON body
        return body or (exc.reason or "unknown error")


# --- Snapshot storage -------------------------------------------------------
def _content_hash(markdown: str) -> str:
    """Stable content fingerprint — a fast-path equality check before diffing."""
    return hashlib.sha256(markdown.encode("utf-8")).hexdigest()


def _now_iso() -> str:
    """UTC timestamp for snapshot bookkeeping (metadata only — never diffed)."""
    return _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")


class SnapshotStore:
    """Persists the last-seen Markdown for each URL as one JSON file per URL.

    The filename is derived from a hash of the URL so arbitrary URLs map to safe,
    collision-resistant filenames. Each snapshot stores the full Markdown (needed
    to compute the next diff), its content hash, length, and a ``checked_at``
    timestamp. The timestamp is operational metadata and is never part of a diff,
    so it does not affect the determinism guarantee.
    """

    def __init__(self, root: str | os.PathLike[str] = DEFAULT_SNAPSHOT_DIR) -> None:
        self.root = Path(root)

    def _path_for(self, url: str) -> Path:
        digest = hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]
        return self.root / f"{digest}.json"

    def load(self, url: str) -> Optional[dict]:
        """Return the saved snapshot for ``url``, or ``None`` if never seen."""
        path = self._path_for(url)
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    def save(self, url: str, markdown: str) -> dict:
        """Write ``markdown`` as the new baseline for ``url`` and return the snapshot."""
        self.root.mkdir(parents=True, exist_ok=True)
        snapshot = {
            "url": url,
            "content_hash": _content_hash(markdown),
            "length": len(markdown),
            "markdown": markdown,
            "checked_at": _now_iso(),
        }
        # sort_keys keeps the on-disk file byte-stable for identical snapshots
        # (modulo the timestamp), which keeps version-controlled stores clean.
        self._path_for(url).write_text(
            json.dumps(snapshot, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
        )
        return snapshot


# --- Check result -----------------------------------------------------------
@dataclass
class CheckResult:
    """Outcome of checking a single URL against its saved snapshot."""

    url: str
    status: str
    length: int = 0
    previous_length: Optional[int] = None
    diff: str = ""
    detail: Optional[str] = None

    @property
    def changed(self) -> bool:
        return self.status == CHANGED

    @property
    def ok(self) -> bool:
        """True unless the check failed to obtain fresh Markdown."""
        return self.status != ERROR


def _unified_diff(old: str, new: str, url: str) -> str:
    """Deterministic line-level unified diff between two Markdown strings.

    No dates are passed to ``unified_diff`` so the output depends only on the two
    inputs — the same before/after pair always yields the same diff.
    """
    lines = difflib.unified_diff(
        old.splitlines(),
        new.splitlines(),
        fromfile=f"{url} (previous)",
        tofile=f"{url} (current)",
        lineterm="",
    )
    return "\n".join(lines)


def check_url(
    url: str,
    store: SnapshotStore,
    *,
    fetch: Optional[Callable[..., dict]] = None,
    api_base: str = API_BASE,
) -> CheckResult:
    """Convert ``url``, compare against its snapshot, and update the baseline.

    On the first sighting the Markdown is saved as a baseline (``new``).
    Thereafter the fresh Markdown is diffed against the last snapshot: identical
    content is ``unchanged``; any difference is ``changed`` with a unified diff
    attached (content drift — expected, not an error, per tech-spec §7). A backend
    or network failure is an ``error`` and leaves the saved baseline untouched, so
    the next successful check diffs against the last *good* snapshot.

    Args:
        url: The page to check.
        store: Where snapshots are read from and written to.
        fetch: Injection seam for the converter. Defaults to the module-level
            ``fetch_markdown`` (resolved at call time so it stays patchable).
        api_base: Backend base URL passed through to ``fetch``.
    """
    fetcher = fetch if fetch is not None else fetch_markdown
    try:
        response = fetcher(url, api_base=api_base)
    except MonitorError as exc:
        return CheckResult(url=url, status=ERROR, detail=str(exc))

    markdown = response.get("markdown", "")
    previous = store.load(url)

    if previous is None:
        store.save(url, markdown)
        return CheckResult(url=url, status=NEW, length=len(markdown))

    if previous.get("content_hash") == _content_hash(markdown):
        # No drift — refresh the baseline's timestamp but the content is stable.
        store.save(url, markdown)
        return CheckResult(
            url=url,
            status=UNCHANGED,
            length=len(markdown),
            previous_length=previous.get("length"),
        )

    diff = _unified_diff(previous.get("markdown", ""), markdown, url)
    store.save(url, markdown)
    return CheckResult(
        url=url,
        status=CHANGED,
        length=len(markdown),
        previous_length=previous.get("length"),
        diff=diff,
    )


# --- Orchestration ----------------------------------------------------------
def run_once(urls: list[str], store: SnapshotStore, *, api_base: str = API_BASE) -> list[CheckResult]:
    """Check every URL once, in order. Deterministic given the same inputs."""
    return [check_url(url, store, api_base=api_base) for url in urls]


def watch(
    urls: list[str],
    store: SnapshotStore,
    *,
    interval: float,
    api_base: str = API_BASE,
    iterations: Optional[int] = None,
    on_result: Optional[Callable[[CheckResult], None]] = None,
    sleep: Callable[[float], None] = time.sleep,
) -> None:
    """Re-check ``urls`` every ``interval`` seconds until interrupted.

    Args:
        interval: Seconds to wait between passes.
        iterations: Stop after this many passes; ``None`` means run forever
            (until ``KeyboardInterrupt``). Bounded runs make the loop testable.
        on_result: Called with each ``CheckResult`` as it is produced.
        sleep: Injection seam for the delay so tests need not actually wait.
    """
    count = 0
    while iterations is None or count < iterations:
        for result in run_once(urls, store, api_base=api_base):
            if on_result is not None:
                on_result(result)
        count += 1
        if iterations is not None and count >= iterations:
            break
        sleep(interval)


# --- CLI --------------------------------------------------------------------
def format_result(result: CheckResult) -> str:
    """Render a check result as a one-line status (plus a diff when changed)."""
    if result.status == NEW:
        return f"[new]       {result.url} — baseline saved ({result.length} chars)"
    if result.status == UNCHANGED:
        return f"[unchanged] {result.url} — {result.length} chars"
    if result.status == CHANGED:
        delta = result.length - (result.previous_length or 0)
        header = (
            f"[changed]   {result.url} — "
            f"{result.previous_length} → {result.length} chars ({delta:+d})"
        )
        return f"{header}\n{result.diff}" if result.diff else header
    return f"[error]     {result.url} — {result.detail}"


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="monitor.py",
        description=(
            "Autonomous ingestion: convert URLs to Markdown on a schedule and "
            "diff each conversion against the last one. A thin HTTP client over "
            "the wiseau backend — it inherits the backend's rate-limit and "
            "concurrency guards."
        ),
    )
    parser.add_argument("urls", nargs="+", help="One or more absolute http(s) URLs to monitor.")
    parser.add_argument(
        "--watch",
        action="store_true",
        help="Keep checking on a schedule instead of running a single pass.",
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=3600.0,
        help="Seconds between checks in --watch mode (default: 3600).",
    )
    parser.add_argument(
        "--store",
        default=DEFAULT_SNAPSHOT_DIR,
        help=f"Directory for snapshots (default: {DEFAULT_SNAPSHOT_DIR!r}).",
    )
    parser.add_argument(
        "--api-base",
        default=API_BASE,
        help=f"Backend base URL (default: {API_BASE!r}, from WISEAU_API_BASE).",
    )
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    """Entry point. Returns 0 on success, 1 if any check errored (drift is not an error)."""
    args = _build_parser().parse_args(argv)
    store = SnapshotStore(args.store)

    def report(result: CheckResult) -> None:
        print(format_result(result))

    if args.watch:
        try:
            watch(
                args.urls,
                store,
                interval=args.interval,
                api_base=args.api_base,
                on_result=report,
            )
        except KeyboardInterrupt:
            print("\nstopped.", file=sys.stderr)
        return 0

    results = run_once(args.urls, store, api_base=args.api_base)
    for result in results:
        report(result)
    # Content changes are expected and exit 0; only a failure to reach/render is an error.
    return 0 if all(result.ok for result in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
