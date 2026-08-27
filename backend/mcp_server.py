"""wiseau MCP server — the Markdown ingestion engine as MCP tools.

This exposes the conversion endpoints (`/convert/url`, `/convert/file`,
`/convert/batch`), keyword extraction (`/keywords`, `/keywords/batch`) and the
health probe (`/ping`) as Model Context Protocol tools so that LLM agents
(Claude Desktop, IDE agents, custom clients) can ingest documents the same way
the web UI does.

Design: this is a **thin HTTP adapter**, not a second engine. Every tool call is
an HTTP request to a running backend (`WISEAU_API_BASE`), so the MCP surface
reuses the exact same `MarkdownResponse` contract (`source`, `markdown`,
`length`) and inherits the backend's fair-use guards — per-IP rate limiting and
the global concurrency ceiling — unchanged. There is no in-process bypass of
those guards, per the tech-spec invariants. See `docs/mcp.md` and ADR-009.

There are two ways this surface reaches a client:

1. **Hosted** — `main.py` grafts `hosted_routes()` onto the FastAPI app, so the
   deployed backend answers MCP over HTTP at `/mcp` and *any* LLM that can add a
   remote connector by URL can use it. One container, one URL, no second service
   to pay for (ADR-029). This is the deployment path; see `docs/hosting.md`.
2. **Standalone** — run this module as its own process, over `stdio` (the client
   spawns it; Claude Desktop, Claude Code) or `streamable-http`:

       cd backend
       pip install -r requirements.txt -r requirements-mcp.txt
       WISEAU_API_BASE=http://localhost:7860 python mcp_server.py

Either way the backend must be reachable at `WISEAU_API_BASE` (default
``http://127.0.0.1:$PORT``, i.e. ``http://127.0.0.1:7860`` locally).
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import httpx
from mcp.server.fastmcp import FastMCP

# --- Configuration ----------------------------------------------------------
# The MCP server's only coupling to the backend is this base URL — mirroring the
# frontend's single `MARKDOWN_API_BASE` knob (ADR-004). No build-time coupling.
#
# The fallback follows `$PORT` because the hosted deployment runs this surface
# *inside* the backend process (ADR-029) and calls it back over the loopback
# interface; `main.py` binds that same `$PORT`. With `PORT` unset the default is
# the historical `7860`, so a local `python mcp_server.py` is unchanged.
def _default_api_base() -> str:
    """Where to reach the backend when `WISEAU_API_BASE` says nothing."""
    return f"http://127.0.0.1:{os.environ.get('PORT', '7860')}"


API_BASE = (os.environ.get("WISEAU_API_BASE") or _default_api_base()).rstrip("/")

# Headless renders and PDF extraction can be slow; allow a generous per-request
# timeout so large jobs are not cut off before the backend finishes.
REQUEST_TIMEOUT = float(os.environ.get("WISEAU_MCP_TIMEOUT", "120"))

# Content types the backend dispatches on (it keys off the filename extension).
_CONTENT_TYPES = {
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}

mcp = FastMCP("wiseau")


# --- HTTP plumbing ----------------------------------------------------------
def _client() -> httpx.AsyncClient:
    """Build the HTTP client used to reach the backend.

    Factored out so tests can substitute a ``MockTransport``-backed client
    without a live server.
    """
    return httpx.AsyncClient(base_url=API_BASE, timeout=REQUEST_TIMEOUT)


def _unwrap(response: httpx.Response) -> dict[str, Any]:
    """Return the JSON body on success; raise a clean error otherwise.

    Backend errors carry a JSON ``detail`` (see tech-spec §7). Surface that
    verbatim so the agent sees the same message a human would, never a stack
    trace or a bare status code.
    """
    if response.is_success:
        return response.json()
    try:
        detail = response.json().get("detail", response.text)
    except ValueError:  # non-JSON body
        detail = response.text
    raise RuntimeError(f"wiseau backend error {response.status_code}: {detail}")


# --- Tools ------------------------------------------------------------------
@mcp.tool()
async def convert_url(url: str, engine: str = "auto", split_chapters: bool = False) -> dict[str, Any]:
    """Convert a web page to clean, deterministic Markdown.

    Renders the URL with a JavaScript-aware headless browser and extracts its
    primary content (article body, docs page, etc.), stripping navigation and
    boilerplate. Identical input yields identical Markdown.

    Args:
        url: Absolute ``http`` or ``https`` URL of the page to convert.
        engine: Document engine, used only when the URL serves a **PDF**:
            ``"docling"`` (highest fidelity, much slower), ``"pymupdf"`` (fast,
            deterministic), or ``"auto"`` (the deployment's default).
        split_chapters: Also return the document split into chapters, each with a
            suggested filename — set this when the user wants one file per
            chapter. Only worth asking for on a long, chaptered document; a web
            article has no chapters and will come back as ``"none"``.

    Returns:
        ``{"source": <url>, "markdown": <content>, "length": <char count>}``, plus
        ``chapters`` and ``chapter_detection`` when ``split_chapters`` is set.
    """
    async with _client() as client:
        response = await client.post(
            "/convert/url",
            json={"url": url, "engine": engine, "split_chapters": split_chapters},
        )
    return _unwrap(response)


@mcp.tool()
async def convert_file(path: str, engine: str = "auto", split_chapters: bool = False) -> dict[str, Any]:
    """Convert a local PDF or DOCX file to clean, deterministic Markdown.

    Reads the file at ``path`` from the machine running this MCP server and
    parses it. Only ``.pdf`` and ``.docx`` are supported.

    Args:
        path: Filesystem path to a local ``.pdf`` or ``.docx`` file.
        engine: ``"docling"`` for the highest-fidelity conversion of complex or
            scanned documents (much slower on free CPU), ``"pymupdf"`` for the
            fast deterministic parser, or ``"auto"`` (the deployment's default).
            docling always falls back to the fast parser if it is unavailable.
        split_chapters: Also return the document split into chapters. Set this
            when the user asks for a long document to be broken up — each chapter
            comes back with its own ``markdown`` and a numbered ``filename``, so
            saving one file per chapter is a loop over ``chapters``.

    Returns:
        ``{"source": <filename>, "markdown": <content>, "length": <char count>}``,
        plus ``chapters`` (``title``/``level``/``filename``/``markdown``/
        ``length``) and ``chapter_detection`` (``toc``/``headings``/``markers``/
        ``none``) when ``split_chapters`` is set.
    """
    file_path = Path(path)
    data = file_path.read_bytes()
    content_type = _CONTENT_TYPES.get(file_path.suffix.lower(), "application/octet-stream")
    files = {"file": (file_path.name, data, content_type)}
    async with _client() as client:
        response = await client.post(
            "/convert/file",
            files=files,
            data={"engine": engine, "split_chapters": "true" if split_chapters else "false"},
        )
    return _unwrap(response)


@mcp.tool()
async def convert_batch(paths: list[str], engine: str = "auto") -> dict[str, Any]:
    """Convert several local PDF or DOCX files to Markdown in one request.

    Use this instead of calling ``convert_file`` in a loop when the user asks for
    a folder, a list, or "all of these" — it is one request against the backend's
    rate limit rather than one per document, and the results come back in the
    order the paths were given, each with the filename to save it as.

    One document failing does not fail the rest: its entry has
    ``"status": "error"`` and no ``filename``. Writing every result that *has* a
    ``filename`` is therefore the whole of a correct save loop.

    Args:
        paths: Filesystem paths to local ``.pdf`` or ``.docx`` files, on the
            machine running this MCP server. Every path must be readable — an
            unreadable one is reported before any conversion runs, rather than
            after paying for the others.
        engine: ``"docling"``, ``"pymupdf"``, or ``"auto"``, applied to every
            document in the batch. Note that ``docling`` costs tens of seconds to
            minutes *per document* on free CPU, so a batch of any size is a long
            wait; ``auto`` (the fast parser on a standard deployment) is usually
            the right choice for bulk work.

    Returns:
        ``{"count": n, "succeeded": n, "failed": n, "results": [...]}`` where each
        result is ``{"status", "source", "filename", "markdown", "length",
        "error"}``.

    Note:
        Chapter splitting is not available on a batch (ADR-031). To split a long
        document into per-chapter files, call ``convert_file`` on it with
        ``split_chapters=True``.
    """
    payload = []
    for raw in paths:
        path = Path(raw)
        try:
            data = path.read_bytes()
        except OSError as exc:
            # Fail before sending anything: a batch that silently skipped a path
            # would look like a successful conversion of everything the agent
            # asked for, and the missing document would go unnoticed.
            raise RuntimeError(f"Cannot read {raw}: {exc}") from exc
        content_type = _CONTENT_TYPES.get(path.suffix.lower(), "application/octet-stream")
        payload.append(("files", (path.name, data, content_type)))

    async with _client() as client:
        response = await client.post("/convert/batch", files=payload, data={"engine": engine})
    return _unwrap(response)


@mcp.tool()
async def extract_keywords(
    markdown: str,
    methods: list[str] | None = None,
    top_k: int = 20,
    language: str | None = None,
    prepend_table: bool = False,
    source: str | None = None,
) -> dict[str, Any]:
    """Extract ranked, weighted keywords from Markdown you already have.

    Use this **after** a conversion, on the Markdown one of the ``convert_*``
    tools returned — it takes the document itself, so answering costs only the
    extraction, not a second conversion. It works equally well on any Markdown
    the user supplies.

    Several independent methods rank the document's terms and the rankings are
    fused, so every keyword reports how many methods found it (``agreement``)
    and where each one placed it. Prefer a term three methods agree on over one
    a single method ranked first.

    Args:
        markdown: The document to analyse.
        methods: Which methods to run — any of ``"frequency"`` (built-in,
            always available), ``"yake"`` (statistical), ``"spacy"`` (noun
            chunks and named entities), ``"keybert"`` (semantic; accurate but
            can take **tens of seconds on free CPU**, so ask for it only when
            the user wants the best answer). Omit for the deployment's default;
            ``["all"]`` for everything it can run. A method that is not
            installed is reported in ``methods_skipped`` and the rest still
            answer — check that field before telling the user which ran.
        top_k: How many keywords to return (1-100).
        language: Language code for the language-aware methods, e.g. ``"en"``.
        prepend_table: Also return the document with the keywords rendered as a
            Markdown table at the top, in ``markdown``. Set this when the user
            wants the keywords saved *into* the document; leave it off when they
            want the list alone, since it sends the whole document back.
        source: A label for the document (filename or URL), echoed as ``source``.

    Returns:
        ``{"source", "keyword_count", "keywords": [...], "methods_used",
        "methods_skipped", "language", "note", "markdown"}``. Each keyword is
        ``{"term", "score", "rank", "kind", "occurrences", "agreement",
        "methods": {<name>: {"rank", "score"}}}``, where ``score`` is a relative
        weight with the top keyword at 1.0. ``note`` explains an empty or
        partial answer (a document too short to characterise, or one truncated
        at the analysis ceiling); ``markdown`` is null unless ``prepend_table``.
    """
    payload: dict[str, Any] = {
        "markdown": markdown,
        "top_k": top_k,
        "prepend_table": prepend_table,
    }
    if methods:
        payload["methods"] = methods
    if language:
        payload["language"] = language
    if source:
        payload["source"] = source
    async with _client() as client:
        response = await client.post("/keywords", json=payload)
    return _unwrap(response)


@mcp.tool()
async def extract_keywords_batch(
    documents: list[dict[str, str]],
    methods: list[str] | None = None,
    top_k: int = 20,
    language: str | None = None,
    prepend_table: bool = False,
) -> dict[str, Any]:
    """Extract keywords from several documents in one request.

    The companion to ``convert_batch``: having converted a folder, use this
    rather than calling ``extract_keywords`` once per document — it is **one**
    request against the backend's rate limit instead of one each, and the
    results come back in the order sent, each with the ``.md`` filename its
    keywords belong to.

    The natural chain is ``convert_batch(paths)`` → this, passing each result's
    ``markdown`` and its ``filename`` as ``source``: the filenames then match
    across both sets of results, so writing each document's table into its own
    file, or collecting the lot into one JSON sidecar, is a plain zip of the two
    lists.

    One document failing does not fail the rest: its entry has
    ``"status": "error"`` and no ``filename``.

    Args:
        documents: One entry per document, each ``{"markdown": "...",
            "source": "annual-report.md"}``. ``source`` is a label used to derive
            the result's ``filename``; it is never fetched.
        methods: As for ``extract_keywords``, applied to every document. Note
            that ``keybert`` costs tens of seconds *per document* on free CPU, so
            a batch of any size wants the default fast methods.
        top_k: How many keywords per document (1-100).
        language: Language code for the language-aware methods.
        prepend_table: Also return each document with its own keyword table
            prepended, in that result's ``markdown``. Off by default — on a
            twenty-document batch it sends every document back.

    Returns:
        ``{"count": n, "succeeded": n, "failed": n, "results": [...]}`` where each
        result is an ``extract_keywords`` answer plus ``status``, ``filename``
        and ``error``.
    """
    payload: list[dict[str, str]] = []
    for position, document in enumerate(documents):
        markdown = document.get("markdown") if isinstance(document, dict) else None
        if not isinstance(markdown, str) or not markdown.strip():
            # Refused before anything is sent: a batch that silently skipped a
            # document would look like a complete answer with one missing.
            raise RuntimeError(
                f"documents[{position}] has no 'markdown' — each entry must be "
                '{"markdown": "...", "source": "..."}.'
            )
        entry: dict[str, str] = {"markdown": markdown}
        source = document.get("source")
        if isinstance(source, str) and source.strip():
            entry["source"] = source
        payload.append(entry)

    body: dict[str, Any] = {
        "documents": payload,
        "top_k": top_k,
        "prepend_table": prepend_table,
    }
    if methods:
        body["methods"] = methods
    if language:
        body["language"] = language
    async with _client() as client:
        response = await client.post("/keywords/batch", json=body)
    return _unwrap(response)


@mcp.tool()
async def ping() -> dict[str, Any]:
    """Check that the backend is reachable and report its service version.

    Returns:
        ``{"status": "ok", "service": ..., "version": ...}``, plus the engines
        and keyword methods this deployment can run and the ones it defaults to.
    """
    async with _client() as client:
        response = await client.get("/ping")
    return _unwrap(response)


# --- Remote (HTTP) transport ------------------------------------------------
def mcp_path() -> str:
    """The URL path the streamable-http endpoint is served at.

    `/mcp` by default. `WISEAU_MCP_PATH` can move it to an unguessable path
    (`/mcp-3f9c…`), which is the only access control every MCP client can use —
    a connector URL is the one thing they all let you set, where custom auth
    headers are not (ADR-029). That is obscurity, not authentication; the
    backend's rate limits remain the real guard.
    """
    raw = os.environ.get("WISEAU_MCP_PATH", "/mcp").strip().strip("/")
    return f"/{raw}" if raw else "/mcp"


def configure_transport_security() -> None:
    """Set the Host/Origin allow-list for the HTTP transport.

    FastMCP defaults to accepting `localhost`/`127.0.0.1` only, as DNS-rebinding
    protection. That is right for a server bound to loopback and wrong for one
    whose entire purpose is to be reached at a public hostname — it answers 421
    to every real client. So:

    * `WISEAU_MCP_ALLOWED_HOSTS` unset (or `*`) — the check is off. The
      deployment is public by design, matching the API's `allow_origins=["*"]`
      CORS stance (ADR-029); rate limiting is what protects it.
    * `WISEAU_MCP_ALLOWED_HOSTS=a.example.com,b.example.com` — only those hosts
      (plus loopback) are accepted, for an operator who wants the check.
    """
    security = mcp.settings.transport_security
    raw = os.environ.get("WISEAU_MCP_ALLOWED_HOSTS", "").strip()
    hosts = [h.strip() for h in raw.split(",") if h.strip() and h.strip() != "*"]
    if not hosts:
        security.enable_dns_rebinding_protection = False
        return
    security.enable_dns_rebinding_protection = True
    security.allowed_hosts = sorted({*security.allowed_hosts, *hosts})
    security.allowed_origins = sorted(
        {*security.allowed_origins, *(f"https://{h}" for h in hosts), *(f"http://{h}" for h in hosts)}
    )


def hosted_routes() -> list:
    """Build the streamable-http endpoint as routes another ASGI app can serve.

    Returned rather than run, so `main.py` can graft the endpoint onto the
    FastAPI app and one container serves both the REST API and the MCP surface
    (ADR-029) — a single free-tier service instead of two. The caller *must*
    drive `session_manager()` from its lifespan, or requests hang.

    Sessions are stateless: a scale-to-zero host may answer two requests of the
    same conversation from two different instances, and in-memory session state
    would not survive that.
    """
    mcp.settings.stateless_http = True
    mcp.settings.streamable_http_path = mcp_path()
    configure_transport_security()
    # `streamable_http_app()` is what lazily constructs the session manager, so
    # it is called for its effect as much as its routes.
    return list(mcp.streamable_http_app().routes)


def session_manager():
    """The StreamableHTTP session manager; run it from the host app's lifespan."""
    return mcp.session_manager


def _configure_standalone_transport() -> None:
    """Point a standalone HTTP server at a public host/port.

    Only for `--transport streamable-http`, i.e. running this module as its own
    process. `WISEAU_MCP_HOST`/`WISEAU_MCP_PORT` set the bind address.
    """
    mcp.settings.host = os.environ.get("WISEAU_MCP_HOST", "0.0.0.0")
    mcp.settings.port = int(os.environ.get("WISEAU_MCP_PORT", "8080"))
    mcp.settings.stateless_http = True
    mcp.settings.streamable_http_path = mcp_path()
    configure_transport_security()


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="wiseau MCP server — expose the ingestion engine to MCP clients.")
    parser.add_argument(
        "--transport",
        choices=["stdio", "streamable-http"],
        default=os.environ.get("WISEAU_MCP_TRANSPORT", "stdio"),
        help="'stdio' for a local agent client (e.g. Claude Desktop, Claude Code); "
        "'streamable-http' to serve the same tools over HTTP so any remote MCP "
        "client (claude.ai connectors, other agents) can reach them. "
        "Defaults to $WISEAU_MCP_TRANSPORT, then 'stdio'. Note that a hosted "
        "deployment normally needs neither: the backend serves the same endpoint "
        "itself (ADR-029).",
    )
    args = parser.parse_args()

    if args.transport == "streamable-http":
        _configure_standalone_transport()
    mcp.run(args.transport)
