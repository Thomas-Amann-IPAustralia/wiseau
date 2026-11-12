"""Progress reports from inside a conversion (ADR-058) — a pure side channel.

A conversion is one blocking call in a worker thread, so the streaming routes
(`POST /convert/file/stream` and friends) need some way to hear "page 7 of 36"
from deep inside the parsers without every function growing a callback
parameter. A `ContextVar` carries the listener: the route sets it in the task
that awaits `asyncio.to_thread`, which copies the context into the worker
thread, so `report()` calls there reach it.

`report()` must never change what is extracted: with no listener set (every
non-streaming route, the MCP tools, the tests) it does nothing, and a listener
that raises is logged and ignored. Events are plain dicts with an `event` key:

- `{"event": "stage", "stage": "inspect" | "text_layer" | "ocr" | "mammoth" |
  "docling" | "glm-ocr" | "render" | "extract" | "structure" | "normalize" |
  "chapters", ...}` — a stage started; some carry `pages`.
- `{"event": "page", "page": n, "pages": N, "method": "ocr" | "text",
  "ocr_done": k, "ocr_pages": M}` — one page finished.
- `{"event": "fallback", "engine": name, "reason": text}` — a remote engine
  failed and the deterministic parser is taking over (ADR-014).

Pages are reported only from the conversion's own thread, in page order; work
that ADR-056 overlaps on helper threads reports nothing of its own.
"""

from __future__ import annotations

import logging
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Callable, Iterator

logger = logging.getLogger(__name__)

Listener = Callable[[dict[str, Any]], None]

_listener: ContextVar[Listener | None] = ContextVar("wiseau_progress_listener", default=None)


def report(event: str, **fields: Any) -> None:
    """Tell whoever is listening that something happened. A no-op otherwise."""
    listener = _listener.get()
    if listener is None:
        return
    try:
        listener({"event": event, **fields})
    except Exception:  # noqa: BLE001 - progress is an extra; the conversion must go on
        logger.debug("progress listener failed", exc_info=True)


def active() -> bool:
    """Whether a listener is set (lets a caller skip building an expensive event)."""
    return _listener.get() is not None


@contextmanager
def listening(listener: Listener) -> Iterator[None]:
    """Route `report()` calls made in this context (and threads it starts) to `listener`."""
    token = _listener.set(listener)
    try:
        yield
    finally:
        _listener.reset(token)
