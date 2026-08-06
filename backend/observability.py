"""Structured logging and lightweight in-process metrics.

Two jobs, both side channels — nothing here may change a single byte of the
Markdown the engine returns (invariant #1 still binds the deterministic paths).

**Structured logging.** `configure_logging()` installs a JSON-lines formatter so
every log record is one machine-readable object (`WISEAU_LOG_FORMAT=text` opts
back into the human format for local work). Callers attach structured fields by
passing `extra={"wiseau": {...}}`; those keys are merged into the emitted object
rather than being formatted into the message string.

**Metrics.** `metrics` is a process-local, thread-safe registry serving two needs
recorded in the roadmap's observability backlog:

1. *Sizing.* Job queue-wait and run duration, peak in-flight jobs, and process
   memory are what `MAX_CONCURRENT_JOBS` should be tuned against — guessing is
   how a free-tier container gets OOM-killed.
2. *Seeing the fallback.* When docling is the selected engine it has an automatic
   fallback (ADR-014), which by design turns a docling outage into a *successful*
   response. That is the point, but it also means a dead docling Space looks
   exactly like normal operation. Engine attribution — how many conversions each
   engine served, and why docling was skipped or abandoned — is what makes a
   silent outage visible.

Deliberately **stdlib-only** and in-process: no Prometheus client, no new runtime
dependency, nothing to scrape or run. Counters are per-process and reset when the
container restarts; they are an operational aid, not an audit trail. On a
multi-worker deployment each worker reports its own slice.
"""

from __future__ import annotations

import json
import logging
import os
import resource
import sys
import threading
import time
from collections import deque
from typing import Any, Deque, Dict, Iterable, Optional

# Fields the stdlib puts on every LogRecord. Anything outside this set was added
# by a caller via `extra=`, so it belongs in the structured payload.
_STANDARD_RECORD_FIELDS = frozenset(
    logging.LogRecord("", 0, "", 0, "", None, None).__dict__
) | {"message", "asctime", "taskName"}

# How many recent samples each duration series keeps for percentiles. Bounded so
# a long-running container cannot grow this without limit; running count/total/max
# are tracked separately and cover the whole process lifetime.
_SAMPLE_WINDOW = 512


# --- Structured logging -----------------------------------------------------
class JsonLogFormatter(logging.Formatter):
    """Render a log record as a single JSON object (one line)."""

    def format(self, record: logging.LogRecord) -> str:
        payload: Dict[str, Any] = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(record.created))
            + f".{int(record.msecs):03d}Z",
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }

        # Structured fields: `extra={"wiseau": {...}}` is the documented form,
        # but any other `extra=` key is picked up too rather than being dropped.
        context = record.__dict__.get("wiseau")
        if isinstance(context, dict):
            payload.update(context)
        for key, value in record.__dict__.items():
            if key not in _STANDARD_RECORD_FIELDS and key != "wiseau":
                payload[key] = value

        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)

        return json.dumps(payload, default=str, sort_keys=False)


def log_format() -> str:
    """Selected log format: `json` (default) or `text`."""
    return os.environ.get("WISEAU_LOG_FORMAT", "json").strip().lower()


def configure_logging() -> None:
    """Install the configured formatter on the root logger. Idempotent."""
    level = os.environ.get("WISEAU_LOG_LEVEL", "INFO").strip().upper()
    handler = logging.StreamHandler(sys.stdout)
    if log_format() == "text":
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    else:
        handler.setFormatter(JsonLogFormatter())

    root = logging.getLogger()
    # Replace rather than append: re-running must not double every line.
    for existing in list(root.handlers):
        root.removeHandler(existing)
    root.addHandler(handler)
    root.setLevel(getattr(logging, level, logging.INFO))


# --- Metrics ----------------------------------------------------------------
class _Durations:
    """Running count/total/max plus a bounded window for percentiles."""

    def __init__(self) -> None:
        self.count = 0
        self.total_ms = 0.0
        self.max_ms = 0.0
        self.recent: Deque[float] = deque(maxlen=_SAMPLE_WINDOW)

    def observe(self, duration_ms: float) -> None:
        self.count += 1
        self.total_ms += duration_ms
        self.max_ms = max(self.max_ms, duration_ms)
        self.recent.append(duration_ms)

    def snapshot(self) -> Dict[str, Any]:
        if not self.count:
            return {"count": 0}
        return {
            "count": self.count,
            "mean_ms": round(self.total_ms / self.count, 1),
            "p50_ms": _percentile(self.recent, 50),
            "p95_ms": _percentile(self.recent, 95),
            "max_ms": round(self.max_ms, 1),
        }


def _percentile(samples: Iterable[float], pct: int) -> float:
    """Nearest-rank percentile over the retained window (0.0 when empty)."""
    ordered = sorted(samples)
    if not ordered:
        return 0.0
    rank = max(1, -(-pct * len(ordered) // 100))  # ceil, without float rounding
    return round(ordered[rank - 1], 1)


class Metrics:
    """Thread-safe counters and duration series for one process."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.reset()

    def reset(self) -> None:
        """Clear every series. Used by tests; not exposed over HTTP."""
        with self._lock:
            self._started_at = time.time()
            self._requests: Dict[str, int] = {}
            self._statuses: Dict[str, int] = {}
            self._request_durations: Dict[str, _Durations] = {}
            self._engines: Dict[str, int] = {}
            self._conversions: Dict[str, int] = {}
            self._docling: Dict[str, int] = {}
            self._docling_skips: Dict[str, int] = {}
            self._chapters: Dict[str, int] = {}
            self._chapter_methods: Dict[str, int] = {}
            self._batches: Dict[str, int] = {}
            self._docling_durations = _Durations()
            self._job_wait = _Durations()
            self._job_duration = _Durations()
            self._in_flight = 0
            self._max_in_flight = 0

    # -- recording ----------------------------------------------------------
    def record_request(self, route: str, status: int, duration_ms: float) -> None:
        """One completed HTTP request."""
        with self._lock:
            self._requests[route] = self._requests.get(route, 0) + 1
            key = str(status)
            self._statuses[key] = self._statuses.get(key, 0) + 1
            self._request_durations.setdefault(route, _Durations()).observe(duration_ms)

    def job_started(self, wait_ms: float) -> None:
        """A heavy job acquired its semaphore slot after `wait_ms` queued."""
        with self._lock:
            self._job_wait.observe(wait_ms)
            self._in_flight += 1
            self._max_in_flight = max(self._max_in_flight, self._in_flight)

    def job_finished(self, duration_ms: float) -> None:
        """A heavy job released its slot after `duration_ms` of work."""
        with self._lock:
            self._job_duration.observe(duration_ms)
            self._in_flight = max(0, self._in_flight - 1)

    def record_conversion(self, kind: str, outcome: str) -> None:
        """A conversion finished: `kind` is `url`/`file`, `outcome` ok/error."""
        with self._lock:
            key = f"{kind}.{outcome}"
            self._conversions[key] = self._conversions.get(key, 0) + 1

    def record_engine(self, engine: str) -> None:
        """Attribute one conversion to the engine that actually produced it."""
        with self._lock:
            self._engines[engine] = self._engines.get(engine, 0) + 1

    def record_docling_attempt(self, duration_ms: float, ok: bool, reason: str = "") -> None:
        """One call to docling-serve: `ok` false means the caller fell back."""
        with self._lock:
            self._docling["attempts"] = self._docling.get("attempts", 0) + 1
            self._docling_durations.observe(duration_ms)
            if ok:
                self._docling["successes"] = self._docling.get("successes", 0) + 1
            else:
                self._docling["fallbacks"] = self._docling.get("fallbacks", 0) + 1
                if reason:
                    self._docling_skips[reason] = self._docling_skips.get(reason, 0) + 1

    def record_chapter_split(self, method: str, chapters: int) -> None:
        """One requested chapter split, and which signal produced it (ADR-030).

        Chapter detection is a heuristic, and the request that asks for it looks
        identical whether it found six chapters or nothing at all. Counting the
        methods is how an operator sees which one is actually carrying real
        documents — and whether `none` is the usual answer, which would mean the
        heuristics need work rather than that users stopped asking.
        """
        with self._lock:
            self._chapters["requested"] = self._chapters.get("requested", 0) + 1
            if chapters:
                self._chapters["split"] = self._chapters.get("split", 0) + 1
                self._chapters["sections"] = self._chapters.get("sections", 0) + chapters
            self._chapter_methods[method] = self._chapter_methods.get(method, 0) + 1

    def record_batch(self, files: int, failed: int) -> None:
        """One batch conversion: how many documents it carried, how many failed.

        A batch is the one request whose cost is not visible from the request
        count — thirty documents and one document are both a single `POST
        /convert/batch` in `requests.by_route`. `files` is what actually sizes
        `MAX_BATCH_FILES` and the rate limit, and a `failed` count that climbs is
        the signal that callers are sending something the engine cannot read
        (ADR-031).
        """
        with self._lock:
            self._batches["requested"] = self._batches.get("requested", 0) + 1
            self._batches["files"] = self._batches.get("files", 0) + files
            self._batches["failed"] = self._batches.get("failed", 0) + failed
            self._batches["largest"] = max(self._batches.get("largest", 0), files)

    def record_docling_skipped(self, reason: str) -> None:
        """docling was never called (not selected, or no base configured)."""
        with self._lock:
            self._docling["skipped"] = self._docling.get("skipped", 0) + 1
            self._docling_skips[reason] = self._docling_skips.get(reason, 0) + 1

    # -- reporting ----------------------------------------------------------
    def snapshot(self) -> Dict[str, Any]:
        """A JSON-serializable view of every series. Keys sorted for stability."""
        with self._lock:
            return {
                "uptime_seconds": round(time.time() - self._started_at, 1),
                "requests": {
                    "by_route": dict(sorted(self._requests.items())),
                    "by_status": dict(sorted(self._statuses.items())),
                    "duration": {
                        route: series.snapshot()
                        for route, series in sorted(self._request_durations.items())
                    },
                },
                "jobs": {
                    "in_flight": self._in_flight,
                    "max_in_flight": self._max_in_flight,
                    "queue_wait": self._job_wait.snapshot(),
                    "duration": self._job_duration.snapshot(),
                },
                "conversions": dict(sorted(self._conversions.items())),
                "engines": dict(sorted(self._engines.items())),
                "docling": {
                    **{k: self._docling.get(k, 0) for k in ("attempts", "successes", "fallbacks", "skipped")},
                    "reasons": dict(sorted(self._docling_skips.items())),
                    "duration": self._docling_durations.snapshot(),
                },
                "chapters": {
                    **{k: self._chapters.get(k, 0) for k in ("requested", "split", "sections")},
                    "by_method": dict(sorted(self._chapter_methods.items())),
                },
                "batches": {
                    k: self._batches.get(k, 0) for k in ("requested", "files", "failed", "largest")
                },
                "memory": _memory(),
            }


def _memory() -> Dict[str, Any]:
    """Process memory, best-effort — the number `MAX_CONCURRENT_JOBS` is sized on.

    Peak RSS comes from `getrusage` (portable); current RSS is read from
    `/proc/self/statm`, which only Linux has. A platform without it simply omits
    the field rather than failing the metrics response.
    """
    peak_kb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    # ru_maxrss is kilobytes on Linux (where this deploys) and bytes on macOS.
    peak_mb = peak_kb / 1024 if sys.platform != "darwin" else peak_kb / (1024 * 1024)
    memory: Dict[str, Any] = {"peak_rss_mb": round(peak_mb, 1)}

    current = _current_rss_mb()
    if current is not None:
        memory["rss_mb"] = current
    return memory


def _current_rss_mb() -> Optional[float]:
    try:
        with open("/proc/self/statm", "r", encoding="ascii") as handle:
            pages = int(handle.read().split()[1])
    except (OSError, IndexError, ValueError):
        return None
    return round(pages * os.sysconf("SC_PAGE_SIZE") / (1024 * 1024), 1)


# The process-wide registry. Imported directly by `main.py` and the parsers so
# recording a metric never needs plumbing through call signatures.
metrics = Metrics()
