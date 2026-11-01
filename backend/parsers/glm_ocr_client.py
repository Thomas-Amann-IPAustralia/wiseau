"""Thin HTTP client to the GLM-OCR converter service (ADR-034).

GLM-OCR is a 0.9B vision-language OCR model run behind its official two-stage
pipeline (PP-DocLayoutV3 layout analysis, then region-by-region recognition). It
is the third document engine, next to the deterministic PyMuPDF/Mammoth parsers
and docling, and it is **opt-in** in exactly the way docling is (ADR-027): a
deployment selects it with ``WISEAU_PDF_ENGINE=glm-ocr`` or a request with
``engine="glm-ocr"``. Like docling it runs as a **separate service** (the
``glm-ocr/`` directory), so the backend image gains no torch/transformers
dependency; its only knowledge of GLM-OCR is ``WISEAU_GLM_OCR_BASE``.

The client deliberately mirrors ``docling_client`` (ADR-016): stdlib ``urllib``,
an injectable transport for tests, and typed errors that let ``file_parser``
fall back to the deterministic parser on *any* failure (ADR-014's rule applies
to every remote engine):

* :class:`GlmOcrUnavailable` — unreachable, timeout, 5xx, 401/403/429, or an
  empty / non-JSON answer. An infrastructure problem; a retry may succeed.
* :class:`GlmOcrBadDocument` — another 4xx: the service read the input and
  refused it (unsupported type, too many pages, corrupt file).

**What GLM-OCR does not do.** It reads *pixels*: every page is rasterized and
re-transcribed, so it never uses a born-digital PDF's text layer, and it has no
DOCX reader at all. ``file_parser`` therefore only sends it PDFs and images —
see :data:`SUPPORTED_EXTENSIONS` — and a DOCX asked to go through GLM-OCR goes
straight to Mammoth (recorded as the ``unsupported_type`` skip).

Configuration (all optional; with ``WISEAU_GLM_OCR_BASE`` unset the engine is
"not configured" and callers skip straight to the deterministic parsers):

* ``WISEAU_GLM_OCR_BASE``    — base URL of the GLM-OCR service. Unset ⇒ disabled.
* ``WISEAU_GLM_OCR_TOKEN``   — ``Authorization: Bearer`` (gateway credential,
  e.g. a Hugging Face token for a private Space).
* ``WISEAU_GLM_OCR_API_KEY`` — ``X-Api-Key`` (the service's own guard,
  ``GLM_OCR_API_KEY`` on the service side).
* ``WISEAU_GLM_OCR_TIMEOUT`` — per-request timeout in seconds (default 300). On
  CPU the model costs **minutes per page** (see ADR-034's measurements), so this
  bounds how long a caller waits before the fallback answers.
* ``WISEAU_GLM_OCR_PATH``    — convert path (default ``/v1/convert/file``).

The returned Markdown is **not** normalized here — the caller runs it through
``clean_markdown()`` like every other path (invariant #3).
"""

from __future__ import annotations

import hashlib
import json
import os
import urllib.error
import urllib.request
from typing import Callable, Optional

Transport = Callable[[urllib.request.Request, float], bytes]

DEFAULT_TIMEOUT = 300.0
DEFAULT_PATH = "/v1/convert/file"

# GLM-OCR transcribes images; a PDF is rasterized page by page on the service.
SUPPORTED_EXTENSIONS = frozenset(
    {".pdf", ".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp", ".gif"}
)


class GlmOcrError(RuntimeError):
    """Base class for any GLM-OCR conversion failure — the caller falls back."""


class GlmOcrUnavailable(GlmOcrError):
    """The service could not be reached or did not return a usable conversion."""


class GlmOcrBadDocument(GlmOcrError):
    """The service ran but rejected the document (a non-infrastructure 4xx)."""


_INFRASTRUCTURE_4XX = frozenset({401, 403, 429})


# --- Configuration ----------------------------------------------------------
def _base() -> str:
    return os.environ.get("WISEAU_GLM_OCR_BASE", "").strip().rstrip("/")


def _token() -> str:
    return os.environ.get("WISEAU_GLM_OCR_TOKEN", "").strip()


def _api_key() -> str:
    return os.environ.get("WISEAU_GLM_OCR_API_KEY", "").strip()


def _timeout() -> float:
    try:
        return float(os.environ.get("WISEAU_GLM_OCR_TIMEOUT", str(DEFAULT_TIMEOUT)))
    except ValueError:
        return DEFAULT_TIMEOUT


def _path() -> str:
    path = os.environ.get("WISEAU_GLM_OCR_PATH", DEFAULT_PATH).strip() or DEFAULT_PATH
    return path if path.startswith("/") else "/" + path


def is_configured() -> bool:
    """True when a GLM-OCR base URL is set, so the engine may be attempted."""
    return bool(_base())


def supports(filename: str) -> bool:
    """True when GLM-OCR can read this file type (PDFs and images, not DOCX)."""
    return os.path.splitext(filename or "")[1].lower() in SUPPORTED_EXTENSIONS


# --- HTTP plumbing ----------------------------------------------------------
def _default_transport(request: urllib.request.Request, timeout: float) -> bytes:
    with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 - fixed backend URL
        return response.read()


def _encode_multipart(filename: str, data: bytes) -> tuple[bytes, str]:
    """Encode ``data`` as a one-file ``multipart/form-data`` body (field ``files``).

    The boundary is derived from a hash of the bytes: deterministic, and it
    cannot collide with the document's own content.
    """
    boundary = "wiseau" + hashlib.sha256(data).hexdigest()[:32]
    safe_name = filename.replace('"', "").replace("\r", "").replace("\n", "") or "document"
    marker = f"--{boundary}".encode("ascii")
    body = b"\r\n".join(
        [
            marker,
            b'Content-Disposition: form-data; name="files"; filename="' + safe_name.encode("utf-8") + b'"',
            b"Content-Type: application/octet-stream",
            b"",
            data,
            f"--{boundary}--".encode("ascii"),
            b"",
        ]
    )
    return body, f"multipart/form-data; boundary={boundary}"


def _read_error_detail(exc: urllib.error.HTTPError) -> str:
    try:
        raw = exc.read().decode("utf-8", "replace")
    except Exception:  # noqa: BLE001 - the body may be unreadable; degrade gracefully
        return exc.reason or "unknown error"
    try:
        payload = json.loads(raw)
    except ValueError:
        return raw.strip() or (exc.reason or "unknown error")
    for key in ("detail", "message", "error"):
        value = payload.get(key) if isinstance(payload, dict) else None
        if value:
            return str(value)
    return raw.strip() or (exc.reason or "unknown error")


def _markdown_from_response(payload: object) -> str:
    """Extract the Markdown from the service's JSON answer.

    The wiseau service returns ``{"markdown": "..."}``; the GLM-OCR SDK's own
    server shape (``markdown_result`` / ``md_results``) is tolerated too, so the
    backend can also be pointed straight at a stock ``glmocr`` server.
    """
    if isinstance(payload, dict):
        for key in ("markdown", "markdown_result", "md_results", "md_content"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return value
        document = payload.get("document")
        if isinstance(document, dict):
            value = document.get("md_content")
            if isinstance(value, str) and value.strip():
                return value
    return ""


# --- Public API -------------------------------------------------------------
def convert_document(
    data: bytes,
    filename: str,
    *,
    base: Optional[str] = None,
    token: Optional[str] = None,
    api_key: Optional[str] = None,
    timeout: Optional[float] = None,
    path: Optional[str] = None,
    transport: Optional[Transport] = None,
) -> str:
    """Convert ``data`` to Markdown via the GLM-OCR service; return raw Markdown.

    Raises:
        GlmOcrUnavailable: unreachable/timed out/5xx/401/403/429, or an empty or
            non-JSON body.
        GlmOcrBadDocument: another 4xx (the service rejected the document).
    """
    resolved_base = base if base is not None else _base()
    if not resolved_base:
        raise GlmOcrUnavailable("WISEAU_GLM_OCR_BASE is not configured")
    resolved_base = resolved_base.rstrip("/")

    body, content_type = _encode_multipart(filename or "document", data)
    headers = {"Content-Type": content_type, "Accept": "application/json"}
    resolved_token = token if token is not None else _token()
    if resolved_token:
        headers["Authorization"] = f"Bearer {resolved_token}"
    resolved_api_key = api_key if api_key is not None else _api_key()
    if resolved_api_key:
        headers["X-Api-Key"] = resolved_api_key

    url = resolved_base + (path if path is not None else _path())
    request = urllib.request.Request(url, data=body, headers=headers, method="POST")
    send = transport if transport is not None else _default_transport
    resolved_timeout = timeout if timeout is not None else _timeout()

    try:
        raw = send(request, resolved_timeout)
    except urllib.error.HTTPError as exc:
        detail = _read_error_detail(exc)
        if exc.code in _INFRASTRUCTURE_4XX:
            raise GlmOcrUnavailable(f"GLM-OCR refused the request ({exc.code}): {detail}") from exc
        if 400 <= exc.code < 500:
            raise GlmOcrBadDocument(f"GLM-OCR rejected the document ({exc.code}): {detail}") from exc
        raise GlmOcrUnavailable(f"GLM-OCR error {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise GlmOcrUnavailable(f"could not reach GLM-OCR at {resolved_base}: {exc.reason}") from exc
    except (TimeoutError, OSError) as exc:
        raise GlmOcrUnavailable(f"GLM-OCR request failed: {exc}") from exc

    try:
        payload = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise GlmOcrUnavailable("GLM-OCR returned a non-JSON response") from exc

    markdown = _markdown_from_response(payload)
    if not markdown.strip():
        raise GlmOcrUnavailable("GLM-OCR returned an empty conversion")
    return markdown
