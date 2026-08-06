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
    default_engine,
    file_to_markdown,
    resolve_engine,
    split_into_chapters,
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


# --- MCP surface (ADR-029) --------------------------------------------------
# The agent-facing MCP endpoint is served by *this* app rather than a second
# process, so one free-tier container gives an LLM a connector URL
# (`https://…/mcp`) and a human the REST API. The tools still reach the engine
# over HTTP — here, the loopback interface — so they keep inheriting the rate
# limits and the concurrency ceiling (invariant #4; see `docs/mcp.md`).
def _mcp_mount_enabled() -> bool:
    """Whether to serve the MCP endpoint from this app. On unless switched off."""
    return os.environ.get("WISEAU_MCP_MOUNT", "1").strip().lower() not in {"0", "false", "no"}


def _load_mcp_routes() -> list:
    """The MCP endpoint's routes, or an empty list if it is off or unavailable.

    The MCP SDK lives in `requirements-mcp.txt`, not the base requirements, so a
    checkout that installed only the latter must still start — it simply serves
    no MCP endpoint. A missing optional dependency is a log line, not a crash.
    """
    if not _mcp_mount_enabled():
        return []
    try:
        import mcp_server
    except ImportError as exc:
        logger.warning(
            "MCP endpoint not served: %s. Install requirements-mcp.txt to enable it, "
            "or set WISEAU_MCP_MOUNT=0 to silence this.",
            exc,
        )
        return []
    routes = mcp_server.hosted_routes()
    for route in routes:
        _name_endpoint_for_limiter(route)
    logger.info("MCP endpoint served at %s (backend %s)", mcp_server.mcp_path(), mcp_server.API_BASE)
    return routes


def _name_endpoint_for_limiter(route) -> None:
    """Give an ASGI-object endpoint a `__name__` so `SlowAPIMiddleware` can bind it.

    slowapi identifies a route by `endpoint.__module__ + "." + endpoint.__name__`
    to decide whether the default limits apply. Every FastAPI route's endpoint is
    a function and has one; the MCP endpoint is an ASGI *object*, so the
    middleware raises `AttributeError` before it checks any limit — turning every
    MCP request into a 500. Naming it puts the endpoint back under the same
    default per-IP limit as any other undecorated route (invariant #4).
    """
    endpoint = getattr(route, "endpoint", None)
    if endpoint is not None and not hasattr(endpoint, "__name__"):
        endpoint.__name__ = "mcp_streamable_http"


_mcp_routes = _load_mcp_routes()
# The path a connector should be pointed at, or None when no MCP endpoint is
# served. Published by `/ping` so a client discovers it rather than guessing —
# `WISEAU_MCP_PATH` may have moved it (ADR-029).
MCP_ENDPOINT: str | None = _mcp_routes[0].path if _mcp_routes else None


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Run the MCP session manager alongside the app, when the endpoint is served.

    Grafting the routes is not enough: the streamable-HTTP transport needs its
    session manager running for the lifetime of the process, and a sub-app's own
    lifespan does not run when its routes are hosted by another app.
    """
    if not _mcp_routes:
        yield
        return
    import mcp_server

    async with mcp_server.session_manager().run():
        yield


app = FastAPI(
    title="Universal Markdown Ingestion Engine",
    description=(
        "Deterministic conversion of web URLs, PDFs, and DOCX documents into "
        "clean, structured Markdown. Designed for both human UIs and LLM/MCP agents."
    ),
    version="0.9.0",
    lifespan=_lifespan,
)

# Grafted rather than `mount()`ed: a mounted sub-app only matches `/mcp/…`, so
# the exact `/mcp` a connector is given would answer with a redirect. These are
# plain Starlette routes, so they stay out of the OpenAPI schema — which
# describes the REST contract, and would only confuse a function-calling client
# by advertising a JSON-RPC endpoint alongside it.
app.router.routes.extend(_mcp_routes)

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
    # DELETE is the MCP transport's session-termination verb; a browser-based
    # MCP client cannot end a session cleanly without it.
    allow_methods=["GET", "POST", "DELETE"],
    allow_headers=["*"],
    # So a browser client can read the correlation id off its own response —
    # and, for an MCP client, the session id the transport assigns it.
    expose_headers=["X-Request-ID", "Mcp-Session-Id"],
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
    "Document engine to use: 'pymupdf' (fast and deterministic — what a standard "
    "deployment defaults to), 'docling' (highest fidelity on complex or scanned "
    "documents, but markedly slower on the free CPU tier), or 'auto' — the "
    "default — for this deployment's own preference. docling always falls back to "
    "the deterministic parser when it is unavailable, whether it was chosen or "
    "defaulted to. On /convert/url the choice applies only when the URL serves a "
    "PDF; HTML pages are always extracted by Trafilatura."
)


_SPLIT_DESCRIPTION = (
    "Also return the document split into chapters, for saving each one as its own "
    "file (ADR-030). Chapters are found from the document's own contents page "
    "where it has one, otherwise from its heading structure. Off by default: it "
    "roughly doubles the response size, and most documents have no chapters. When "
    "no chapter structure is found the document is returned whole and "
    "`chapter_detection` is 'none'."
)


class UrlRequest(BaseModel):
    url: HttpUrl
    engine: str | None = Field(default=None, description=_ENGINE_DESCRIPTION)
    split_chapters: bool = Field(default=False, description=_SPLIT_DESCRIPTION)


class ChapterModel(BaseModel):
    """One chapter of a split document — the shape a caller writes to a file."""

    title: str = Field(description="The chapter's title, as the document gives it.")
    level: int = Field(
        description="Heading depth of the chapter's opening (1-6); 0 for the "
        "material that precedes the first chapter, such as a title or contents page."
    )
    filename: str = Field(
        description="Suggested filename, numbered so the chapters sort in reading "
        "order (e.g. '03-the-reckoning.md')."
    )
    markdown: str
    length: int


class MarkdownResponse(BaseModel):
    source: str
    markdown: str
    length: int
    # Both are null unless `split_chapters` was requested, so an existing client
    # sees exactly the response it saw before (ADR-003: one contract, not a fork).
    chapters: list[ChapterModel] | None = Field(
        default=None,
        description="The document as an ordered list of chapters. Concatenating "
        "them reproduces `markdown`; nothing is dropped or duplicated. Null when "
        "the split was not requested, empty when nothing chapter-like was found.",
    )
    chapter_detection: str | None = Field(
        default=None,
        description="Which signal produced the chapters: 'toc' (the document's "
        "contents page), 'headings', 'markers' (plain-text 'Chapter N' lines), "
        "'none' (no chapter structure found), or 'error' (the split failed; the "
        "conversion itself succeeded). Null when the split was not requested.",
    )


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


async def _split_or_none(markdown: str, requested: bool) -> tuple[list[ChapterModel] | None, str | None]:
    """Split converted Markdown into chapters, when the caller asked for it.

    Runs off the event loop: the split is pure text work, but a book-sized
    document is a lot of text and the loop has other requests to serve. Called
    from inside the job slot, so it queues behind the same concurrency ceiling as
    the conversion itself (invariant #4). Returns `(None, None)` when the split
    was not requested, so the response is byte-for-byte what it has always been
    for callers that know nothing about chapters.

    A failure here does **not** fail the conversion. The document converted
    successfully — often after a minute of docling — and chapter detection is an
    extra on top of it, so a bug in the heuristics reports itself
    (`chapter_detection: "error"`, plus a logged traceback and a counted method)
    rather than throwing away a result the caller can use.
    """
    if not requested:
        return None, None
    try:
        split = await asyncio.to_thread(split_into_chapters, markdown)
    except Exception:  # noqa: BLE001 - never lose a good conversion to this
        metrics.record_chapter_split("error", 0)
        logger.exception("chapter splitting failed; returning the document whole")
        return [], "error"
    metrics.record_chapter_split(split.method, len(split.chapters))
    chapters = [
        ChapterModel(
            title=chapter.title,
            level=chapter.level,
            filename=chapter.filename,
            markdown=chapter.markdown,
            length=chapter.length,
        )
        for chapter in split.chapters
    ]
    return chapters, split.method


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

    Also advertises the engines this build accepts on a convert request, and
    which of them `auto` resolves to here, so a client can offer the choice —
    and estimate what it will cost — without hard-coding either (ADR-027) — plus
    the path of the MCP endpoint, if this deployment serves one (ADR-029).
    """
    return {
        "status": "ok",
        "service": "markdown-ingestion-engine",
        "version": app.version,
        "engines": sorted(REQUESTABLE_ENGINES),
        "default_engine": default_engine(),
        "mcp_endpoint": MCP_ENDPOINT,
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
        chapters, detection = await _split_or_none(markdown, body.split_chapters)
    metrics.record_conversion("url", "ok")
    return MarkdownResponse(
        source=url,
        markdown=markdown,
        length=len(markdown),
        chapters=chapters,
        chapter_detection=detection,
    )


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
    split_chapters: bool = Form(default=False, description=_SPLIT_DESCRIPTION),
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
        chapters, detection = await _split_or_none(markdown, split_chapters)
    metrics.record_conversion("file", "ok")
    return MarkdownResponse(
        source=file.filename or "upload",
        markdown=markdown,
        length=len(markdown),
        chapters=chapters,
        chapter_detection=detection,
    )


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
