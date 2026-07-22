"""Document -> Markdown extraction for PDF, DOCX, and image uploads.

PDFs are converted with PyMuPDF4LLM (LLM-tuned Markdown output); DOCX files are
converted to HTML with Mammoth and then to Markdown with Markdownify. Both paths
finish in the shared `clean_markdown` normalizer for uniform output.

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
* OCR is done by a pluggable engine (`ocr.py`; default RapidOCR, else Tesseract),
  **not** PyMuPDF4LLM's OCR integration, which has the same cross-call instability.

Behaviour is controlled by env vars: `WISEAU_OCR_MODE` (`auto`/`force`/`off`),
`WISEAU_OCR_DPI`, `WISEAU_OCR_LANG`, `WISEAU_OCR_ENGINE`.
"""

from __future__ import annotations

import importlib.util
import io
import os

import mammoth
import pymupdf  # provided by pymupdf4llm; formerly imported as `fitz`
import pymupdf4llm
from markdownify import markdownify as html_to_md

from .cleaner import clean_markdown
from .ocr import get_engine

# Pin PyMuPDF4LLM to its deterministic legacy extractor (see module docstring /
# ADR-012). This is a process-wide setting; do it once at import.
pymupdf4llm.use_layout(False)

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp", ".gif"}
SUPPORTED_EXTENSIONS = {".pdf", ".docx"} | IMAGE_EXTENSIONS

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


def _default_engine_name() -> str:
    """RapidOCR when its (ONNX) deps are installed, else the zero-dependency
    Tesseract fallback — so a lean deploy still OCRs without a hard failure."""
    if importlib.util.find_spec("rapidocr_onnxruntime") is not None:
        return "rapidocr"
    return "tesseract"


def _engine():
    name = os.environ.get("WISEAU_OCR_ENGINE") or _default_engine_name()
    return get_engine(name)


def _page_needs_ocr(page: pymupdf.Page) -> bool:
    """True when a page lacks a usable embedded text layer (i.e. is scanned)."""
    return len(page.get_text("text").strip()) < _TEXT_LAYER_MIN_CHARS


def _native_page_markdown(doc: pymupdf.Document, index: int) -> str:
    """Legacy PyMuPDF4LLM Markdown for a single text-bearing page."""
    return pymupdf4llm.to_markdown(doc, pages=[index])


def pdf_to_markdown(data: bytes) -> str:
    """Convert PDF bytes to Markdown, OCR-ing scanned pages as configured."""
    doc = pymupdf.open(stream=data, filetype="pdf")
    try:
        mode = _ocr_mode()
        # OCR disabled: native extraction only (scanned pages yield nothing).
        if mode == "off":
            return pymupdf4llm.to_markdown(doc)

        page_count = doc.page_count
        if mode == "force":
            ocr_pages = set(range(page_count))
        else:  # "auto" (default) and any unrecognized value
            ocr_pages = {i for i in range(page_count) if _page_needs_ocr(doc[i])}

        # Fast path: nothing needs OCR — identical to the pre-OCR behaviour.
        if not ocr_pages:
            return pymupdf4llm.to_markdown(doc)

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


def docx_to_markdown(data: bytes) -> str:
    """Convert DOCX bytes to Markdown via Mammoth + Markdownify."""
    result = mammoth.convert_to_html(io.BytesIO(data))
    return html_to_md(result.value, heading_style="ATX")


def file_to_markdown(data: bytes, filename: str) -> str:
    """Dispatch an uploaded document to the right parser by extension."""
    ext = os.path.splitext(filename)[1].lower()
    if ext == ".pdf":
        raw = pdf_to_markdown(data)
    elif ext == ".docx":
        raw = docx_to_markdown(data)
    elif ext in IMAGE_EXTENSIONS:
        raw = image_to_markdown(data, ext)
    else:
        supported = ", ".join(sorted(SUPPORTED_EXTENSIONS))
        raise ValueError(f"Unsupported file type '{ext or 'unknown'}'. Supported: {supported}.")

    return clean_markdown(raw)
