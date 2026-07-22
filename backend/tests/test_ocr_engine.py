"""Unit tests for the pluggable OCR engine layer (`parsers.ocr`).

These need neither the `tesseract` binary nor PyTorch — they exercise engine
selection and language-code translation only, so they always run.
"""

from __future__ import annotations

import pytest

from parsers import ocr


def test_engines_selectable_by_name():
    assert ocr.get_engine("rapidocr").name == "rapidocr"
    assert ocr.get_engine("tesseract").name == "tesseract"
    assert ocr.get_engine("easyocr").name == "easyocr"
    # Empty name resolves to the default engine (RapidOCR).
    assert ocr.get_engine("").name == "rapidocr"


def test_engines_selectable_without_importing_heavy_deps():
    # Constructing an engine must not import its heavy backend (all lazy), so
    # selection works even where those deps are not installed.
    assert ocr.get_engine("rapidocr").name == "rapidocr"
    assert ocr.get_engine("easyocr").name == "easyocr"


def test_get_engine_is_cached_singleton():
    assert ocr.get_engine("tesseract") is ocr.get_engine("tesseract")


def test_unknown_engine_raises():
    with pytest.raises(ValueError):
        ocr.get_engine("does-not-exist")


def test_easyocr_lang_translation():
    assert ocr._easyocr_langs("eng") == ["en"]
    assert ocr._easyocr_langs("eng+deu") == ["en", "de"]
    assert ocr._easyocr_langs("eng,fra") == ["en", "fr"]
    assert ocr._easyocr_langs("") == ["en"]
    # An unknown code passes through unchanged (EasyOCR may still support it).
    assert ocr._easyocr_langs("jpn") == ["jpn"]
