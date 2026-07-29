"""Universal Markdown Ingestion Engine — FastAPI entrypoint.

Exposes a small, public HTTP surface that converts web URLs, PDFs, and DOCX
documents into clean, deterministic Markdown. The API is intentionally open
(any origin may call it) but guarded by per-IP rate limiting and a global
concurrency ceiling so the shared free compute stays healthy for everyone.

Every request is timed and logged as one structured line, and the counters behind
`GET /metrics` are recorded here and in the parsers (see `observability.py` /
ADR-019). That is a pure side channel: it must never change what is extracted.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
import uuid
from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, HttpUrl
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from slowapi.util import get_remote_address

from observability import configure_logging, metrics
from parsers import (
    REQUESTABLE_ENGINES,
    BlockedUrlError,
    file_to_markdown,
    resolve_engine,
    url_to_markdown,
)

configure_logging()
logger = logging.getLogger("markdown_engine")

# --- Fair-use guards --------------------------------------------------------
# Per-IP rate limits (see individual routes for per-endpoint overrides).
#
# The `default_limits` here only bind routes that carry no `@limiter.limit`
# decorator, and *only* because `SlowAPIMiddleware` is installed below: slowapi
# applies decorator limits from the decorator itself, but the defaults are
# enforced by the middleware alone. Without it the defaults silently apply to
# nothing and `@limiter.exempt` becomes a no-op (invariant #4).
limiter = Limiter(key_func=get_remote_address, default_limits=["60/minute", "1000/day"])

# Global concurrency ceiling: headless-browser renders and PDF extraction are
# memory-heavy, so cap how many run at once rather than letting them stampede
# the single free-tier container into an out-of-memory crash. Overflow requests
# queue on the semaphore instead of failing.
MAX_CONCURRENT_JOBS = int(os.environ.get("MAX_CONCURRENT_JOBS", "4"))
_job_semaphore = asyncio.Semaphore(MAX_CONCURRENT_JOBS)

# Upload size ceiling (bytes). Enforced by `_read_upload` while streaming, so an
# oversized body is rejected without ever being assembled in memory.
MAX_UPLOAD_BYTES = int(os.environ.get("MAX_UPLOAD_BYTES", str(25 * 1024 * 1024)))

# How much of an upload to pull per `read()`. Bounds the overshoot past
# MAX_UPLOAD_BYTES to one chunk before the 413 is raised.
_UPLOAD_CHUNK_BYTES = 256 * 1024

app = FastAPI(
    title="Universal Markdown Ingestion Engine",
    description=(
        "Deterministic conversion of web URLs, PDFs, and DOCX documents into "
        "clean, structured Markdown. Designed for both human UIs and LLM/MCP agents."
    ),
    version="0.6.0",
)

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# Applies `limiter`'s default limits to every route that has no explicit
# `@limiter.limit` (i.e. `/metrics`), and honours `@limiter.exempt` (`/ping`).
# Added *before* CORS so the CORS middleware wraps it and a middleware-issued 429
# still carries the headers a browser client needs to read it.
app.add_middleware(SlowAPIMiddleware)

# Open but protected: any browser origin may call the public API. Access is
# controlled by rate limiting, not origin locks.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
    # So a browser client can read the correlation id off its own response.
    expose_headers=["X-Request-ID"],
)


# --- Observability ----------------------------------------------------------
@app.middleware("http")
async def observe_requests(request: Request, call_next):
    """Time every request, emit one structured access log line, count it.

    Registered last, so it wraps CORS and therefore sees the response actually
    sent — including preflights, rate-limit 429s, and validation errors.
    """
    request_id = uuid.uuid4().hex[:12]
    request.state.request_id = request_id
    started = time.perf_counter()
    try:
        response = await call_next(request)
    except Exception:
        duration_ms = (time.perf_counter() - started) * 1000
        metrics.record_request(_route_label(request), 500, duration_ms)
        logger.exception(
            "request failed",
            extra={
                "wiseau": {
                    "request_id": request_id,
                    "method": request.method,
                    "path": request.url.path,
                    "duration_ms": round(duration_ms, 1),
                }
            },
        )
        raise

    duration_ms = (time.perf_counter() - started) * 1000
    metrics.record_request(_route_label(request), response.status_code, duration_ms)
    response.headers["X-Request-ID"] = request_id
    logger.info(
        "request",
        extra={
            "wiseau": {
                "request_id": request_id,
                "method": request.method,
                "path": request.url.path,
                "status": response.status_code,
                "duration_ms": round(duration_ms, 1),
                "client": get_remote_address(request),
            }
        },
    )
    return response


def _registered_paths() -> frozenset:
    """Every route path this app serves — the only labels `/metrics` may report."""
    return frozenset(
        path for path in (getattr(route, "path", None) for route in app.routes) if path
    )


def _route_label(request: Request) -> str:
    """The matched route's *template* — never an arbitrary caller-supplied path.

    Counting raw paths would let any caller inflate the metrics registry with
    unbounded keys, so unmatched requests all collapse into one bucket. Normally
    the router has already resolved the route, but a request rejected by a
    middleware (a rate-limit 429) never reaches the router; for those the path is
    accepted only when it is one this app actually registers, which keeps the key
    space bounded while still attributing the rejection to its real route.
    """
    route = request.scope.get("route")
    label = getattr(route, "path", None)
    if label:
        return label
    path = request.url.path
    return path if path in _registered_paths() else "unmatched"


@asynccontextmanager
async def _job_slot() -> AsyncIterator[None]:
    """Hold a slot in the concurrency ceiling, timing the queue wait and the work.

    Wraps `_job_semaphore` rather than replacing it: the guard is unchanged
    (invariant #4), the measurements are what tune `MAX_CONCURRENT_JOBS`.
    """
    queued_at = time.perf_counter()
    async with _job_semaphore:
        metrics.job_started((time.perf_counter() - queued_at) * 1000)
        started = time.perf_counter()
        try:
            yield
        finally:
            metrics.job_finished((time.perf_counter() - started) * 1000)


# --- Schemas ----------------------------------------------------------------
# Documented once, shared by both request shapes (JSON body and multipart form).
_ENGINE_DESCRIPTION = (
    "Document engine to use: 'docling' (highest fidelity, markedly slower on the "
    "free CPU tier), 'pymupdf' (fast and deterministic), or 'auto' — the default "
    "— for this deployment's own preference. docling always falls back to the "
    "deterministic parser when it is unavailable, whether it was chosen or "
    "defaulted to. On /convert/url the choice applies only when the URL serves a "
    "PDF; HTML pages are always extracted by Trafilatura."
)


class UrlRequest(BaseModel):
    url: HttpUrl
    engine: str | None = Field(default=None, description=_ENGINE_DESCRIPTION)


class MarkdownResponse(BaseModel):
    source: str
    markdown: str
    length: int


def _resolve_engine_or_400(requested: str | None) -> str | None:
    """Validate a requested engine, mapping an unknown name to a 400.

    Naming an engine this build cannot run is a caller mistake, not a conversion
    failure — it must not read as a 502 (nor silently convert with something the
    caller did not ask for). `None`/`auto` means "this deployment's default".
    """
    try:
        return resolve_engine(requested)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


# --- Upload handling --------------------------------------------------------
async def _read_upload(file: UploadFile) -> bytes:
    """Read an upload, refusing to assemble more than `MAX_UPLOAD_BYTES` in memory.

    Reading the whole part first and *then* checking its size would materialize
    an arbitrarily large body in the container's RAM before rejecting it — the
    opposite of what a memory-sized free-tier box wants. Streaming in chunks
    bounds that to the limit plus one chunk.

    Raises:
        HTTPException: 413 as soon as the part exceeds `MAX_UPLOAD_BYTES`.
    """
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await file.read(_UPLOAD_CHUNK_BYTES)
        if not chunk:
            break
        total += len(chunk)
        if total > MAX_UPLOAD_BYTES:
            raise HTTPException(
                status_code=413,
                detail=f"File exceeds the {MAX_UPLOAD_BYTES // (1024 * 1024)} MB limit.",
            )
        chunks.append(chunk)
    return b"".join(chunks)


# --- Routes -----------------------------------------------------------------
@app.get(
    "/ping",
    tags=["status"],
    operation_id="ping",
    summary="Liveness/readiness probe",
)
@limiter.exempt
async def ping(request: Request) -> dict:
    """Liveness/readiness check for the UI status badge and background monitors.

    Also advertises the engines this build accepts on a convert request, so a
    client can offer the choice without hard-coding the list.
    """
    return {
        "status": "ok",
        "service": "markdown-ingestion-engine",
        "version": app.version,
        "engines": sorted(REQUESTABLE_ENGINES),
    }


@app.get(
    "/metrics",
    tags=["status"],
    operation_id="metrics",
    summary="Operational counters for this process",
)
async def read_metrics(request: Request) -> dict:
    """Per-process counters: request/job timings, memory, and engine attribution.

    Exists to answer two operational questions that the API contract cannot:
    what to set `MAX_CONCURRENT_JOBS` to, and whether docling is actually
    serving conversions or silently falling back (ADR-014 makes an outage look
    like success). Aggregate numbers only — no URLs, filenames, or content — and
    they reset with the process. Rate-limited like any other route; it does no
    heavy work, so it takes no job slot.
    """
    return metrics.snapshot()


@app.post(
    "/convert/url",
    response_model=MarkdownResponse,
    tags=["convert"],
    operation_id="convert_url",
    summary="Convert a web page to Markdown",
)
@limiter.limit("20/minute")
async def convert_url(request: Request, body: UrlRequest) -> MarkdownResponse:
    """Render a URL (JS-aware) and extract its primary content as Markdown."""
    url = str(body.url)
    engine = _resolve_engine_or_400(body.engine)
    async with _job_slot():
        try:
            markdown = await asyncio.to_thread(url_to_markdown, url, engine)
        except BlockedUrlError as exc:
            # The caller asked for a host this deployment refuses to fetch. That
            # is a bad request, not a failed render, so it must not read as a 502.
            metrics.record_conversion("url", "error")
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:  # noqa: BLE001 - surface a clean 502 to the caller
            metrics.record_conversion("url", "error")
            logger.exception("URL conversion failed for %s", url)
            raise HTTPException(status_code=502, detail=f"Failed to convert URL: {exc}") from exc
    metrics.record_conversion("url", "ok")
    return MarkdownResponse(source=url, markdown=markdown, length=len(markdown))


@app.post(
    "/convert/file",
    response_model=MarkdownResponse,
    tags=["convert"],
    operation_id="convert_file",
    summary="Convert an uploaded PDF or DOCX to Markdown",
)
@limiter.limit("20/minute")
async def convert_file(
    request: Request,
    file: UploadFile = File(...),
    engine: str | None = Form(default=None, description=_ENGINE_DESCRIPTION),
) -> MarkdownResponse:
    """Parse an uploaded PDF or DOCX into Markdown."""
    resolved_engine = _resolve_engine_or_400(engine)
    data = await _read_upload(file)
    if not data:
        raise HTTPException(status_code=400, detail="Empty file upload.")

    async with _job_slot():
        try:
            markdown = await asyncio.to_thread(
                file_to_markdown, data, file.filename or "", resolved_engine
            )
        except ValueError as exc:
            metrics.record_conversion("file", "error")
            raise HTTPException(status_code=415, detail=str(exc)) from exc
        except Exception as exc:  # noqa: BLE001
            metrics.record_conversion("file", "error")
            logger.exception("File conversion failed for %s", file.filename)
            raise HTTPException(status_code=502, detail=f"Failed to convert file: {exc}") from exc
    metrics.record_conversion("file", "ok")
    return MarkdownResponse(source=file.filename or "upload", markdown=markdown, length=len(markdown))


if __name__ == "__main__":
    import uvicorn

    # Access logging is off because `observe_requests` already emits one
    # structured line per request; uvicorn's would only duplicate it.
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=int(os.environ.get("PORT", "7860")),
        access_log=False,
    )
