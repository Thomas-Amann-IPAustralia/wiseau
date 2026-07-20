"""Tests for document dispatch and extraction.

Covers the browser-free half of the engine: extension-based routing, the
error contract for unsupported types, and a real PDF round-trip (a PDF is
generated in-memory so the test needs no fixture files).
"""

from __future__ import annotations

import pymupdf
import pytest

from parsers.file_parser import SUPPORTED_EXTENSIONS, file_to_markdown


def _make_pdf(text: str) -> bytes:
    """Build a minimal one-page PDF containing ``text``."""
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 72), text)
    data = doc.tobytes()
    doc.close()
    return data


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


def test_supported_extensions_are_pdf_and_docx():
    assert SUPPORTED_EXTENSIONS == {".pdf", ".docx"}
