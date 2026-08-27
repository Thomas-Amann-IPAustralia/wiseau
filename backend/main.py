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
    available_methods,
    default_engine,
    default_methods,
    extract_keywords,
    file_to_markdown,
    prepend_keyword_table,
    resolve_engine,
    resolve_methods,
    split_into_chapters,
    unique_filenames,
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

# Batch ceilings (ADR-031). A batch is N conversions behind one rate-limit
# token, so it needs its own bounds or it becomes the way around the fair-use
# guards. Files are read and converted one at a time, so the memory cost is one
# document; these caps bound the *time* one caller can occupy the queue.
MAX_BATCH_FILES = int(os.environ.get("MAX_BATCH_FILES", "20"))
MAX_BATCH_BYTES = int(os.environ.get("MAX_BATCH_BYTES", str(50 * 1024 * 1024)))

# Longest document `POST /keywords` will analyse, in characters (ADR-032). The
# body is Markdown in JSON rather than an upload, so `_read_upload`'s streaming
# guard cannot bound it; this is the equivalent ceiling for the one route whose
# payload is text the caller already holds.
MAX_KEYWORD_CHARS = int(os.environ.get("MAX_KEYWORD_CHARS", str(2_000_000)))


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
    version="0.11.0",
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


_BATCH_FILES_DESCRIPTION = (
    "The documents to convert — repeat the `files` part once per document. Each "
    "is converted independently and the results come back in the order they were "
    "sent, so one unreadable document cannot cost the caller the rest of the batch."
)


_BATCH_SPLIT_DESCRIPTION = (
    "Not available on a batch: sending it as true is a 400. Bulk conversion and "
    "chapter splitting are mutually exclusive for now (ADR-031) — convert a "
    "document on its own via POST /convert/file to split it into chapters."
)


_METHODS_DESCRIPTION = (
    "Which keyword methods to run, from 'frequency' (built-in, always "
    "available), 'yake' (statistical), 'spacy' (noun chunks and named "
    "entities), and 'keybert' (semantic — accurate but slow, and heavy to "
    "install). Omit, or send ['auto'], for this deployment's default; send "
    "['all'] for every method it has. A method this build has never heard of is "
    "a 400; one it knows but cannot run is reported in `methods_skipped` and the "
    "rest still answer."
)


_PREPEND_DESCRIPTION = (
    "Also return the document with the keywords rendered as a Markdown table at "
    "the top, in `markdown`. Off by default — the ranked list in `keywords` is "
    "the machine-readable answer, and a caller that only wants a JSON sidecar "
    "should not pay to have the whole document sent back."
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


class KeywordRequest(BaseModel):
    """Ask for the keywords of Markdown the caller already has (ADR-032).

    Deliberately takes the *document*, not a URL or an upload: keyword
    extraction is a second look at a conversion that already happened, so it
    must not re-render a page or re-run a minute of docling to answer.
    """

    markdown: str = Field(description="The document to analyse, as the engine returned it.")
    source: str | None = Field(
        default=None,
        description="What the document is, echoed back in `source` — a filename "
        "or URL. Labelling only; it is never fetched.",
    )
    methods: list[str] | None = Field(default=None, description=_METHODS_DESCRIPTION)
    top_k: int = Field(default=20, ge=1, le=100, description="How many keywords to return.")
    language: str | None = Field(
        default=None,
        description="Language code for the language-aware methods (e.g. 'en', "
        "'de', 'pt'). Defaults to the deployment's WISEAU_KEYWORD_LANG.",
    )
    prepend_table: bool = Field(default=False, description=_PREPEND_DESCRIPTION)


class KeywordMethodScore(BaseModel):
    """Where one method placed a keyword, and what it scored it natively."""

    rank: int = Field(description="1-based position in that method's own ranking.")
    score: float = Field(
        description="That method's own score, on its own scale — a YAKE cost "
        "(lower is better), a cosine similarity, a weighted occurrence count. "
        "Reported for transparency; the ranking is fused from `rank`, not from these."
    )


class KeywordModel(BaseModel):
    """One extracted keyword and the evidence behind it."""

    term: str = Field(description="The keyword, in the form the document uses.")
    score: float = Field(
        description="Relative weight, 0-1, where the top-ranked keyword is 1.0. "
        "Fused across methods by rank (Reciprocal Rank Fusion), because the "
        "methods' own scores are on incomparable scales."
    )
    rank: int = Field(description="1-based position in the fused ranking.")
    kind: str = Field(description="'entity' when a method recognized it as a named entity, else 'phrase'.")
    occurrences: int = Field(description="How often the term occurs in the document.")
    agreement: int = Field(
        description="How many of the methods that ran found this term — the "
        "confidence signal. A term all four agree on is a different thing from "
        "one a single method liked."
    )
    methods: dict[str, KeywordMethodScore] = Field(
        description="Per-method evidence, keyed by method name."
    )


class KeywordResponse(BaseModel):
    """The ranked keywords of one document, and how they were arrived at."""

    source: str
    keyword_count: int
    keywords: list[KeywordModel]
    methods_used: list[str] = Field(description="The methods that actually ran, in canonical order.")
    methods_skipped: dict[str, str] = Field(
        description="Requested methods that did not run, and why — a missing "
        "optional package, or a method that raised. The request still succeeds: "
        "a degraded answer beats no answer (ADR-014's rule)."
    )
    language: str
    note: str | None = Field(
        default=None,
        description="Why the answer looks the way it does when that is not "
        "obvious from the list: a document too short to characterise, one "
        "truncated at the analysis ceiling, or nothing that ranked. Null otherwise.",
    )
    markdown: str | None = Field(
        default=None,
        description="The document with the keyword table prepended. Null unless "
        "`prepend_table` was requested, so a caller that did not ask is sent "
        "exactly the response it saw before.",
    )


class BatchItem(MarkdownResponse):
    """One document's result inside a batch (ADR-031).

    A successful item *is* a `MarkdownResponse` — same fields, same meanings —
    plus the `status` and the `filename` to save it under. A failed item carries
    the reason in `error` and, deliberately, **no `filename`**: "write every item
    that has a filename" is then the whole of a correct save loop, and an empty
    file can never be written in place of a document that did not convert.
    """

    status: str = Field(description="'ok' or 'error' for this document alone.")
    filename: str | None = Field(
        default=None,
        description="Suggested filename, taken from the uploaded name and made "
        "unique within the batch ('annual-report.md'). Null when the document "
        "failed, because there is nothing to save.",
    )
    error: str | None = Field(
        default=None,
        description="Why this document failed — the same message the equivalent "
        "single-file request would have returned. Null on success.",
    )


class BatchResponse(BaseModel):
    """The result of converting several documents in one request.

    A batch is a list of independent conversions, not one big conversion, so the
    response is always 200 when the *request* was valid: per-document failures
    live in `results` rather than replacing the whole answer with an error the
    caller cannot act on selectively (invariant: resilience over strictness).
    """

    count: int = Field(description="How many documents were submitted.")
    succeeded: int
    failed: int
    results: list[BatchItem] = Field(
        description="One entry per submitted document, in the order they were sent."
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


def _resolve_methods_or_400(requested: list[str] | None) -> list[str] | None:
    """Validate a requested method set, mapping an unknown name to a 400.

    The same split `_resolve_engine_or_400` makes: a name this build has never
    heard of is a caller mistake and must not read as a failed extraction. A
    name it *knows* but cannot run is not an error at all — it is skipped and
    said so, because the alternative is refusing a request three other methods
    could have answered.
    """
    try:
        return resolve_methods(requested)
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


def _batch_failure(source: str, message: str) -> BatchItem:
    """One document of a batch that could not be converted.

    `markdown` is empty and `filename` stays null: a client looping over the
    results to write files must find nothing to write here, rather than an empty
    document under a plausible name.
    """
    return BatchItem(status="error", source=source, markdown="", length=0, error=message)


# --- Upload handling --------------------------------------------------------
async def _read_upload(file: UploadFile, ceiling: int | None = None) -> bytes:
    """Read an upload, refusing to assemble more than the ceiling in memory.

    Reading the whole part first and *then* checking its size would materialize
    an arbitrarily large body in the container's RAM before rejecting it — the
    opposite of what a memory-sized free-tier box wants. Streaming in chunks
    bounds that to the limit plus one chunk.

    Args:
        file: The multipart part to read.
        ceiling: Bytes to allow, defaulting to `MAX_UPLOAD_BYTES`. A batch passes
            what is left of its own budget, so the same streaming guard bounds
            both the single file and the batch as a whole.

    Raises:
        HTTPException: 413 as soon as the part exceeds the ceiling.
    """
    limit = MAX_UPLOAD_BYTES if ceiling is None else ceiling
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await file.read(_UPLOAD_CHUNK_BYTES)
        if not chunk:
            break
        total += len(chunk)
        if total > limit:
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
    the path of the MCP endpoint, if this deployment serves one (ADR-029), and
    the same pair for keyword methods (ADR-032): which ones this build can run,
    and which of them run when a request names none. `keyword_methods` is the
    installed set, so a UI offers only the choices that will actually work
    instead of a checkbox that reports itself skipped.
    """
    return {
        "status": "ok",
        "service": "markdown-ingestion-engine",
        "version": app.version,
        "engines": sorted(REQUESTABLE_ENGINES),
        "default_engine": default_engine(),
        "mcp_endpoint": MCP_ENDPOINT,
        "keyword_methods": available_methods(),
        "default_keyword_methods": default_methods(),
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


@app.post(
    "/convert/batch",
    response_model=BatchResponse,
    tags=["convert"],
    operation_id="convert_batch",
    summary="Convert several uploaded documents in one request",
)
@limiter.limit("5/minute")
async def convert_batch(
    request: Request,
    files: list[UploadFile] = File(..., description=_BATCH_FILES_DESCRIPTION),
    engine: str | None = Form(default=None, description=_ENGINE_DESCRIPTION),
    split_chapters: bool = Form(default=False, description=_BATCH_SPLIT_DESCRIPTION),
) -> BatchResponse:
    """Convert a set of uploaded documents, one Markdown file per document.

    The batch exists because "convert these thirty reports" should not be thirty
    requests against a `20/minute` limit, and because the client that asked for
    them wants one archive at the end (ADR-031). It is deliberately *not* a new
    kind of conversion: each document goes through exactly the path
    `/convert/file` would take it through, one at a time, each taking its own
    slot in the concurrency ceiling so a large batch queues fairly alongside
    other callers rather than holding the engine for the whole run.

    A document that fails does not fail the batch: its entry carries the error
    the single-file endpoint would have returned, and the rest still convert.
    """
    if split_chapters:
        # Mutually exclusive for now (ADR-031): chapter detection is a heuristic
        # a reader is meant to *check* before saving 30 files, and there is no
        # good answer yet for what a zip of twelve documents' chapters should
        # look like. Refused rather than ignored — silently dropping a flag the
        # caller set is how a client comes to believe it got chapters.
        raise HTTPException(
            status_code=400,
            detail=(
                "split_chapters is not available on a batch conversion. Convert a "
                "document on its own with POST /convert/file to split it into chapters."
            ),
        )
    resolved_engine = _resolve_engine_or_400(engine)
    if not files:
        raise HTTPException(status_code=400, detail="No files uploaded.")
    if len(files) > MAX_BATCH_FILES:
        raise HTTPException(
            status_code=400,
            detail=f"A batch is limited to {MAX_BATCH_FILES} files; {len(files)} were sent.",
        )

    results: list[BatchItem] = []
    budget = MAX_BATCH_BYTES
    for position, upload in enumerate(files):
        source = upload.filename or f"upload-{position + 1}"
        try:
            data = await _read_upload(upload, ceiling=min(MAX_UPLOAD_BYTES, budget))
        except HTTPException:
            if budget < MAX_UPLOAD_BYTES:
                # The batch as a whole ran out of room, so nothing after this
                # point could convert either: that is a request-level refusal.
                raise HTTPException(
                    status_code=413,
                    detail=f"The batch exceeds the {MAX_BATCH_BYTES // (1024 * 1024)} MB total limit.",
                ) from None
            results.append(_batch_failure(source, f"File exceeds the {MAX_UPLOAD_BYTES // (1024 * 1024)} MB limit."))
            continue
        budget -= len(data)

        if not data:
            results.append(_batch_failure(source, "Empty file upload."))
            continue

        async with _job_slot():
            try:
                markdown = await asyncio.to_thread(file_to_markdown, data, source, resolved_engine)
            except ValueError as exc:
                # Unsupported extension — the 415 of a single-file request.
                results.append(_batch_failure(source, str(exc)))
                continue
            except Exception as exc:  # noqa: BLE001 - one bad document, not a bad batch
                logger.exception("Batch conversion failed for %s", source)
                results.append(_batch_failure(source, f"Failed to convert file: {exc}"))
                continue
        results.append(
            BatchItem(status="ok", source=source, markdown=markdown, length=len(markdown))
        )

    # Named only now, and only from what succeeded, so a caller's save loop is
    # "write every item that has a filename" and duplicate uploaded names
    # disambiguate against the documents that actually produced Markdown.
    converted = [item for item in results if item.status == "ok"]
    filenames = unique_filenames(item.source for item in converted)
    for item, filename in zip(converted, filenames):
        item.filename = filename

    for item in results:
        metrics.record_conversion("batch", item.status)
    failed = len(results) - len(converted)
    metrics.record_batch(len(results), failed)
    return BatchResponse(
        count=len(results), succeeded=len(converted), failed=failed, results=results
    )


@app.post(
    "/keywords",
    response_model=KeywordResponse,
    tags=["analyse"],
    operation_id="extract_keywords",
    summary="Extract ranked keywords from converted Markdown",
)
@limiter.limit("20/minute")
async def keywords(request: Request, body: KeywordRequest) -> KeywordResponse:
    """Rank what a converted document is *about*, with the evidence for each term.

    A second call rather than a flag on the convert routes (ADR-032). Keyword
    extraction is something a reader decides they want *after* seeing the
    Markdown, and making it a flag would mean re-converting — a minute of docling
    on a scanned report — every time someone changed their mind about the method
    set. Taking the document as the request body instead means the choice can be
    revisited, with different methods, for the cost of the extraction alone.

    The answer is a ranked list where each term carries the rank and native score
    every method gave it, so a caller can see whether four methods agreed or one
    method guessed. `prepend_table` additionally returns the document with the
    keywords as a Markdown table at the top.
    """
    markdown = body.markdown
    if not markdown.strip():
        raise HTTPException(status_code=400, detail="No markdown supplied.")
    if len(markdown) > MAX_KEYWORD_CHARS:
        raise HTTPException(
            status_code=413,
            detail=f"Document exceeds the {MAX_KEYWORD_CHARS:,}-character limit for keyword extraction.",
        )
    methods = _resolve_methods_or_400(body.methods)

    async with _job_slot():
        # Off the event loop and inside the job slot: `keybert` is minutes of CPU
        # inference on a free tier, and even the cheap methods scan the whole
        # document. It queues behind the same ceiling as a conversion
        # (invariant #4) rather than running beside four of them.
        try:
            result = await asyncio.to_thread(
                extract_keywords,
                markdown,
                methods=methods,
                top_k=body.top_k,
                language=body.language,
            )
        except Exception as exc:  # noqa: BLE001 - surface a clean 502
            metrics.record_keyword_extraction((), 0)
            logger.exception("Keyword extraction failed for %s", body.source or "document")
            raise HTTPException(status_code=502, detail=f"Failed to extract keywords: {exc}") from exc
        annotated = (
            await asyncio.to_thread(prepend_keyword_table, markdown, result)
            if body.prepend_table
            else None
        )

    metrics.record_keyword_extraction(
        result.methods_used, len(result.keywords), result.methods_skipped
    )
    return KeywordResponse(
        source=body.source or "document",
        keyword_count=len(result.keywords),
        keywords=[
            KeywordModel(
                term=keyword.term,
                score=keyword.score,
                rank=keyword.rank,
                kind=keyword.kind,
                occurrences=keyword.occurrences,
                agreement=keyword.agreement,
                methods={
                    name: KeywordMethodScore(rank=score.rank, score=score.score)
                    for name, score in keyword.methods.items()
                },
            )
            for keyword in result.keywords
        ],
        methods_used=list(result.methods_used),
        methods_skipped=result.methods_skipped,
        language=result.language,
        note=result.note or None,
        markdown=annotated,
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
