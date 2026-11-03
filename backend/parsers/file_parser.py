"""Document -> Markdown extraction for PDF, DOCX, and image uploads.

**Engine selection (Phase 6; ADR-014, amended by ADR-027).** `WISEAU_PDF_ENGINE`
picks the deployment's default engine, and that default is **`pymupdf`** — the
fast, deterministic local parser — because most documents convert well with it in
about a second, where docling on a free CPU Space costs tens of seconds to
minutes. docling remains a first-class engine: set `WISEAU_PDF_ENGINE=docling` to
make it the deployment default, or ask for it per request (`engine="docling"`;
ADR-025) when a particular document needs the fidelity.

When docling *is* selected **and** a docling-serve Space is configured
(`WISEAU_DOCLING_BASE`), the bytes are sent to docling for high-fidelity
Markdown; on any docling failure — unreachable, timeout, 5xx, empty, or a
rejected document — the parser **logs and falls back** to the deterministic
parsers below, so asking for docling can never turn an outage into a failed
conversion. docling's output may vary run-to-run **by design** (ADR-013); the
default path stays deterministic.

The deterministic default (and docling's fallback): PDFs are converted with
PyMuPDF4LLM (LLM-tuned Markdown output); DOCX files are converted to HTML with
Mammoth and then to Markdown with Markdownify. Every path — docling included —
finishes in the shared `clean_markdown` normalizer for uniform output
(invariant #3).

**OCR.** A born-digital PDF carries a text layer that PyMuPDF4LLM reads directly.
Scanned and handwritten PDFs do not — their pages are images. Detection is
per-page: a page with (essentially) no extractable text is rasterized and OCR'd
by the configured engine (see `ocr.py`); pages with a real text layer keep the
fast native path. So a mixed PDF (digital pages plus scanned inserts) is
assembled page by page in order, OCR-ing only what it must. Plain image uploads
(PNG/JPEG/TIFF/...) are OCR'd through the same engine.

Two determinism-critical choices (see ADR-012):

* PyMuPDF4LLM runs in its **legacy layout mode** (`use_layout(False)`). The 1.28
  layout engine accumulates cross-call state that non-deterministically drops
  content; the legacy path is stable and byte-reproducible.
* OCR uses MuPDF's own OCR primitive (via `ocr.py`), **not** PyMuPDF4LLM's OCR
  integration, which has the same cross-call instability.

Behaviour is controlled by env vars: `WISEAU_OCR_MODE` (`auto`/`force`/`off`),
`WISEAU_OCR_DPI`, `WISEAU_OCR_LANG`, `WISEAU_OCR_ENGINE`.
"""

from __future__ import annotations

import io
import logging
import os
import time

import mammoth
import pymupdf  # provided by pymupdf4llm; formerly imported as `fitz`
import pymupdf4llm
from markdownify import markdownify as html_to_md
from observability import metrics

from . import docling_client, glm_ocr_client
from .cleaner import clean_markdown
from .ocr import get_engine

logger = logging.getLogger(__name__)

# Pin PyMuPDF4LLM to its deterministic legacy extractor (see module docstring /
# ADR-012). This is a process-wide setting; do it once at import.
pymupdf4llm.use_layout(False)

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp", ".gif"}
SUPPORTED_EXTENSIONS = {".pdf", ".docx"} | IMAGE_EXTENSIONS

# Engines a caller may name on a request (ADR-025). `auto` is accepted too and
# means "this deployment's default", i.e. whatever `WISEAU_PDF_ENGINE` says.
# `glm-ocr` is the third engine (ADR-034): opt-in, remote, with the same
# automatic fallback docling has.
REQUESTABLE_ENGINES = frozenset({"docling", "glm-ocr", "pymupdf"})

# The engines that run as a separate service and therefore need a fallback.
# Each entry: (client module, the base error class that triggers the fallback).
_REMOTE_ENGINES = {
    "docling": (docling_client, docling_client.DoclingError),
    "glm-ocr": (glm_ocr_client, glm_ocr_client.GlmOcrError),
}

# Resolution (dots per inch) for rasterizing a page before OCR. 300 DPI is the
# accuracy sweet spot; a fixed constant (overridable via env) so the same page
# always renders to the same image and thus the same recognized text.
DEFAULT_OCR_DPI = 300

# OCR recognition language. Tesseract ISO 639-2/T code(s); "+"-join for multiple
# (e.g. "eng+deu"). The matching data must be installed for the chosen engine.
DEFAULT_OCR_LANG = "eng"

# A page with fewer than this many non-whitespace characters of embedded text is
# treated as image-only (needs OCR). Large enough to ignore a stray character or
# two leaked by a scan, small enough not to trip a genuinely sparse digital page.
_TEXT_LAYER_MIN_CHARS = 16


def _ocr_mode() -> str:
    """OCR policy: 'auto' (default; OCR pages that need it), 'force' (OCR every
    page), or 'off' (never OCR — native text layer only)."""
    return os.environ.get("WISEAU_OCR_MODE", "auto").strip().lower()


def _ocr_dpi() -> int:
    try:
        return int(os.environ.get("WISEAU_OCR_DPI", str(DEFAULT_OCR_DPI)))
    except ValueError:
        return DEFAULT_OCR_DPI


def _ocr_lang() -> str:
    return os.environ.get("WISEAU_OCR_LANG", DEFAULT_OCR_LANG)


def _engine():
    return get_engine(os.environ.get("WISEAU_OCR_ENGINE", "tesseract"))


def _page_needs_ocr(page: pymupdf.Page) -> bool:
    """True when a page lacks a usable embedded text layer (i.e. is scanned)."""
    return len(page.get_text("text").strip()) < _TEXT_LAYER_MIN_CHARS


def _image_page_indices(doc: pymupdf.Document, mode: str) -> set[int]:
    """The pages to read from pixels: every page under `force`, else image-only ones."""
    if mode == "force":
        return set(range(doc.page_count))
    # "auto" (default) and any unrecognized value
    return {i for i in range(doc.page_count) if _page_needs_ocr(doc[i])}


def _native_page_markdown(doc: pymupdf.Document, index: int) -> str:
    """Legacy PyMuPDF4LLM Markdown for a single text-bearing page."""
    return pymupdf4llm.to_markdown(doc, pages=[index])


def pdf_to_markdown(data: bytes) -> str:
    """Convert PDF bytes to Markdown, OCR-ing scanned pages as configured.

    Records its own engine attribution, because only this function knows whether
    the text came off the page's own layer (`pymupdf`) or out of the OCR engine
    (`ocr`) — a scanned PDF billed to `pymupdf` would hide OCR entirely from
    `GET /metrics`. A document with *any* OCR'd page counts as `ocr`, so one
    conversion is still attributed to exactly one engine.
    """
    doc = pymupdf.open(stream=data, filetype="pdf")
    try:
        mode = _ocr_mode()
        # OCR disabled: native extraction only (scanned pages yield nothing).
        if mode == "off":
            metrics.record_engine("pymupdf")
            return pymupdf4llm.to_markdown(doc)

        page_count = doc.page_count
        ocr_pages = _image_page_indices(doc, mode)

        # Fast path: nothing needs OCR — identical to the pre-OCR behaviour.
        if not ocr_pages:
            metrics.record_engine("pymupdf")
            return pymupdf4llm.to_markdown(doc)

        metrics.record_engine("ocr")
        engine = _engine()
        dpi, lang = _ocr_dpi(), _ocr_lang()

        # Whole document is scanned: OCR every page.
        if len(ocr_pages) == page_count:
            parts = [engine.ocr_page(doc[i], dpi=dpi, language=lang).strip() for i in range(page_count)]
            return "\n\n".join(p for p in parts if p)

        # Mixed: native Markdown for text pages, OCR for scanned pages, in order.
        parts = []
        for i in range(page_count):
            if i in ocr_pages:
                part = engine.ocr_page(doc[i], dpi=dpi, language=lang)
            else:
                part = _native_page_markdown(doc, i)
            if part and part.strip():
                parts.append(part.strip())
        return "\n\n".join(parts)
    finally:
        if not doc.is_closed:
            doc.close()


def image_to_markdown(data: bytes, ext: str) -> str:
    """OCR a standalone image (PNG/JPEG/TIFF/...) into text."""
    if _ocr_mode() == "off":
        raise ValueError("OCR is disabled (WISEAU_OCR_MODE=off); cannot read image files.")
    metrics.record_engine("ocr")
    # Re-wrap the image as a single-page PDF so the OCR engine sees a normal page.
    src = pymupdf.open(stream=data, filetype=ext.lstrip("."))
    try:
        pdf_bytes = src.convert_to_pdf()
    finally:
        if not src.is_closed:
            src.close()
    doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
    try:
        engine = _engine()
        dpi, lang = _ocr_dpi(), _ocr_lang()
        parts = [engine.ocr_page(doc[i], dpi=dpi, language=lang).strip() for i in range(doc.page_count)]
        return "\n\n".join(p for p in parts if p)
    finally:
        if not doc.is_closed:
            doc.close()


@mammoth.images.img_element
def _image_without_payload(image) -> dict:
    """Render an embedded DOCX image as an empty-target `<img>` (ADR-024).

    Mammoth's default image handler is `mammoth.images.data_uri`, which inlines
    every picture as a base64 data URI — one screenshot in a Word document then
    contributes tens of thousands of unreadable characters to the Markdown, often
    more than the document's entire text. Emitting the element without a source
    keeps the fact of the image (and its alt text, which Mammoth attaches on its
    own) while leaving the payload out of the string entirely. `clean_markdown`
    catches any data URI that reaches it from another extractor; this keeps the
    DOCX path from building one in the first place.
    """
    return {"src": ""}


def docx_to_markdown(data: bytes) -> str:
    """Convert DOCX bytes to Markdown via Mammoth + Markdownify."""
    metrics.record_engine("mammoth")
    result = mammoth.convert_to_html(io.BytesIO(data), convert_image=_image_without_payload)
    return html_to_md(result.value, heading_style="ATX")


def _pdf_engine() -> str:
    """Preferred document engine: 'pymupdf' (default; ADR-027), 'docling', or 'glm-ocr'.

    The default is the fast local parser: it answers in about a second and is
    deterministic, which is the right trade for the common document. docling's
    fidelity is opt-in — per deployment via this variable, or per request via
    `engine="docling"` (ADR-025).
    """
    return os.environ.get("WISEAU_PDF_ENGINE", "pymupdf").strip().lower()


def default_engine() -> str:
    """The engine an `auto` request resolves to on this deployment (ADR-027).

    Reported by `GET /ping` so a client can label its "Auto" option and size its
    progress estimate without hard-coding a default that a deployment may have
    changed. Anything other than a remote engine's name (`docling`, `glm-ocr`) in
    `WISEAU_PDF_ENGINE` runs the local parser, so that is what an unrecognized
    value reports too — the answer describes what will actually happen, not what
    was typed.
    """
    selected = _pdf_engine()
    return selected if selected in _REMOTE_ENGINES else "pymupdf"


def available_engines() -> list[str]:
    """The engines that would actually run here if a request named them (ADR-034).

    `REQUESTABLE_ENGINES` is what this *build* accepts; a remote engine whose
    service is not configured is still accepted — and silently falls back — so a
    client that offered it would be offering a choice that does nothing. `/ping`
    publishes this list so a UI can show only the engines this deployment has.
    The local parser is always available.
    """
    return sorted(["pymupdf", *(name for name, (client, _) in _REMOTE_ENGINES.items() if client.is_configured())])


def resolve_engine(requested: str | None) -> str | None:
    """Validate a caller-supplied engine choice (ADR-025).

    The API lets a caller override this deployment's default per request — the
    UI exposes it as "highest fidelity" vs "fastest", because docling on free CPU
    can take a minute where the deterministic parser takes a second.

    Args:
        requested: `"docling"`, `"glm-ocr"`, `"pymupdf"`, `"auto"`/empty (defer to
            `WISEAU_PDF_ENGINE`), in any case.

    Returns:
        The engine name to use, or `None` for "whatever the deployment defaults
        to" — kept distinct so a request never pins an engine it did not ask for.

    Raises:
        ValueError: the value is not one this build knows how to run.
    """
    value = (requested or "").strip().lower()
    if value in {"", "auto", "default"}:
        return None
    if value not in REQUESTABLE_ENGINES:
        supported = ", ".join(sorted(REQUESTABLE_ENGINES | {"auto"}))
        raise ValueError(f"Unknown engine '{requested}'. Supported: {supported}.")
    return value


def _fallback_markdown(data: bytes, ext: str) -> str:
    """Deterministic extraction by extension — the always-available fallback.

    Each parser records the engine that actually served the conversion; a scanned
    PDF and a born-digital one both arrive here but are attributed differently.
    """
    if ext == ".pdf":
        return pdf_to_markdown(data)
    if ext == ".docx":
        return docx_to_markdown(data)
    # Only reachable for image extensions; other types are rejected upstream.
    return image_to_markdown(data, ext)


def _extract_markdown(data: bytes, filename: str, ext: str, engine: str | None = None) -> str:
    """Convert a supported document to Markdown, with a remote engine when selected.

    Tries a remote engine (docling, or GLM-OCR — ADR-034) only when it is
    *selected* (`WISEAU_PDF_ENGINE`, or a per-request `engine=` — the default is
    the fast local parser, ADR-027), *configured* (its base URL is set), and able
    to read the file type (GLM-OCR has no DOCX reader). Any failure — the service
    unreachable or a rejected document — is logged and degraded to the
    deterministic parser, so the service returns a working result rather than an
    error (ADR-014). That holds for an explicitly requested engine too: a caller
    asking for fidelity still gets a result.

    Every remote engine that did *not* run records why, so `GET /metrics` can tell
    "never asked for" from "asked for but not deployed" (ADR-019).
    """
    selected = engine or _pdf_engine()
    for name in _REMOTE_ENGINES:
        if name != selected:
            metrics.record_remote_skipped(name, "engine_not_selected")
    remote = _REMOTE_ENGINES.get(selected)
    if remote is None:
        return _fallback_markdown(data, ext)
    client, error_type = remote
    if not client.is_configured():
        metrics.record_remote_skipped(selected, "not_configured")
        return _fallback_markdown(data, ext)
    if hasattr(client, "supports") and not client.supports(filename):
        metrics.record_remote_skipped(selected, "unsupported_type")
        return _fallback_markdown(data, ext)

    if selected == "glm-ocr" and ext == ".pdf":
        return _glm_ocr_pdf(data, filename, client, error_type)

    markdown = _call_remote(selected, client, error_type, data, filename)
    if markdown is None:
        return _fallback_markdown(data, ext)
    metrics.record_engine(selected)
    return markdown


def _call_remote(selected: str, client, error_type, data: bytes, filename: str) -> str | None:
    """One call to a remote engine; None (logged and recorded) if it failed.

    `*Unavailable` means the *service* is the problem; `*BadDocument` means it read
    the document and refused it. They are recorded apart so a silent outage is
    distinguishable from a file the engine simply cannot handle (ADR-014/018).
    """
    started = time.perf_counter()
    try:
        markdown = client.convert_document(data, filename)
    except error_type as exc:
        reason = type(exc).__name__
        metrics.record_remote_attempt(selected, (time.perf_counter() - started) * 1000, ok=False, reason=reason)
        logger.warning(
            f"{selected} conversion failed; falling back to the deterministic parser",
            extra={"wiseau": {"engine": selected, "filename": filename, "reason": reason, "error": str(exc)}},
        )
        return None

    elapsed_ms = (time.perf_counter() - started) * 1000
    metrics.record_remote_attempt(selected, elapsed_ms, ok=True)
    logger.info(
        f"{selected} converted document",
        extra={
            "wiseau": {
                "engine": selected,
                "filename": filename,
                "chars": len(markdown),
                "duration_ms": round(elapsed_ms, 1),
            }
        },
    )
    return markdown


def _page_runs(page_count: int, image_pages: set[int]) -> list[tuple[bool, list[int]]]:
    """Split pages 0..n-1 into consecutive runs: (is_image_run, page indices)."""
    runs: list[tuple[bool, list[int]]] = []
    for i in range(page_count):
        is_image = i in image_pages
        if runs and runs[-1][0] == is_image:
            runs[-1][1].append(i)
        else:
            runs.append((is_image, [i]))
    return runs


def _glm_ocr_pdf(data: bytes, filename: str, client, error_type) -> str:
    """GLM-OCR for a PDF, page by page (ADR-036).

    GLM-OCR reads only pixels, so a page that already has a text layer is read
    worse by it (bake-off wer 0.297 against PyMuPDF's 0.019) and about 1,000
    times slower. Only image-only pages are sent to it:

    * **No image-only pages:** GLM-OCR is skipped (`no_image_pages`) and the local
      parser answers.
    * **All pages image-only:** the whole document goes in one request, as before.
    * **Mixed:** text pages are read from their text layer; each consecutive run
      of image-only pages goes to GLM-OCR as its own small PDF. The parts are
      joined in page order.

    A run that GLM-OCR fails on falls back to the local OCR engine for just those
    pages (ADR-014), so one failure never costs the pages that did convert.
    `WISEAU_OCR_MODE=force` sends every page to GLM-OCR.
    """
    doc = pymupdf.open(stream=data, filetype="pdf")
    try:
        image_pages = _image_page_indices(doc, _ocr_mode())
        if not image_pages:
            metrics.record_remote_skipped("glm-ocr", "no_image_pages")
            return pdf_to_markdown(data)
        if len(image_pages) == doc.page_count:
            markdown = _call_remote("glm-ocr", client, error_type, data, filename)
            if markdown is None:
                return pdf_to_markdown(data)
            metrics.record_engine("glm-ocr")
            return markdown

        parts: list[str] = []
        used_glm = False
        for is_image, pages in _page_runs(doc.page_count, image_pages):
            if not is_image:
                parts.extend(_native_page_markdown(doc, i) for i in pages)
                continue
            run = pymupdf.open()
            try:
                run.insert_pdf(doc, from_page=pages[0], to_page=pages[-1])
                run_bytes = run.tobytes()
            finally:
                run.close()
            markdown = _call_remote("glm-ocr", client, error_type, run_bytes, filename)
            if markdown is None:
                engine, dpi, lang = _engine(), _ocr_dpi(), _ocr_lang()
                markdown = "\n\n".join(engine.ocr_page(doc[i], dpi=dpi, language=lang).strip() for i in pages)
            else:
                used_glm = True
            parts.append(markdown)
        # One conversion, one engine: GLM-OCR if it read any page, else local OCR.
        metrics.record_engine("glm-ocr" if used_glm else "ocr")
        return "\n\n".join(p.strip() for p in parts if p and p.strip())
    finally:
        if not doc.is_closed:
            doc.close()


def file_to_markdown(data: bytes, filename: str, engine: str | None = None) -> str:
    """Dispatch an uploaded document to the right parser by extension.

    Args:
        data: The document bytes.
        filename: Original filename; its extension selects the parser.
        engine: Optional per-request engine override (`"docling"`/`"glm-ocr"`/`"pymupdf"`);
            `None` uses this deployment's default. Validate caller input with
            `resolve_engine` first.
    """
    ext = os.path.splitext(filename)[1].lower()
    if ext not in SUPPORTED_EXTENSIONS:
        supported = ", ".join(sorted(SUPPORTED_EXTENSIONS))
        raise ValueError(f"Unsupported file type '{ext or 'unknown'}'. Supported: {supported}.")

    raw = _extract_markdown(data, filename, ext, engine)
    return clean_markdown(raw)
