"""Thin HTTP client to a ``docling-serve`` converter (Phase 6; ADR-014/015/016).

docling produces markedly more faithful Markdown of complex, multi-column, and
scanned documents than the deterministic PyMuPDF/Mammoth path (ADR-014). It is
heavy (PyTorch + model weights), so it runs as a **separate microservice** — its
own Hugging Face Space (ADR-015) — and this backend reaches it only over HTTP at
``WISEAU_DOCLING_BASE``. The backend image therefore gains **no** docling/torch
dependency; its sole knowledge of docling is that base URL.

Design mirrors ``monitor.py``: a **stdlib-only** HTTP client (``urllib``) with an
injectable transport, so the backend image acquires no new runtime dependency and
can never fail to import because an HTTP library is missing (ADR-016). This is the
resilient default the project wants — docling is best-effort, and the caller
(``file_parser``) always has the deterministic parsers to fall back to.

**Typed errors let the caller distinguish "docling down" from "bad document".**
Both subclass :class:`DoclingError` so ``file_parser`` can fall back on either,
while logging which happened:

* :class:`DoclingUnavailable` — connection refused, timeout, 5xx (including the
  504 docling-serve raises past ``DOCLING_SERVE_MAX_SYNC_WAIT``), an auth or
  rate-limit rejection (401/403/429 — a *deployment* problem, not the document's
  fault), or a non-JSON/empty conversion. The docling Space is
  asleep/overloaded/misconfigured; a retry later might succeed. This is the
  common free-tier case (cold starts).
* :class:`DoclingBadDocument` — docling answered with another 4xx. It ran but
  rejected the input (unsupported/corrupt). A retry won't help; the fallback
  parser may still make sense of it, or surface the real error.

Configuration (all optional; when ``WISEAU_DOCLING_BASE`` is unset the client is
"not configured" and callers skip straight to the deterministic parsers):

* ``WISEAU_DOCLING_BASE``  — base URL of the docling-serve Space, e.g.
  ``https://user-docling.hf.space``. Unset ⇒ docling disabled.
* ``WISEAU_DOCLING_TOKEN`` — sent as ``Authorization: Bearer``. This is the
  *gateway* credential: a Hugging Face token when Space #2 is private, so only
  this backend gets past the platform (ADR-015).
* ``WISEAU_DOCLING_API_KEY`` — sent as ``X-Api-Key``. This is docling-serve's
  *own* app-level guard (``DOCLING_SERVE_API_KEY``); it is a different mechanism
  from the bearer token above, and either, both, or neither may be in use.
* ``WISEAU_DOCLING_TIMEOUT`` — per-request timeout in seconds (default 120; CPU
  inference is slow).
* ``WISEAU_DOCLING_PATH``  — convert endpoint path (default ``/v1/convert/file``).

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

# A transport turns a prepared request into raw response bytes. Injectable so
# tests can mock docling-serve without a live Space (roadmap: mocked transport).
Transport = Callable[[urllib.request.Request, float], bytes]

DEFAULT_TIMEOUT = 120.0
DEFAULT_PATH = "/v1/convert/file"


class DoclingError(RuntimeError):
    """Base class for any docling conversion failure — the caller falls back."""


class DoclingUnavailable(DoclingError):
    """docling could not be reached or did not return a usable conversion.

    Connection error, timeout, 5xx, or an empty/non-JSON body — i.e. an
    *infrastructure* problem (asleep/overloaded/broken Space), not a verdict on
    the document. Common on the free CPU tier's cold starts.
    """


class DoclingBadDocument(DoclingError):
    """docling ran but rejected the document (a 4xx response).

    The input was unsupported or corrupt; retrying docling won't help. Distinct
    from :class:`DoclingUnavailable` so outages and bad inputs are logged apart.
    """


# 4xx codes that are *not* a verdict on the document: a wrong/missing credential
# or a rate limit is a deployment problem, and logging it as "bad document"
# would send the next instance hunting the wrong bug.
_INFRASTRUCTURE_4XX = frozenset({401, 403, 429})


# --- Configuration ----------------------------------------------------------
def _base() -> str:
    return os.environ.get("WISEAU_DOCLING_BASE", "").strip().rstrip("/")


def _token() -> str:
    return os.environ.get("WISEAU_DOCLING_TOKEN", "").strip()


def _api_key() -> str:
    return os.environ.get("WISEAU_DOCLING_API_KEY", "").strip()


def _timeout() -> float:
    try:
        return float(os.environ.get("WISEAU_DOCLING_TIMEOUT", str(DEFAULT_TIMEOUT)))
    except ValueError:
        return DEFAULT_TIMEOUT


def _path() -> str:
    path = os.environ.get("WISEAU_DOCLING_PATH", DEFAULT_PATH).strip() or DEFAULT_PATH
    return path if path.startswith("/") else "/" + path


def is_configured() -> bool:
    """True when a docling-serve base URL is set, so docling may be attempted."""
    return bool(_base())


# --- HTTP plumbing ----------------------------------------------------------
def _default_transport(request: urllib.request.Request, timeout: float) -> bytes:
    with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 - fixed backend URL
        return response.read()


def _encode_multipart(filename: str, data: bytes) -> tuple[bytes, str]:
    """Encode ``data`` as a ``multipart/form-data`` body for docling-serve.

    Sends the document under the ``files`` field and requests Markdown output
    (``to_formats=md``). The boundary is derived from a hash of the bytes so it
    is deterministic yet cannot collide with the document's own content.
    """
    boundary = "wiseau" + hashlib.sha256(data).hexdigest()[:32]
    safe_name = filename.replace('"', "").replace("\r", "").replace("\n", "") or "document"
    marker = f"--{boundary}".encode("ascii")

    body = b"\r\n".join(
        [
            marker,
            b'Content-Disposition: form-data; name="to_formats"',
            b"",
            b"md",
            marker,
            (
                b'Content-Disposition: form-data; name="files"; '
                b'filename="' + safe_name.encode("utf-8") + b'"'
            ),
            b"Content-Type: application/octet-stream",
            b"",
            data,
            f"--{boundary}--".encode("ascii"),
            b"",
        ]
    )
    return body, f"multipart/form-data; boundary={boundary}"


def _read_error_detail(exc: urllib.error.HTTPError) -> str:
    """Pull a human-readable detail out of a docling error body, best-effort."""
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
    """Extract the Markdown string from a docling-serve JSON response.

    docling-serve returns ``{"document": {"md_content": "..."}}``; a few flatter
    shapes are tolerated so a minor server-version change doesn't break the path.
    """
    if isinstance(payload, dict):
        document = payload.get("document")
        if isinstance(document, dict):
            for key in ("md_content", "markdown", "text_content"):
                value = document.get(key)
                if isinstance(value, str) and value.strip():
                    return value
        for key in ("md_content", "markdown"):
            value = payload.get(key)
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
    """Convert ``data`` to Markdown via docling-serve; return the raw Markdown.

    The result is **not** normalized — the caller runs ``clean_markdown()``.

    Args:
        data: The document bytes (PDF/DOCX/image) to convert.
        filename: Original filename; forwarded so docling can sniff the format.
        base/token/api_key/timeout/path: Override the corresponding env config
            (mainly for tests). ``None`` ⇒ read from the environment.
        transport: Injected request→bytes transport (mainly for tests). ``None``
            ⇒ the real ``urllib`` transport.

    Raises:
        DoclingUnavailable: docling unreachable/timed out/5xx/401/403/429, or it
            returned an empty or non-JSON body.
        DoclingBadDocument: docling returned another 4xx (rejected the document).
    """
    resolved_base = base if base is not None else _base()
    if not resolved_base:
        raise DoclingUnavailable("WISEAU_DOCLING_BASE is not configured")
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
            raise DoclingUnavailable(f"docling refused the request ({exc.code}): {detail}") from exc
        if 400 <= exc.code < 500:
            raise DoclingBadDocument(f"docling rejected the document ({exc.code}): {detail}") from exc
        raise DoclingUnavailable(f"docling error {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise DoclingUnavailable(f"could not reach docling at {resolved_base}: {exc.reason}") from exc
    except (TimeoutError, OSError) as exc:  # socket timeout / low-level I/O error
        raise DoclingUnavailable(f"docling request failed: {exc}") from exc

    try:
        payload = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise DoclingUnavailable("docling returned a non-JSON response") from exc

    markdown = _markdown_from_response(payload)
    if not markdown.strip():
        raise DoclingUnavailable("docling returned an empty conversion")
    return markdown
