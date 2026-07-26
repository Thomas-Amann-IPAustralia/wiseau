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

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, HttpUrl
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

from observability import configure_logging, metrics
from parsers import file_to_markdown, url_to_markdown

configure_logging()
logger = logging.getLogger("markdown_engine")

# --- Fair-use guards --------------------------------------------------------
# Per-IP rate limits (see individual routes for per-endpoint overrides).
limiter = Limiter(key_func=get_remote_address, default_limits=["60/minute", "1000/day"])

# Global concurrency ceiling: headless-browser renders and PDF extraction are
# memory-heavy, so cap how many run at once rather than letting them stampede
# the single free-tier container into an out-of-memory crash. Overflow requests
# queue on the semaphore instead of failing.
MAX_CONCURRENT_JOBS = int(os.environ.get("MAX_CONCURRENT_JOBS", "4"))
_job_semaphore = asyncio.Semaphore(MAX_CONCURRENT_JOBS)

# Reject obviously oversized uploads before buffering them (bytes).
MAX_UPLOAD_BYTES = int(os.environ.get("MAX_UPLOAD_BYTES", str(25 * 1024 * 1024)))

app = FastAPI(
    title="Universal Markdown Ingestion Engine",
    description=(
        "Deterministic conversion of web URLs, PDFs, and DOCX documents into "
        "clean, structured Markdown. Designed for both human UIs and LLM/MCP agents."
    ),
    version="0.4.0",
)

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

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


def _route_label(request: Request) -> str:
    """The matched route's *template* — never the raw path.

    Counting raw paths would let any caller inflate the metrics registry with
    unbounded keys, so unmatched requests all collapse into one bucket.
    """
    route = request.scope.get("route")
    return getattr(route, "path", None) or "unmatched"


@asynccontextmanager
async def _job_slot(kind: str) -> AsyncIterator[None]:
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
class UrlRequest(BaseModel):
    url: HttpUrl


class MarkdownResponse(BaseModel):
    source: str
    markdown: str
    length: int


# --- Routes -----------------------------------------------------------------
@app.get(
    "/ping",
    tags=["status"],
    operation_id="ping",
    summary="Liveness/readiness probe",
)
@limiter.exempt
async def ping(request: Request) -> dict:
    """Liveness/readiness check for the UI status badge and background monitors."""
    return {"status": "ok", "service": "markdown-ingestion-engine", "version": app.version}


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
    async with _job_slot("url"):
        try:
            markdown = await asyncio.to_thread(url_to_markdown, url)
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
async def convert_file(request: Request, file: UploadFile = File(...)) -> MarkdownResponse:
    """Parse an uploaded PDF or DOCX into Markdown."""
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="Empty file upload.")
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"File exceeds the {MAX_UPLOAD_BYTES // (1024 * 1024)} MB limit.",
        )

    async with _job_slot("file"):
        try:
            markdown = await asyncio.to_thread(file_to_markdown, data, file.filename or "")
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
