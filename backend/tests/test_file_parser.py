"""Tests for document dispatch and extraction.

Covers the browser-free half of the engine: extension-based routing, the
error contract for unsupported types, and real round-trips for both supported
document types. Fixtures (PDF and DOCX) are generated in-memory so the tests
need no committed binary files.
"""

from __future__ import annotations

import io

import pymupdf
import pytest

from parsers.file_parser import SUPPORTED_EXTENSIONS, file_to_markdown

docx = pytest.importorskip("docx", reason="python-docx (dev dependency) is required to synthesize DOCX fixtures")


def _make_pdf(text: str) -> bytes:
    """Build a minimal one-page PDF containing ``text``."""
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 72), text)
    data = doc.tobytes()
    doc.close()
    return data


def _make_docx() -> bytes:
    """Build a small DOCX exercising headings, a paragraph, and a bullet list."""
    document = docx.Document()
    document.add_heading("Quarterly Report", level=1)
    document.add_paragraph("Revenue grew by 12% this quarter.")
    document.add_heading("Details", level=2)
    document.add_paragraph("First point", style="List Bullet")
    document.add_paragraph("Second point", style="List Bullet")
    buf = io.BytesIO()
    document.save(buf)
    return buf.getvalue()


def test_unsupported_extension_raises_valueerror():
    with pytest.raises(ValueError) as excinfo:
        file_to_markdown(b"whatever", "notes.txt")
    assert ".txt" in str(excinfo.value)


def test_missing_extension_raises_valueerror():
    with pytest.raises(ValueError) as excinfo:
        file_to_markdown(b"whatever", "README")
    assert "unknown" in str(excinfo.value)


def test_extension_matching_is_case_insensitive():
    # An uppercase extension must not fall through to the unsupported branch.
    pdf = _make_pdf("Case Insensitive")
    result = file_to_markdown(pdf, "REPORT.PDF")
    assert "Case Insensitive" in result


def test_pdf_round_trip_extracts_text():
    pdf = _make_pdf("Hello Markdown")
    result = file_to_markdown(pdf, "doc.pdf")
    assert "Hello Markdown" in result
    # Output flows through the cleaner, so it ends with a single newline.
    assert result.endswith("\n")
    assert not result.endswith("\n\n")


def test_docx_round_trip_extracts_structure():
    result = file_to_markdown(_make_docx(), "report.docx")
    # Headings become ATX, the body paragraph survives, and the list becomes
    # Markdown bullets — the full Mammoth -> markdownify -> cleaner path.
    assert "# Quarterly Report" in result
    assert "## Details" in result
    assert "Revenue grew by 12% this quarter." in result
    assert "* First point" in result
    assert "* Second point" in result
    # Cleaner guarantees a single trailing newline.
    assert result.endswith("\n")
    assert not result.endswith("\n\n")


def test_docx_extraction_is_deterministic():
    data = _make_docx()
    assert file_to_markdown(data, "report.docx") == file_to_markdown(data, "report.docx")


def test_supported_extensions_are_pdf_and_docx():
    assert SUPPORTED_EXTENSIONS == {".pdf", ".docx"}
