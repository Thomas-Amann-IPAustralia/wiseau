"""Universal Markdown Ingestion Engine — FastAPI entrypoint.

Exposes a small, public HTTP surface that converts web URLs, PDFs, and DOCX
documents into clean, deterministic Markdown. The API is intentionally open
(any origin may call it) but guarded by per-IP rate limiting and a global
concurrency ceiling so the shared free compute stays healthy for everyone.
"""

from __future__ import annotations

import asyncio
import logging
import os

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, HttpUrl
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

from parsers import file_to_markdown, url_to_markdown

logging.basicConfig(level=logging.INFO)
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
    version="0.3.0",
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
)


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
    async with _job_semaphore:
        try:
            markdown = await asyncio.to_thread(url_to_markdown, url)
        except Exception as exc:  # noqa: BLE001 - surface a clean 502 to the caller
            logger.exception("URL conversion failed for %s", url)
            raise HTTPException(status_code=502, detail=f"Failed to convert URL: {exc}") from exc
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

    async with _job_semaphore:
        try:
            markdown = await asyncio.to_thread(file_to_markdown, data, file.filename or "")
        except ValueError as exc:
            raise HTTPException(status_code=415, detail=str(exc)) from exc
        except Exception as exc:  # noqa: BLE001
            logger.exception("File conversion failed for %s", file.filename)
            raise HTTPException(status_code=502, detail=f"Failed to convert file: {exc}") from exc
    return MarkdownResponse(source=file.filename or "upload", markdown=markdown, length=len(markdown))


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=int(os.environ.get("PORT", "7860")))
