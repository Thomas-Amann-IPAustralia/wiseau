"""Tests for OCR of scanned/image-only PDFs and image uploads.

These exercise the real OCR path end-to-end. Fixtures are generated in memory:
text is *rendered to an image* (no embedded text layer) so the only way to
recover it is genuine OCR — proving the scanned-document path works, not merely
PyMuPDF4LLM's native text extraction.

The whole module is skipped when the `tesseract` binary / language data are
unavailable, mirroring the existing opt-in pattern for environment-dependent
tests. CI installs Tesseract so these run for real there.
"""

from __future__ import annotations

import io
import shutil

import pymupdf
import pytest

from parsers.file_parser import file_to_markdown, image_to_markdown, pdf_to_markdown

Image = pytest.importorskip("PIL.Image", reason="Pillow is required to synthesize OCR fixtures")
ImageDraw = pytest.importorskip("PIL.ImageDraw")
ImageFont = pytest.importorskip("PIL.ImageFont")

if shutil.which("tesseract") is None or pymupdf.get_tessdata() is None:
    pytest.skip("tesseract binary / tessdata not installed", allow_module_level=True)


def _font(size: int):
    """A large, clean TrueType font if one is available; else Pillow's default."""
    for name in ("DejaVuSans.ttf", "LiberationSans-Regular.ttf", "Arial.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def _text_image(text: str, *, width: int = 1100, line_height: int = 80):
    """Render text onto a clean white image at a size Tesseract reads reliably."""
    lines = text.split("\n")
    img = Image.new("RGB", (width, line_height * len(lines) + 80), "white")
    draw = ImageDraw.Draw(img)
    font = _font(52)
    y = 40
    for line in lines:
        draw.text((40, y), line, fill="black", font=font)
        y += line_height
    return img


def _image_bytes(text: str, fmt: str = "PNG") -> bytes:
    buf = io.BytesIO()
    _text_image(text).save(buf, format=fmt)
    return buf.getvalue()


def _scanned_pdf(text: str) -> bytes:
    """A PDF whose page is a rendered image — no text layer at all (a 'scan')."""
    src = pymupdf.open(stream=_image_bytes(text), filetype="png")
    pdf_bytes = src.convert_to_pdf()
    src.close()
    doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
    out = doc.tobytes()
    doc.close()
    return out


def _mixed_pdf(native_text: str, scanned_text: str) -> bytes:
    """A two-page PDF: page 0 has a real text layer, page 1 is a scanned image."""
    doc = pymupdf.open()
    doc.new_page().insert_text((72, 72), native_text)
    page1 = doc.new_page()
    page1.insert_image(page1.rect, stream=_image_bytes(scanned_text))
    out = doc.tobytes()
    doc.close()
    return out


# --- Sanity: the scanned fixture really has no text layer --------------------


def test_scanned_pdf_fixture_has_no_text_layer():
    doc = pymupdf.open(stream=_scanned_pdf("No Text Layer Here"), filetype="pdf")
    try:
        assert doc[0].get_text("text").strip() == ""
    finally:
        doc.close()


# --- OCR recovers text from a scanned PDF ------------------------------------


def test_scanned_pdf_is_ocred():
    result = pdf_to_markdown(_scanned_pdf("Invoice Total Amount"))
    assert "Invoice" in result
    assert "Total" in result


def test_scanned_pdf_via_dispatch_and_cleaner():
    # Full public path: dispatch by extension + clean_markdown normalization.
    result = file_to_markdown(_scanned_pdf("Scanned Document Body"), "scan.pdf")
    assert "Scanned" in result
    assert result.endswith("\n")
    assert not result.endswith("\n\n")


def test_scanned_pdf_ocr_is_deterministic():
    data = _scanned_pdf("Deterministic OCR Output")
    assert pdf_to_markdown(data) == pdf_to_markdown(data)


def test_mixed_pdf_uses_native_text_and_ocr():
    # The native page must survive verbatim; the scanned page must be OCR'd.
    result = file_to_markdown(_mixed_pdf("Native Page Content", "Scanned Insert Page"), "mixed.pdf")
    assert "Native Page Content" in result
    assert "Scanned" in result and "Insert" in result


# --- Image uploads -----------------------------------------------------------


def test_png_upload_is_ocred():
    result = file_to_markdown(_image_bytes("Hello From Image"), "note.png")
    assert "Hello" in result
    assert "Image" in result


def test_jpeg_upload_is_ocred():
    result = file_to_markdown(_image_bytes("Receipt Photo", fmt="JPEG"), "receipt.jpg")
    assert "Receipt" in result


def test_image_to_markdown_direct():
    assert "Snapshot" in image_to_markdown(_image_bytes("Snapshot Text"), ".png")


def test_supported_extensions_include_pdf_docx_and_images():
    from parsers.file_parser import SUPPORTED_EXTENSIONS

    assert {".pdf", ".docx", ".png", ".jpg"} <= SUPPORTED_EXTENSIONS


# --- Native text path is unaffected by OCR being available -------------------


def test_digital_pdf_still_extracts_native_text():
    doc = pymupdf.open()
    doc.new_page().insert_text((72, 72), "Born Digital Text")
    data = doc.tobytes()
    doc.close()
    assert "Born Digital Text" in pdf_to_markdown(data)


# --- OCR modes ---------------------------------------------------------------


def test_ocr_mode_off_skips_scanned_ocr(monkeypatch):
    # With OCR off, a scanned PDF yields (essentially) nothing, not recovered text.
    monkeypatch.setenv("WISEAU_OCR_MODE", "off")
    result = pdf_to_markdown(_scanned_pdf("Should Not Appear"))
    assert "Should Not Appear" not in result


def test_ocr_mode_off_rejects_image_upload(monkeypatch):
    monkeypatch.setenv("WISEAU_OCR_MODE", "off")
    with pytest.raises(ValueError):
        image_to_markdown(_image_bytes("nope"), ".png")


def test_ocr_mode_off_leaves_native_text_intact(monkeypatch):
    monkeypatch.setenv("WISEAU_OCR_MODE", "off")
    doc = pymupdf.open()
    doc.new_page().insert_text((72, 72), "Native Survives Off")
    data = doc.tobytes()
    doc.close()
    assert "Native Survives Off" in pdf_to_markdown(data)
