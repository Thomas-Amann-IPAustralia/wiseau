"""Pluggable OCR engines for scanned and handwritten pages.

`file_parser` renders/hands each image-only page to an engine and gets text
back; it stays ignorant of which engine ran. Two engines ship:

* **tesseract** (default) — MuPDF's built-in Tesseract, driven through
  ``Page.get_textpage_ocr``. It needs only the system ``tesseract`` binary plus
  language data (installed in the Dockerfile) — no extra Python package. It is
  fully deterministic and strong on printed/scanned text. This is what runs in
  the default image.

  Note: we deliberately do **not** use PyMuPDF4LLM's own OCR integration. In
  1.28 its layout+OCR engine carries state across calls that non-deterministically
  drops words (and even native text) — fatal for this project's determinism
  invariant. Driving MuPDF's OCR primitive directly is clean and reproducible;
  see ADR-012.

* **easyocr** (opt-in) — a neural engine that additionally handles *handwriting*
  and messy real-world captures. It pulls heavy dependencies (PyTorch), so it is
  kept out of the default image and enabled with ``WISEAU_OCR_ENGINE=easyocr``
  after installing ``requirements-ocr.txt``. RNG seeds are pinned and it runs in
  CPU inference mode, so output stays reproducible for fixed model weights.

Determinism is the product (``CLAUDE.md`` invariant #1): both engines are
configured for reproducible output — identical page pixels yield identical text.
Engines are selected by ``WISEAU_OCR_ENGINE`` and built lazily as process-wide
singletons, so a heavy neural model loads at most once per worker.
"""

from __future__ import annotations

import threading
from abc import ABC, abstractmethod
from functools import lru_cache

import pymupdf

from . import borders, photo_regions, tables, tesseract_layout
from . import document as ir
from .reading_order import Line, order_paragraphs, paragraph_text
from .serializer import render_elements

# Drop OCR noise read out of photographs (ADR-037). False restores the ADR-035
# behaviour, which the bake-off keeps as a baseline.
SKIP_PHOTO_REGIONS = True

# Read scans with Tesseract's own page layout, table rows stitched back
# (ADR-038), keeping the ADR-035/037 text as a guard against dropped text.
# False restores the ADR-037 path, which the bake-off keeps as a baseline.
TESSERACT_LAYOUT = True

# The ADR-038 guard counts only plausible words, not OCR speckle (ADR-042). False
# restores the raw count, which the bake-off keeps as a baseline.
PLAUSIBLE_GUARD = True

# Paint out solid scanner bands at the page edges before OCR (borders.py), so
# their speckle is not read as junk words. False restores the path before it,
# which the bake-off keeps as a baseline.
BORDER_CLEANUP = True

# EasyOCR uses ISO 639-1 short codes ("en"); the rest of the pipeline speaks
# Tesseract's 639-2/T codes ("eng"). Translate the common cases so one env var
# (`WISEAU_OCR_LANG`) configures either engine.
_TESSERACT_TO_EASYOCR_LANG = {
    "eng": "en",
    "deu": "de",
    "fra": "fr",
    "spa": "es",
    "ita": "it",
    "por": "pt",
    "nld": "nl",
}


class OcrEngine(ABC):
    """An OCR backend: a PDF page in, recognized text out.

    Implementations must be deterministic — identical page pixels rendered at the
    same DPI must yield identical text, independent of any prior call.
    """

    name: str

    @abstractmethod
    def ocr_page(self, page: pymupdf.Page, *, dpi: int, language: str) -> str:
        """Return the text recognized on ``page`` (rasterized at ``dpi``)."""

    def read_elements(self, page: pymupdf.Page, *, dpi: int, language: str) -> list[ir.Element]:
        """The page as IR elements in reading order (ADR-043).

        An engine that knows no more than its text returns it as one element.
        """
        return text_elements(self.ocr_page(page, dpi=dpi, language=language), source=self.name)


def text_elements(text: str, *, source: str) -> list[ir.Element]:
    """A page's whole text as one element, or none when it is blank."""
    return [ir.Element(type=ir.TEXT, text=text, source=source)] if text and text.strip() else []


class TesseractEngine(OcrEngine):
    """Default engine: MuPDF's built-in Tesseract via ``get_textpage_ocr``.

    Deterministic and lightweight (system binary only). Great on printed/scanned
    text; weak on cursive handwriting — use the EasyOCR engine for that.
    """

    name = "tesseract"

    def ocr_page(self, page: pymupdf.Page, *, dpi: int, language: str) -> str:
        return render_elements(self.read_elements(page, dpi=dpi, language=language))

    def read_elements(self, page: pymupdf.Page, *, dpi: int, language: str) -> list[ir.Element]:
        # Every pass below reads the same cleaned pixels: a guard that still saw the
        # junk could otherwise out-count, and override, the cleaned text.
        cleaned = borders.cleaned_page(page) if BORDER_CLEANUP else None
        if cleaned is not None:
            _doc, page = cleaned  # _doc keeps the cleaned page alive
        # full=True rasterizes and OCRs the whole page (the right choice for an
        # image-only page), so it also works when forcing OCR over a text layer.
        mask = photo_regions.photo_mask(page) if SKIP_PHOTO_REGIONS else None
        guard = self._geometric_elements(page, dpi=dpi, language=language, mask=mask)
        if not TESSERACT_LAYOUT:
            return guard
        # Tesseract's own layout reads mixed table-and-prose pages that ADR-035's
        # geometry cannot (ADR-038). Its layout analysis occasionally discards a
        # whole column of text, so the geometric text stays as the guard.
        read = tesseract_layout.read_page_layout(page, dpi=dpi, language=language, mask=mask)
        if read is None:
            return guard
        layout = layout_elements(tesseract_layout.groups(read.blocks))
        if not tesseract_layout.keeps_words(render_elements(layout), render_elements(guard),
                                            plausible=PLAUSIBLE_GUARD):
            return guard
        # Opt-in (ADR-046): tables found by Table Transformer, read cell by cell
        # from psm 3's own words. Only on psm 3's reading, whose words it reuses.
        if tables.enabled():
            present = [ln for el in layout for ln in el.lines]
            layout = tables.apply(layout, tables.read_tables(page, read.words, present), read.words)
        return layout

    @classmethod
    def _geometric_text(cls, page: pymupdf.Page, *, dpi: int, language: str,
                        mask: "photo_regions.PhotoMask | None") -> str:
        """The ADR-035/037 path's text (the guard), for the harness."""
        return render_elements(cls._geometric_elements(page, dpi=dpi, language=language, mask=mask))

    @staticmethod
    def _geometric_elements(page: pymupdf.Page, *, dpi: int, language: str,
                            mask: "photo_regions.PhotoMask | None") -> list[ir.Element]:
        """MuPDF's OCR, photo noise dropped (ADR-037), columns ordered (ADR-035)."""
        textpage = page.get_textpage_ocr(flags=0, language=language, dpi=dpi, full=True)
        # MuPDF groups OCR'd lines into blocks that run across columns, so a
        # multi-column scan comes out interleaved. Re-order by geometry; a page
        # with no text columns keeps MuPDF's own text unchanged (ADR-035).
        lines = _ocr_lines(page, textpage)
        # Photographs are "read" as garbage; drop those lines first (ADR-037).
        dropped = False
        if mask is not None and mask.cells:
            kept = [ln for ln in lines if not photo_regions.is_photo_noise(ln, mask)]
            dropped = len(kept) < len(lines)
            lines = kept
        paragraphs = order_paragraphs(lines)
        if paragraphs is not None:
            return [_lines_element(paragraph_text(p), [ln for row in p for ln in row], GEOMETRIC)
                    for p in paragraphs]
        if dropped:  # MuPDF's order, minus the photo noise
            text = "\n".join(ln.text for ln in lines if ln.text)
        else:
            text = page.get_text("text", textpage=textpage)
        return [_lines_element(text, lines, MUPDF_OCR)] if text.strip() else []


# Element sources on the Tesseract path (ADR-043).
GEOMETRIC = "tesseract-geometric"  # MuPDF's OCR lines, ordered by ADR-035's geometry
MUPDF_OCR = "mupdf-ocr"            # MuPDF's OCR text in its own order
PSM3 = "tesseract-psm3"            # Tesseract's own layout (ADR-038)
# What made a psm 3 group a table: ADR-038's row stitching.
ROW_STITCH = "tesseract-row-stitch"


def _lines_element(text: str, lines, source: str) -> ir.Element:
    return ir.Element(type=ir.TEXT, text=text, source=source, lines=tuple(lines),
                      bbox=ir.BBox.around(lines))


def layout_elements(groups) -> list[ir.Element]:
    """Tesseract psm 3 groups as IR elements: text blocks, and stitched tables."""
    out = []
    for g in groups:
        text = tesseract_layout.group_text(g)
        if not g.rows:
            out.append(_lines_element(text, g.lines, PSM3))
            continue
        rows = tuple(tuple(ir.Cell(ln.text, ir.BBox.around([ln])) for ln in row) for row in g.rows)
        out.append(ir.Element(type=ir.TABLE, text=text, source=PSM3, lines=g.lines,
                              bbox=ir.BBox.around(g.lines), rows=rows, signal=ROW_STITCH))
    return out


def _ocr_lines(page: pymupdf.Page, textpage) -> list[Line]:
    """Every recognised line on an OCR text page, with its bounding box."""
    lines = []
    for block in page.get_text("dict", textpage=textpage)["blocks"]:
        for line in block.get("lines", []):
            text = "".join(span["text"] for span in line["spans"]).strip()
            lines.append(Line(*line["bbox"], text))
    return lines


class EasyOCREngine(OcrEngine):
    """Opt-in neural engine (EasyOCR) — handles handwriting and noisy captures.

    Heavy (PyTorch); enabled with ``WISEAU_OCR_ENGINE=easyocr`` after installing
    ``requirements-ocr.txt``. The ``Reader`` is built once and reused; seeds are
    pinned so output is reproducible for identical input.
    """

    name = "easyocr"

    def __init__(self) -> None:
        self._readers: dict[str, object] = {}
        self._lock = threading.Lock()

    def _get_reader(self, language: str):
        langs = _easyocr_langs(language)
        key = "+".join(langs)
        reader = self._readers.get(key)
        if reader is None:
            with self._lock:
                reader = self._readers.get(key)
                if reader is None:
                    import easyocr
                    import torch

                    torch.manual_seed(0)
                    reader = easyocr.Reader(langs, gpu=False, verbose=False)
                    self._readers[key] = reader
        return reader

    def ocr_page(self, page: pymupdf.Page, *, dpi: int, language: str) -> str:
        pix = page.get_pixmap(dpi=dpi)
        png_bytes = pix.tobytes("png")
        reader = self._get_reader(language)
        # detail=0 → strings only; paragraph=True groups nearby lines in reading
        # order. EasyOCR returns results top-to-bottom, left-to-right.
        lines = reader.readtext(png_bytes, detail=0, paragraph=True)
        return "\n\n".join(line for line in lines if line and line.strip())


def _easyocr_langs(language: str) -> list[str]:
    """Translate a ``WISEAU_OCR_LANG`` value into EasyOCR language codes."""
    codes: list[str] = []
    for part in language.replace(",", "+").split("+"):
        part = part.strip()
        if part:
            codes.append(_TESSERACT_TO_EASYOCR_LANG.get(part, part))
    return codes or ["en"]


def _build_engine(name: str) -> OcrEngine:
    key = name.strip().lower()
    if key in ("", "tesseract", "tess"):
        return TesseractEngine()
    if key in ("easyocr", "easy", "neural"):
        return EasyOCREngine()
    raise ValueError(
        f"Unknown OCR engine '{name}'. Supported: 'tesseract' (default), 'easyocr'."
    )


@lru_cache(maxsize=None)
def get_engine(name: str) -> OcrEngine:
    """Return the named OCR engine (cached process-wide singleton)."""
    return _build_engine(name)
