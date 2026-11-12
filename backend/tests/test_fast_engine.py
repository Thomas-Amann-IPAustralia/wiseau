"""The fast engine (ADR-059): Tesseract psm 3 alone on scans, a cheap guard, no models.

Run against real OCR on synthesized scans, like `test_ocr.py`, and skipped the
same way when Tesseract is not installed.
"""

from __future__ import annotations

import shutil

import pymupdf
import pytest

from parsers import file_parser, ocr, tesseract_layout
from parsers.file_parser import file_to_markdown, resolve_engine

from test_ocr import LEFT_COLUMN, RIGHT_COLUMN, _mixed_pdf, _scanned_pdf, _two_column_scan

if shutil.which("tesseract") is None or pymupdf.get_tessdata() is None:
    pytest.skip("tesseract binary / tessdata not installed", allow_module_level=True)


def test_fast_is_a_requestable_local_engine():
    assert resolve_engine("fast") == "fast"
    assert resolve_engine("FAST") == "fast"
    assert "fast" in file_parser.available_engines()


def test_fast_can_be_the_deployment_default(monkeypatch):
    monkeypatch.setenv("WISEAU_PDF_ENGINE", "fast")
    assert file_parser.default_engine() == "fast"


def test_fast_reads_a_scan():
    md = file_to_markdown(_scanned_pdf("Invoice Total Amount"), "scan.pdf", engine="fast")
    assert "Invoice" in md and "Total" in md


def test_fast_is_deterministic():
    data = _scanned_pdf("Deterministic Fast Output")
    assert file_to_markdown(data, "scan.pdf", engine="fast") == file_to_markdown(data, "scan.pdf", engine="fast")


def test_fast_reads_two_columns_in_order():
    md = file_to_markdown(_two_column_scan(LEFT_COLUMN, RIGHT_COLUMN), "scan.pdf", engine="fast")
    assert md.index("until the harvest comes in autumn") < md.index("Banana groves need steady warmth")


def test_fast_leaves_text_layer_pages_as_the_default_reads_them():
    # Only the OCR stage differs: a text-layer page is read by PyMuPDF4LLM either way.
    data = _mixed_pdf("Native Page Content", "Scanned Insert Page")
    fast = file_to_markdown(data, "mixed.pdf", engine="fast")
    assert "Native Page Content" in fast
    doc = pymupdf.open()
    doc.new_page().insert_text((72, 72), "Born digital paragraph")
    digital = doc.tobytes()
    assert file_to_markdown(digital, "d.pdf", engine="fast") == file_to_markdown(digital, "d.pdf", engine="pymupdf")


def test_fast_never_runs_the_models(monkeypatch):
    """Even where the deployment turns both model passes on (the image does)."""
    from parsers import layout, tables

    monkeypatch.setattr(tables, "enabled", lambda: True)
    monkeypatch.setattr(layout, "enabled", lambda: True)

    def boom(*a, **k):
        raise AssertionError("a model pass ran on the fast engine")

    for name in ("_load", "render", "detect_image", "read_tables"):
        monkeypatch.setattr(tables, name, boom)
    for name in ("_load", "render", "detect_rendered", "read_layout"):
        monkeypatch.setattr(layout, name, boom)
    assert "Scanned" in file_to_markdown(_scanned_pdf("Scanned Without Models"), "scan.pdf", engine="fast")


def test_fast_uses_its_own_dpi(monkeypatch):
    seen = []
    real = ocr.FastTesseractEngine.read_elements

    def spy(self, page, *, dpi, language):
        seen.append(dpi)
        return real(self, page, dpi=dpi, language=language)

    monkeypatch.setattr(ocr.FastTesseractEngine, "read_elements", spy)
    monkeypatch.setenv("WISEAU_FAST_OCR_DPI", "240")
    file_to_markdown(_scanned_pdf("Resolution Check"), "scan.pdf", engine="fast")
    monkeypatch.delenv("WISEAU_FAST_OCR_DPI")
    file_to_markdown(_scanned_pdf("Resolution Check"), "scan.pdf", engine="fast")
    assert seen == [240, file_parser.DEFAULT_FAST_OCR_DPI]


def _guard_calls(monkeypatch, keeps):
    """Run one fast conversion with `keeps_words` forced; return the guard reads' DPIs."""
    calls = []
    real = ocr.TesseractEngine._geometric_elements

    def spy(page, *, dpi, language, mask):
        calls.append(dpi)
        return real(page, dpi=dpi, language=language, mask=mask)

    monkeypatch.setattr(ocr.TesseractEngine, "_geometric_elements", staticmethod(spy))
    monkeypatch.setattr(tesseract_layout, "keeps_words", lambda *a, **k: keeps)
    monkeypatch.setenv("WISEAU_FAST_OCR_DPI", "300")
    md = file_to_markdown(_scanned_pdf("Guarded Page Text"), "scan.pdf", engine="fast")
    return calls, md


def test_a_page_that_passes_the_cheap_guard_is_read_once(monkeypatch):
    calls, md = _guard_calls(monkeypatch, keeps=True)
    assert calls == [ocr.FAST_GUARD_DPI]
    assert "Guarded" in md


def test_a_page_that_fails_the_cheap_guard_gets_the_full_guard(monkeypatch):
    # psm 3 looks short against the cheap read, so the default path's guard and
    # rule decide, at the full resolution: the page is never left short-handed.
    calls, md = _guard_calls(monkeypatch, keeps=False)
    assert calls == [ocr.FAST_GUARD_DPI, 300]
    assert "Guarded" in md


def test_the_default_engine_is_unchanged_by_the_fast_engine(monkeypatch):
    # `pymupdf` still runs the default path's TesseractEngine, not the fast one.
    def boom(*a, **k):
        raise AssertionError("the fast engine ran for engine=pymupdf")

    monkeypatch.setattr(ocr.FastTesseractEngine, "read_elements", boom)
    assert "Scanned" in file_to_markdown(_scanned_pdf("Scanned Default Path"), "scan.pdf", engine="pymupdf")
