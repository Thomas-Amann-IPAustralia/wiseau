"""wiseau MCP server — the Markdown ingestion engine as MCP tools.

This exposes the two conversion endpoints (`/convert/url`, `/convert/file`) and
the health probe (`/ping`) as Model Context Protocol tools so that LLM agents
(Claude Desktop, IDE agents, custom clients) can ingest documents the same way
the web UI does.

Design: this is a **thin HTTP adapter**, not a second engine. Every tool call is
an HTTP request to a running backend (`WISEAU_API_BASE`), so the MCP surface
reuses the exact same `MarkdownResponse` contract (`source`, `markdown`,
`length`) and inherits the backend's fair-use guards — per-IP rate limiting and
the global concurrency ceiling — unchanged. There is no in-process bypass of
those guards, per the tech-spec invariants. See `docs/mcp.md` and ADR-009.

Run it (stdio transport, for local agent integration):

    cd backend
    pip install -r requirements.txt -r requirements-mcp.txt
    WISEAU_API_BASE=http://localhost:7860 python mcp_server.py

The backend must be reachable at `WISEAU_API_BASE` (default
``http://localhost:7860``) for the tools to work.
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
API_BASE = os.environ.get("WISEAU_API_BASE", "http://localhost:7860").rstrip("/")

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
async def convert_url(url: str, engine: str = "auto") -> dict[str, Any]:
    """Convert a web page to clean, deterministic Markdown.

    Renders the URL with a JavaScript-aware headless browser and extracts its
    primary content (article body, docs page, etc.), stripping navigation and
    boilerplate. Identical input yields identical Markdown.

    Args:
        url: Absolute ``http`` or ``https`` URL of the page to convert.
        engine: Document engine, used only when the URL serves a **PDF**:
            ``"docling"`` (highest fidelity, much slower), ``"pymupdf"`` (fast,
            deterministic), or ``"auto"`` (the deployment's default).

    Returns:
        ``{"source": <url>, "markdown": <content>, "length": <char count>}``.
    """
    async with _client() as client:
        response = await client.post("/convert/url", json={"url": url, "engine": engine})
    return _unwrap(response)


@mcp.tool()
async def convert_file(path: str, engine: str = "auto") -> dict[str, Any]:
    """Convert a local PDF or DOCX file to clean, deterministic Markdown.

    Reads the file at ``path`` from the machine running this MCP server and
    parses it. Only ``.pdf`` and ``.docx`` are supported.

    Args:
        path: Filesystem path to a local ``.pdf`` or ``.docx`` file.
        engine: ``"docling"`` for the highest-fidelity conversion of complex or
            scanned documents (much slower on free CPU), ``"pymupdf"`` for the
            fast deterministic parser, or ``"auto"`` (the deployment's default).
            docling always falls back to the fast parser if it is unavailable.

    Returns:
        ``{"source": <filename>, "markdown": <content>, "length": <char count>}``.
    """
    file_path = Path(path)
    data = file_path.read_bytes()
    content_type = _CONTENT_TYPES.get(file_path.suffix.lower(), "application/octet-stream")
    files = {"file": (file_path.name, data, content_type)}
    async with _client() as client:
        response = await client.post("/convert/file", files=files, data={"engine": engine})
    return _unwrap(response)


@mcp.tool()
async def ping() -> dict[str, Any]:
    """Check that the backend is reachable and report its service version.

    Returns:
        ``{"status": "ok", "service": ..., "version": ...}``.
    """
    async with _client() as client:
        response = await client.get("/ping")
    return _unwrap(response)


def _configure_remote_transport() -> None:
    """Point the server at a public host/port and widen the Host-header allow-list.

    FastMCP's streamable-http transport binds `127.0.0.1` and only accepts
    requests whose `Host` header matches `localhost`/`127.0.0.1` by default (DNS
    rebinding protection) — safe for a local process, useless for a remote
    deployment. `WISEAU_MCP_HOST`/`WISEAU_MCP_PORT` open the bind address;
    `WISEAU_MCP_ALLOWED_HOSTS` (comma-separated, e.g. your HF Space's hostname)
    must be set for a remote client's requests to pass the Host check.
    """
    mcp.settings.host = os.environ.get("WISEAU_MCP_HOST", "0.0.0.0")
    mcp.settings.port = int(os.environ.get("WISEAU_MCP_PORT", "8080"))
    extra_hosts = [h.strip() for h in os.environ.get("WISEAU_MCP_ALLOWED_HOSTS", "").split(",") if h.strip()]
    if extra_hosts:
        security = mcp.settings.transport_security
        security.allowed_hosts = list({*security.allowed_hosts, *extra_hosts})
        security.allowed_origins = list(
            {*security.allowed_origins, *(f"https://{h}" for h in extra_hosts), *(f"http://{h}" for h in extra_hosts)}
        )


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
        "Defaults to $WISEAU_MCP_TRANSPORT, then 'stdio'.",
    )
    args = parser.parse_args()

    if args.transport == "streamable-http":
        _configure_remote_transport()
    mcp.run(args.transport)
