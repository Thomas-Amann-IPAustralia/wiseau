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

from parsers import docling_client
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


def test_supported_extensions_include_documents_and_images():
    # Documents plus image types (the latter are OCR'd). Kept as a superset check
    # so adding another image format doesn't spuriously break this test.
    assert {".pdf", ".docx"} <= SUPPORTED_EXTENSIONS
    assert {".png", ".jpg", ".jpeg", ".tiff"} <= SUPPORTED_EXTENSIONS


# --- Engine selection: docling-first with automatic fallback (Phase 6) ------
#
# `is_configured()` (does WISEAU_DOCLING_BASE point somewhere?) gates whether
# docling is attempted at all. With no base set — the default in tests and local
# dev — the deterministic parsers run exactly as before Phase 6, so every test
# above is unaffected. These tests drive the docling branch by faking both the
# "configured" check and the client's `convert_document`.


def _enable_docling(monkeypatch, converter):
    """Make docling appear configured and route conversions to ``converter``."""
    monkeypatch.setattr(docling_client, "is_configured", lambda: True)
    monkeypatch.setattr(docling_client, "convert_document", converter)


def test_docling_used_by_default_when_configured(monkeypatch):
    calls = {}

    def fake_convert(data, filename, **kwargs):
        calls["filename"] = filename
        return "# From docling\n\nHigh-fidelity body."

    monkeypatch.delenv("WISEAU_PDF_ENGINE", raising=False)  # default is docling
    _enable_docling(monkeypatch, fake_convert)

    result = file_to_markdown(_make_pdf("native text here"), "gov.pdf")
    assert "# From docling" in result
    assert calls["filename"] == "gov.pdf"
    # docling output still flows through the cleaner (single trailing newline).
    assert result.endswith("\n")
    assert not result.endswith("\n\n")


def test_docling_skipped_when_not_configured(monkeypatch):
    # No base configured (the default): the deterministic parser runs, and the
    # docling client is never called.
    def exploding_convert(*args, **kwargs):  # pragma: no cover - must not run
        raise AssertionError("docling must not be called when unconfigured")

    monkeypatch.setattr(docling_client, "is_configured", lambda: False)
    monkeypatch.setattr(docling_client, "convert_document", exploding_convert)

    result = file_to_markdown(_make_pdf("Deterministic PyMuPDF path"), "doc.pdf")
    assert "Deterministic PyMuPDF path" in result


def test_pymupdf_engine_forces_deterministic_path(monkeypatch):
    # WISEAU_PDF_ENGINE=pymupdf pins the deterministic parser even if docling is up.
    def exploding_convert(*args, **kwargs):  # pragma: no cover - must not run
        raise AssertionError("docling must not be called when WISEAU_PDF_ENGINE=pymupdf")

    monkeypatch.setenv("WISEAU_PDF_ENGINE", "pymupdf")
    _enable_docling(monkeypatch, exploding_convert)

    result = file_to_markdown(_make_pdf("Pinned to PyMuPDF"), "doc.pdf")
    assert "Pinned to PyMuPDF" in result


def test_fallback_on_docling_unavailable(monkeypatch):
    # docling infrastructure failure → degrade to the deterministic parser.
    def failing_convert(data, filename, **kwargs):
        raise docling_client.DoclingUnavailable("cold start")

    monkeypatch.delenv("WISEAU_PDF_ENGINE", raising=False)
    _enable_docling(monkeypatch, failing_convert)

    result = file_to_markdown(_make_pdf("Recovered by fallback"), "doc.pdf")
    assert "Recovered by fallback" in result


def test_fallback_on_bad_document(monkeypatch):
    # docling rejects the document (4xx) → still fall back rather than error out.
    def failing_convert(data, filename, **kwargs):
        raise docling_client.DoclingBadDocument("unsupported")

    monkeypatch.delenv("WISEAU_PDF_ENGINE", raising=False)
    _enable_docling(monkeypatch, failing_convert)

    result = file_to_markdown(_make_docx(), "report.docx")
    # The Mammoth fallback recovers the DOCX structure.
    assert "# Quarterly Report" in result


def test_fallback_preserves_unsupported_type_error(monkeypatch):
    # Engine selection must not swallow the 415 contract for unknown types: the
    # extension is rejected before any engine is consulted.
    def exploding_convert(*args, **kwargs):  # pragma: no cover - must not run
        raise AssertionError("unsupported types must be rejected before docling")

    _enable_docling(monkeypatch, exploding_convert)
    with pytest.raises(ValueError) as excinfo:
        file_to_markdown(b"whatever", "notes.txt")
    assert ".txt" in str(excinfo.value)


# --- Engine attribution (observability) -------------------------------------
# ADR-014 turns a docling outage into a *successful* response, so the only way a
# silent outage is visible is the engine attribution these tests pin.


@pytest.fixture()
def fresh_metrics():
    from observability import metrics

    metrics.reset()
    yield metrics
    metrics.reset()


def test_docling_success_is_attributed_to_docling(monkeypatch, fresh_metrics):
    monkeypatch.delenv("WISEAU_PDF_ENGINE", raising=False)
    _enable_docling(monkeypatch, lambda data, filename, **kwargs: "# From docling\n")

    file_to_markdown(_make_pdf("native text here"), "gov.pdf")

    snapshot = fresh_metrics.snapshot()
    assert snapshot["engines"] == {"docling": 1}
    assert snapshot["docling"]["successes"] == 1
    assert snapshot["docling"]["fallbacks"] == 0


def test_fallback_records_the_engine_and_the_reason(monkeypatch, fresh_metrics):
    def failing_convert(data, filename, **kwargs):
        raise docling_client.DoclingUnavailable("cold start")

    monkeypatch.delenv("WISEAU_PDF_ENGINE", raising=False)
    _enable_docling(monkeypatch, failing_convert)

    file_to_markdown(_make_pdf("Recovered by fallback"), "doc.pdf")

    snapshot = fresh_metrics.snapshot()
    assert snapshot["engines"] == {"pymupdf": 1}
    assert snapshot["docling"]["fallbacks"] == 1
    assert snapshot["docling"]["reasons"] == {"DoclingUnavailable": 1}


def test_a_rejected_document_is_recorded_apart_from_an_outage(monkeypatch, fresh_metrics):
    def rejecting_convert(data, filename, **kwargs):
        raise docling_client.DoclingBadDocument("unreadable")

    monkeypatch.delenv("WISEAU_PDF_ENGINE", raising=False)
    _enable_docling(monkeypatch, rejecting_convert)

    file_to_markdown(_make_pdf("Recovered by fallback"), "doc.pdf")

    assert fresh_metrics.snapshot()["docling"]["reasons"] == {"DoclingBadDocument": 1}


def test_pinning_pymupdf_is_recorded_as_a_deliberate_skip(monkeypatch, fresh_metrics):
    monkeypatch.setenv("WISEAU_PDF_ENGINE", "pymupdf")
    _enable_docling(monkeypatch, lambda *a, **k: pytest.fail("docling must not be called"))

    file_to_markdown(_make_docx(), "doc.docx")

    snapshot = fresh_metrics.snapshot()
    assert snapshot["engines"] == {"mammoth": 1}
    assert snapshot["docling"]["reasons"] == {"engine_not_selected": 1}
