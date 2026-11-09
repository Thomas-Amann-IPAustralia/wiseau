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
fast native path. A scan that carries only an *invisible* OCR text layer (another
engine's earlier guess, e.g. Acrobat Paper Capture) counts as a scan and is OCR'd
from its pixels; its layer is not trusted (ADR-040). So a mixed PDF (digital pages
plus scanned inserts) is assembled page by page in order, OCR-ing only what it
must. Plain image uploads (PNG/JPEG/TIFF/...) are OCR'd through the same engine.

Two determinism-critical choices (see ADR-012):

* PyMuPDF4LLM runs in its **legacy layout mode** (`use_layout(False)`). The 1.28
  layout engine accumulates cross-call state that non-deterministically drops
  content; the legacy path is stable and byte-reproducible.
* OCR uses MuPDF's own OCR primitive (via `ocr.py`), **not** PyMuPDF4LLM's OCR
  integration, which has the same cross-call instability.

**The document IR (ADR-043).** Every path builds a `document.Document` (elements
in reading order, each with its page, box when known, and source engine) and
`serializer.to_markdown()` turns it into the Markdown string, which is
byte-for-byte what these paths returned before the IR existed. `file_to_document`
returns the IR itself; `file_to_markdown` is that, serialized and normalized.

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

from . import docling_client, glm_ocr_client, serializer
from . import document as ir
from .cleaner import clean_markdown
from .ocr import get_engine, text_elements

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


# ADR-040: a scan whose only text is an invisible OCR layer (PDF text render mode 3,
# e.g. Acrobat "Paper Capture") is read from its pixels, not from that layer. On a
# random sample of such PBR pages the layer had lost whole tables (a third of the
# numbers), so it is not trusted. The harness turns this off to reproduce the old
# behaviour (`benchmarks/bakeoff/run_engines.py`).
OCR_INVISIBLE_LAYER = True

# How much of the page one image must cover for the page to count as a scan.
_SCAN_IMAGE_MIN_COVER = 0.9

# PDF text render mode 3: neither filled nor stroked, i.e. invisible text.
_RENDER_INVISIBLE = 3


def _invisible_layer_over_scan(page: pymupdf.Page) -> bool:
    """True when every character on the page is invisible and one image covers it.

    That is the shape of an OCR'd scan: the picture is what a reader sees and the
    invisible text is some earlier OCR engine's guess at it. A born-digital page
    has visible text; a page that merely hides a watermark or a few characters
    behind visible text has visible text too, so neither matches.
    """
    spans = page.get_texttrace()
    if not spans or any(s["type"] != _RENDER_INVISIBLE for s in spans):
        return False
    area = page.rect.width * page.rect.height
    if area <= 0:
        return False
    for info in page.get_image_info():
        x0, y0, x1, y1 = info["bbox"]
        if (x1 - x0) * (y1 - y0) / area >= _SCAN_IMAGE_MIN_COVER:
            return True
    return False


def _page_needs_ocr(page: pymupdf.Page) -> bool:
    """True when a page lacks a text layer worth reading (i.e. is scanned).

    That is a page with (essentially) no embedded text, or, under ADR-040, a scan
    whose only text is an invisible OCR layer.
    """
    if len(page.get_text("text").strip()) < _TEXT_LAYER_MIN_CHARS:
        return True
    return OCR_INVISIBLE_LAYER and _invisible_layer_over_scan(page)


def _image_page_indices(doc: pymupdf.Document, mode: str) -> set[int]:
    """The pages to read from pixels: every page under `force`, else image-only ones."""
    if mode == "force":
        return set(range(doc.page_count))
    # "auto" (default) and any unrecognized value
    return {i for i in range(doc.page_count) if _page_needs_ocr(doc[i])}


# The source name on elements read from a PDF's own text layer.
PYMUPDF4LLM = "pymupdf4llm"


def _native_page_markdown(doc: pymupdf.Document, index: int) -> str:
    """Legacy PyMuPDF4LLM Markdown for a single text-bearing page."""
    return pymupdf4llm.to_markdown(doc, pages=[index])


def _page_info(page: pymupdf.Page, method: str, source: str) -> ir.PageInfo:
    return ir.PageInfo(page.number + 1, method, source, page.rect.width, page.rect.height)


def _add_native_page(out: ir.Document, page: pymupdf.Page, markdown: str) -> None:
    out.add_page(_page_info(page, ir.TEXT_LAYER, PYMUPDF4LLM), serializer.split_markdown(markdown, PYMUPDF4LLM))


def _add_native_document(out: ir.Document, doc: pymupdf.Document) -> None:
    """Every page from its text layer, in one PyMuPDF4LLM call.

    One call, not one per page, because PyMuPDF4LLM sizes headings from the font
    statistics of all the pages it is given; `page_chunks=True` only keeps each
    page's Markdown apart (their concatenation is the single-string output).
    """
    for chunk, page in zip(pymupdf4llm.to_markdown(doc, page_chunks=True), doc):
        _add_native_page(out, page, chunk["text"])


def _ocr_elements(engine, page: pymupdf.Page, *, dpi: int, language: str) -> list[ir.Element]:
    """An OCR engine's reading of a page as IR elements (any engine with `ocr_page`)."""
    read = getattr(engine, "read_elements", None)
    if read is not None:
        return read(page, dpi=dpi, language=language)
    return text_elements(engine.ocr_page(page, dpi=dpi, language=language), source=engine.name)


def _add_ocr_page(out: ir.Document, engine, page: pymupdf.Page, *, dpi: int, language: str) -> None:
    out.add_page(_page_info(page, ir.OCR, engine.name), _ocr_elements(engine, page, dpi=dpi, language=language))


def pdf_to_document(data: bytes) -> ir.Document:
    """Convert PDF bytes to the document IR, OCR-ing scanned pages as configured.

    Records its own engine attribution, because only this function knows whether
    the text came off the page's own layer (`pymupdf`) or out of the OCR engine
    (`ocr`) — a scanned PDF billed to `pymupdf` would hide OCR entirely from
    `GET /metrics`. A document with *any* OCR'd page counts as `ocr`, so one
    conversion is still attributed to exactly one engine.
    """
    out = ir.Document()
    doc = pymupdf.open(stream=data, filetype="pdf")
    try:
        mode = _ocr_mode()
        # OCR disabled: native extraction only (scanned pages yield nothing).
        if mode == "off":
            metrics.record_engine("pymupdf")
            _add_native_document(out, doc)
            return out

        page_count = doc.page_count
        ocr_pages = _image_page_indices(doc, mode)

        # Fast path: nothing needs OCR — identical to the pre-OCR behaviour.
        if not ocr_pages:
            metrics.record_engine("pymupdf")
            _add_native_document(out, doc)
            return out

        metrics.record_engine("ocr")
        engine = _engine()
        dpi, lang = _ocr_dpi(), _ocr_lang()

        # Every page is OCR'd, or mixed: native Markdown for text pages (each
        # converted on its own, as before), OCR for scanned pages, in page order.
        for i in range(page_count):
            if i in ocr_pages:
                _add_ocr_page(out, engine, doc[i], dpi=dpi, language=lang)
            else:
                _add_native_page(out, doc[i], _native_page_markdown(doc, i))
        return out
    finally:
        if not doc.is_closed:
            doc.close()


def pdf_to_markdown(data: bytes) -> str:
    """Convert PDF bytes to Markdown (the serialized `pdf_to_document`)."""
    return serializer.to_markdown(pdf_to_document(data))


def image_to_document(data: bytes, ext: str) -> ir.Document:
    """OCR a standalone image (PNG/JPEG/TIFF/...) into the document IR."""
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
    out = ir.Document()
    doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
    try:
        engine = _engine()
        dpi, lang = _ocr_dpi(), _ocr_lang()
        for page in doc:
            _add_ocr_page(out, engine, page, dpi=dpi, language=lang)
        return out
    finally:
        if not doc.is_closed:
            doc.close()


def image_to_markdown(data: bytes, ext: str) -> str:
    """OCR a standalone image (PNG/JPEG/TIFF/...) into text."""
    return serializer.to_markdown(image_to_document(data, ext))


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


def _markdown_document(markdown: str, source: str) -> ir.Document:
    """Whole-document Markdown from an engine that writes it, as IR blocks (no pages)."""
    out = ir.Document()
    out.extend(serializer.split_markdown(markdown, source))
    return out


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


def _fallback_document(data: bytes, ext: str) -> ir.Document:
    """Deterministic extraction by extension — the always-available fallback.

    Each parser records the engine that actually served the conversion; a scanned
    PDF and a born-digital one both arrive here but are attributed differently.
    """
    if ext == ".pdf":
        return pdf_to_document(data)
    if ext == ".docx":
        return _markdown_document(docx_to_markdown(data), "mammoth")
    # Only reachable for image extensions; other types are rejected upstream.
    return image_to_document(data, ext)


def _extract_document(data: bytes, filename: str, ext: str, engine: str | None = None) -> ir.Document:
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
        return _fallback_document(data, ext)
    client, error_type = remote
    if not client.is_configured():
        metrics.record_remote_skipped(selected, "not_configured")
        return _fallback_document(data, ext)
    if hasattr(client, "supports") and not client.supports(filename):
        metrics.record_remote_skipped(selected, "unsupported_type")
        return _fallback_document(data, ext)

    if selected == "glm-ocr" and ext == ".pdf":
        return _glm_ocr_pdf(data, filename, client, error_type)

    markdown = _call_remote(selected, client, error_type, data, filename)
    if markdown is None:
        return _fallback_document(data, ext)
    metrics.record_engine(selected)
    return _markdown_document(markdown, selected)


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


def _glm_ocr_pdf(data: bytes, filename: str, client, error_type) -> ir.Document:
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
            return pdf_to_document(data)
        if len(image_pages) == doc.page_count:
            markdown = _call_remote("glm-ocr", client, error_type, data, filename)
            if markdown is None:
                return pdf_to_document(data)
            metrics.record_engine("glm-ocr")
            return _markdown_document(markdown, "glm-ocr")

        out = ir.Document()
        used_glm = False
        for is_image, pages in _page_runs(doc.page_count, image_pages):
            if not is_image:
                for i in pages:
                    _add_native_page(out, doc[i], _native_page_markdown(doc, i))
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
                for i in pages:
                    _add_ocr_page(out, engine, doc[i], dpi=dpi, language=lang)
                continue
            used_glm = True
            # GLM-OCR returns one Markdown string for the run, so its blocks are
            # not tied to a page; the pages it covered are still listed.
            for i in pages:
                out.pages.append(_page_info(doc[i], ir.REMOTE, "glm-ocr"))
            out.extend(serializer.split_markdown(markdown, "glm-ocr"))
        # One conversion, one engine: GLM-OCR if it read any page, else local OCR.
        metrics.record_engine("glm-ocr" if used_glm else "ocr")
        return out
    finally:
        if not doc.is_closed:
            doc.close()


def file_to_document(data: bytes, filename: str, engine: str | None = None) -> ir.Document:
    """Convert an uploaded document to the document IR (ADR-043), by extension.

    Same arguments and errors as `file_to_markdown`. The elements' text is what
    each engine produced, before the shared normalizer runs.
    """
    ext = os.path.splitext(filename)[1].lower()
    if ext not in SUPPORTED_EXTENSIONS:
        supported = ", ".join(sorted(SUPPORTED_EXTENSIONS))
        raise ValueError(f"Unsupported file type '{ext or 'unknown'}'. Supported: {supported}.")
    return _extract_document(data, filename, ext, engine)


def file_to_markdown(data: bytes, filename: str, engine: str | None = None) -> str:
    """Dispatch an uploaded document to the right parser by extension.

    The document is converted to the IR (`file_to_document`), serialized
    (`serializer.to_markdown`) and normalized (`clean_markdown`).

    Args:
        data: The document bytes.
        filename: Original filename; its extension selects the parser.
        engine: Optional per-request engine override (`"docling"`/`"glm-ocr"`/`"pymupdf"`);
            `None` uses this deployment's default. Validate caller input with
            `resolve_engine` first.
    """
    return clean_markdown(serializer.to_markdown(file_to_document(data, filename, engine)))
