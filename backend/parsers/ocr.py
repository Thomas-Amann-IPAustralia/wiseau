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


class TesseractEngine(OcrEngine):
    """Default engine: MuPDF's built-in Tesseract via ``get_textpage_ocr``.

    Deterministic and lightweight (system binary only). Great on printed/scanned
    text; weak on cursive handwriting — use the EasyOCR engine for that.
    """

    name = "tesseract"

    def ocr_page(self, page: pymupdf.Page, *, dpi: int, language: str) -> str:
        # full=True rasterizes and OCRs the whole page (the right choice for an
        # image-only page), so it also works when forcing OCR over a text layer.
        textpage = page.get_textpage_ocr(flags=0, language=language, dpi=dpi, full=True)
        return page.get_text("text", textpage=textpage)


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
