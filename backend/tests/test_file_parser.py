"""Tests for document dispatch and extraction.

Covers the browser-free half of the engine: extension-based routing, the
error contract for unsupported types, and real round-trips for both supported
document types. Fixtures (PDF and DOCX) are generated in-memory so the tests
need no committed binary files.
"""

from __future__ import annotations

import base64
import io
import shutil

import pymupdf
import pytest

from parsers import docling_client, file_parser, glm_ocr_client
from parsers.file_parser import SUPPORTED_EXTENSIONS, file_to_markdown

docx = pytest.importorskip("docx", reason="python-docx (dev dependency) is required to synthesize DOCX fixtures")


def _make_pdf(text: str) -> bytes:
    """Build a minimal one-page **born-digital** PDF containing ``text``.

    Keep ``text`` comfortably longer than ``_TEXT_LAYER_MIN_CHARS``: a page with
    fewer than that many characters is classified as image-only and quietly
    rerouted through OCR, so a short fixture stops testing the native path (and
    starts needing a `tesseract` binary to pass at all).
    """
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 72), text)
    data = doc.tobytes()
    doc.close()
    return data


def _make_scanned_pdf(text: str) -> bytes:
    """Build a one-page PDF with **no** text layer — the page is a rendered image."""
    src = pymupdf.open()
    src.new_page().insert_text((72, 120), text, fontsize=44)
    pix = src[0].get_pixmap(dpi=150)
    src.close()

    scan = pymupdf.open()
    page = scan.new_page(width=pix.width, height=pix.height)
    page.insert_image(page.rect, pixmap=pix)
    data = scan.tobytes()
    scan.close()
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
    pdf = _make_pdf("Case Insensitive extension routing")
    result = file_to_markdown(pdf, "REPORT.PDF")
    assert "Case Insensitive" in result


def test_a_page_with_a_text_layer_is_never_ocred(monkeypatch):
    """The native fast path must not touch the OCR engine.

    Detection is a character count, so a fixture that drifts under
    `_TEXT_LAYER_MIN_CHARS` silently moves onto the OCR path — passing for the
    wrong reason, and only where `tesseract` is installed. Making the engine fatal
    pins the born-digital PDF to native extraction.
    """
    monkeypatch.setattr(
        file_parser,
        "_engine",
        lambda: pytest.fail("a born-digital page must not be sent to OCR"),
    )

    result = file_to_markdown(_make_pdf("A born-digital page with a real text layer"), "doc.pdf")

    assert "born-digital page" in result


def test_pdf_round_trip_extracts_text():
    pdf = _make_pdf("Hello Markdown, rendered from a real text layer")
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


# --- Engine selection: docling when selected, with automatic fallback -------
#
# Two gates decide whether docling runs: it must be *selected* (the default is
# `pymupdf` — ADR-027) and *configured* (`is_configured()`: does
# WISEAU_DOCLING_BASE point somewhere?). With neither set — the state in tests
# and local dev — the deterministic parsers run exactly as before Phase 6, so
# every test above is unaffected. These tests drive the docling branch by faking both the
# "configured" check and the client's `convert_document`.


def _enable_docling(monkeypatch, converter):
    """Make docling appear configured and route conversions to ``converter``."""
    monkeypatch.setattr(docling_client, "is_configured", lambda: True)
    monkeypatch.setattr(docling_client, "convert_document", converter)


def test_the_default_engine_is_the_fast_local_parser(monkeypatch):
    """With no `WISEAU_PDF_ENGINE`, a configured docling is *not* used (ADR-027).

    Fidelity is opt-in: the deployment default is the parser that answers in a
    second, so a docling Space being reachable must not by itself divert every
    conversion through a minute of ML inference.
    """

    def exploding_convert(*args, **kwargs):  # pragma: no cover - must not run
        raise AssertionError("docling must not run unless it is asked for")

    monkeypatch.delenv("WISEAU_PDF_ENGINE", raising=False)
    _enable_docling(monkeypatch, exploding_convert)

    result = file_to_markdown(_make_pdf("Deterministic by default"), "gov.pdf")
    assert "Deterministic by default" in result


@pytest.mark.parametrize(
    ("configured", "expected"),
    [
        (None, "pymupdf"),
        ("docling", "docling"),
        ("glm-ocr", "glm-ocr"),
        ("pymupdf", "pymupdf"),
        ("nonsense", "pymupdf"),
    ],
)
def test_default_engine_reports_what_will_actually_run(monkeypatch, configured, expected):
    # `/ping` publishes this, so it must describe behaviour, not the raw string:
    # anything that is not `docling` runs the local parser.
    if configured is None:
        monkeypatch.delenv("WISEAU_PDF_ENGINE", raising=False)
    else:
        monkeypatch.setenv("WISEAU_PDF_ENGINE", configured)
    assert file_parser.default_engine() == expected


def test_docling_used_when_the_deployment_selects_it(monkeypatch):
    calls = {}

    def fake_convert(data, filename, **kwargs):
        calls["filename"] = filename
        return "# From docling\n\nHigh-fidelity body."

    monkeypatch.setenv("WISEAU_PDF_ENGINE", "docling")
    _enable_docling(monkeypatch, fake_convert)

    result = file_to_markdown(_make_pdf("native text layer, comfortably long"), "gov.pdf")
    assert "# From docling" in result
    assert calls["filename"] == "gov.pdf"
    # docling output still flows through the cleaner (single trailing newline).
    assert result.endswith("\n")
    assert not result.endswith("\n\n")


def test_docling_skipped_when_not_configured(monkeypatch):
    # Selected but with no base configured: the deterministic parser runs, and
    # the docling client is never called.
    def exploding_convert(*args, **kwargs):  # pragma: no cover - must not run
        raise AssertionError("docling must not be called when unconfigured")

    monkeypatch.setenv("WISEAU_PDF_ENGINE", "docling")
    monkeypatch.setattr(docling_client, "is_configured", lambda: False)
    monkeypatch.setattr(docling_client, "convert_document", exploding_convert)

    result = file_to_markdown(_make_pdf("Deterministic PyMuPDF path"), "doc.pdf")
    assert "Deterministic PyMuPDF path" in result


def test_pymupdf_engine_forces_deterministic_path(monkeypatch):
    # WISEAU_PDF_ENGINE=pymupdf (also the default) pins the deterministic parser
    # even if docling is up.
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

    monkeypatch.setenv("WISEAU_PDF_ENGINE", "docling")
    _enable_docling(monkeypatch, failing_convert)

    result = file_to_markdown(_make_pdf("Recovered by fallback"), "doc.pdf")
    assert "Recovered by fallback" in result


def test_fallback_on_bad_document(monkeypatch):
    # docling rejects the document (4xx) → still fall back rather than error out.
    def failing_convert(data, filename, **kwargs):
        raise docling_client.DoclingBadDocument("unsupported")

    monkeypatch.setenv("WISEAU_PDF_ENGINE", "docling")
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
    monkeypatch.setenv("WISEAU_PDF_ENGINE", "docling")
    _enable_docling(monkeypatch, lambda data, filename, **kwargs: "# From docling\n")

    file_to_markdown(_make_pdf("native text layer, comfortably long"), "gov.pdf")

    snapshot = fresh_metrics.snapshot()
    assert snapshot["engines"] == {"docling": 1}
    assert snapshot["docling"]["successes"] == 1
    assert snapshot["docling"]["fallbacks"] == 0


def test_fallback_records_the_engine_and_the_reason(monkeypatch, fresh_metrics):
    def failing_convert(data, filename, **kwargs):
        raise docling_client.DoclingUnavailable("cold start")

    monkeypatch.setenv("WISEAU_PDF_ENGINE", "docling")
    _enable_docling(monkeypatch, failing_convert)

    file_to_markdown(_make_pdf("Recovered by fallback"), "doc.pdf")

    snapshot = fresh_metrics.snapshot()
    assert snapshot["engines"] == {"pymupdf": 1}
    assert snapshot["docling"]["fallbacks"] == 1
    assert snapshot["docling"]["reasons"] == {"DoclingUnavailable": 1}


def test_a_rejected_document_is_recorded_apart_from_an_outage(monkeypatch, fresh_metrics):
    def rejecting_convert(data, filename, **kwargs):
        raise docling_client.DoclingBadDocument("unreadable")

    monkeypatch.setenv("WISEAU_PDF_ENGINE", "docling")
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


def test_a_scanned_pdf_is_attributed_to_ocr(fresh_metrics):
    """A scanned PDF must not be billed to `pymupdf`.

    Attributing every `.pdf` to `pymupdf` regardless of how the text was actually
    recovered leaves `engines.ocr` permanently at zero for documents, hiding the
    OCR path completely from `GET /metrics` (tech-spec §12).
    """
    if shutil.which("tesseract") is None or pymupdf.get_tessdata() is None:
        pytest.skip("tesseract binary / tessdata not installed")

    result = file_to_markdown(_make_scanned_pdf("SCANNED NOTICE"), "scan.pdf")

    assert "SCANNED" in result.upper()
    assert fresh_metrics.snapshot()["engines"] == {"ocr": 1}


def test_a_born_digital_pdf_is_still_attributed_to_pymupdf(fresh_metrics):
    file_to_markdown(_make_pdf("A born-digital page with a real text layer"), "doc.pdf")
    assert fresh_metrics.snapshot()["engines"] == {"pymupdf": 1}


# --- Per-request engine choice (ADR-025) ------------------------------------
#
# The deployment default lives in `WISEAU_PDF_ENGINE`; a caller may override it
# per request, because the two engines trade speed against fidelity and only the
# caller knows which they want for *this* document.


@pytest.mark.parametrize("requested", [None, "", "  ", "auto", "AUTO", "default"])
def test_no_choice_defers_to_the_deployment_default(requested):
    assert file_parser.resolve_engine(requested) is None


@pytest.mark.parametrize(
    ("requested", "expected"),
    [
        ("docling", "docling"),
        ("DOCLING", "docling"),
        ("glm-ocr", "glm-ocr"),
        (" GLM-OCR ", "glm-ocr"),
        ("pymupdf", "pymupdf"),
        (" PyMuPDF ", "pymupdf"),
    ],
)
def test_a_known_engine_is_normalized(requested, expected):
    assert file_parser.resolve_engine(requested) == expected


def test_an_unknown_engine_is_rejected():
    with pytest.raises(ValueError) as excinfo:
        file_parser.resolve_engine("tesseract-please")
    # The message names what the caller may ask for instead.
    assert "docling" in str(excinfo.value)
    assert "pymupdf" in str(excinfo.value)


def test_requesting_docling_overrides_a_pymupdf_deployment(monkeypatch):
    monkeypatch.setenv("WISEAU_PDF_ENGINE", "pymupdf")
    _enable_docling(monkeypatch, lambda data, filename, **kwargs: "# From docling\n")

    result = file_to_markdown(_make_pdf("Ignored by docling"), "doc.pdf", engine="docling")

    assert "From docling" in result


def test_requesting_pymupdf_skips_a_configured_docling(monkeypatch):
    monkeypatch.setenv("WISEAU_PDF_ENGINE", "docling")  # a fidelity-first deployment
    _enable_docling(monkeypatch, lambda *a, **k: pytest.fail("docling must not be called"))

    result = file_to_markdown(_make_pdf("Pinned per request"), "doc.pdf", engine="pymupdf")

    assert "Pinned per request" in result


def test_a_requested_docling_still_falls_back(monkeypatch, fresh_metrics):
    """Choosing fidelity must not cost resilience: the fallback still applies."""

    def failing_convert(data, filename, **kwargs):
        raise docling_client.DoclingUnavailable("space asleep")

    monkeypatch.setenv("WISEAU_PDF_ENGINE", "pymupdf")
    _enable_docling(monkeypatch, failing_convert)

    result = file_to_markdown(_make_pdf("Recovered by fallback"), "doc.pdf", engine="docling")

    assert "Recovered by fallback" in result
    assert fresh_metrics.snapshot()["docling"]["fallbacks"] == 1


# --- Embedded images are not inlined as base64 (ADR-024) --------------------

# A 1x1 PNG: enough for python-docx to embed a real picture part.
_PNG_1X1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)


def _make_docx_with_image() -> bytes:
    document = docx.Document()
    document.add_heading("Illustrated", level=1)
    document.add_paragraph("Text around the figure.")
    document.add_picture(io.BytesIO(_PNG_1X1))
    buf = io.BytesIO()
    document.save(buf)
    return buf.getvalue()


def test_a_docx_image_does_not_become_a_base64_blob():
    """Mammoth's default handler inlines pictures as data URIs; ours must not.

    A screenshot in a Word document would otherwise contribute tens of thousands
    of unreadable characters — often more than the document's own text.
    """
    result = file_to_markdown(_make_docx_with_image(), "illustrated.docx")

    assert "base64" not in result
    assert "iVBORw0" not in result
    # The text around the figure is untouched, and the result stays small.
    assert "# Illustrated" in result
    assert "Text around the figure." in result
    assert len(result) < 200


# --- GLM-OCR: the third engine (ADR-034) -------------------------------------
# Same two gates as docling (selected *and* configured), one more: GLM-OCR reads
# pixels and has no DOCX reader, so a DOCX never reaches it.


def _enable_glm_ocr(monkeypatch, converter):
    monkeypatch.setattr(glm_ocr_client, "is_configured", lambda: True)
    monkeypatch.setattr(glm_ocr_client, "convert_document", converter)


class _FakeOcr:
    """Stands in for Tesseract so the fallback tests need no `tesseract` binary."""

    name = "fake"

    def ocr_page(self, page, *, dpi, language):
        return f"local OCR of page {page.number}"


def _fake_local_ocr(monkeypatch):
    monkeypatch.setattr(file_parser, "_engine", lambda: _FakeOcr())


def _pdf_of(*pages: bytes) -> bytes:
    """Concatenate one-page PDFs into one document, in order."""
    out = pymupdf.open()
    for data in pages:
        src = pymupdf.open(stream=data, filetype="pdf")
        out.insert_pdf(src)
        src.close()
    data = out.tobytes()
    out.close()
    return data


def test_glm_ocr_is_not_used_unless_asked_for(monkeypatch):
    monkeypatch.delenv("WISEAU_PDF_ENGINE", raising=False)
    _enable_glm_ocr(monkeypatch, lambda *a, **k: pytest.fail("GLM-OCR must not run unless asked for"))
    _enable_docling(monkeypatch, lambda *a, **k: pytest.fail("docling must not run either"))

    result = file_to_markdown(_make_pdf("The fast parser answers by default"), "doc.pdf")

    assert "The fast parser answers by default" in result


def test_requesting_glm_ocr_routes_to_it_and_cleans_the_output(monkeypatch, fresh_metrics):
    calls = {}

    def fake_convert(data, filename, **kwargs):
        calls["filename"] = filename
        return "# From GLM-OCR\n\n\n\nScanned body.   \n\n"

    _enable_glm_ocr(monkeypatch, fake_convert)
    _enable_docling(monkeypatch, lambda *a, **k: pytest.fail("docling must not be called"))

    result = file_to_markdown(_make_scanned_pdf("Ignored by GLM-OCR"), "scan.pdf", engine="glm-ocr")

    assert calls["filename"] == "scan.pdf"
    assert result == "# From GLM-OCR\n\nScanned body.\n"  # through clean_markdown
    snapshot = fresh_metrics.snapshot()
    assert snapshot["engines"] == {"glm-ocr": 1}
    assert snapshot["glm_ocr"]["successes"] == 1
    assert snapshot["docling"]["reasons"] == {"engine_not_selected": 1}


def test_the_deployment_can_default_to_glm_ocr(monkeypatch):
    monkeypatch.setenv("WISEAU_PDF_ENGINE", "glm-ocr")
    _enable_glm_ocr(monkeypatch, lambda data, filename, **kwargs: "# From GLM-OCR\n")

    assert "From GLM-OCR" in file_to_markdown(_make_scanned_pdf("Ignored"), "scan.pdf")


@pytest.mark.parametrize(
    "error", [glm_ocr_client.GlmOcrUnavailable("asleep"), glm_ocr_client.GlmOcrBadDocument("too many pages")]
)
def test_a_requested_glm_ocr_still_falls_back(monkeypatch, fresh_metrics, error):
    def failing_convert(data, filename, **kwargs):
        raise error

    _enable_glm_ocr(monkeypatch, failing_convert)
    _fake_local_ocr(monkeypatch)

    result = file_to_markdown(_make_scanned_pdf("Recovered by fallback"), "doc.pdf", engine="glm-ocr")

    assert "local OCR of page" in result
    snapshot = fresh_metrics.snapshot()
    assert snapshot["engines"] == {"ocr": 1}
    assert snapshot["glm_ocr"]["fallbacks"] == 1
    assert snapshot["glm_ocr"]["reasons"] == {type(error).__name__: 1}


def test_glm_ocr_skipped_when_not_configured(monkeypatch, fresh_metrics):
    monkeypatch.setattr(glm_ocr_client, "is_configured", lambda: False)
    monkeypatch.setattr(glm_ocr_client, "convert_document", lambda *a, **k: pytest.fail("not configured"))

    result = file_to_markdown(_make_pdf("Local parser answers"), "doc.pdf", engine="glm-ocr")

    assert "Local parser answers" in result
    assert fresh_metrics.snapshot()["glm_ocr"]["reasons"] == {"not_configured": 1}


def test_a_docx_never_goes_to_glm_ocr(monkeypatch, fresh_metrics):
    # GLM-OCR has no DOCX reader; Mammoth converts it, and the skip says why.
    _enable_glm_ocr(monkeypatch, lambda *a, **k: pytest.fail("GLM-OCR cannot read DOCX"))

    result = file_to_markdown(_make_docx(), "report.docx", engine="glm-ocr")

    assert "# Quarterly Report" in result
    snapshot = fresh_metrics.snapshot()
    assert snapshot["engines"] == {"mammoth": 1}
    assert snapshot["glm_ocr"]["reasons"] == {"unsupported_type": 1}


# --- GLM-OCR page by page (ADR-036) -------------------------------------------


def _page_count(data: bytes) -> int:
    doc = pymupdf.open(stream=data, filetype="pdf")
    try:
        return doc.page_count
    finally:
        doc.close()


def test_a_born_digital_pdf_never_goes_to_glm_ocr(monkeypatch, fresh_metrics):
    _enable_glm_ocr(monkeypatch, lambda *a, **k: pytest.fail("a text layer must not be re-read from pixels"))

    result = file_to_markdown(_make_pdf("Read straight from the text layer"), "doc.pdf", engine="glm-ocr")

    assert "Read straight from the text layer" in result
    snapshot = fresh_metrics.snapshot()
    assert snapshot["engines"] == {"pymupdf": 1}
    assert snapshot["glm_ocr"]["reasons"] == {"no_image_pages": 1}


def test_a_mixed_pdf_sends_only_its_image_runs_to_glm_ocr(monkeypatch, fresh_metrics):
    calls = []

    def fake_convert(data, filename, **kwargs):
        calls.append(_page_count(data))
        return f"GLM run {len(calls)}"

    _enable_glm_ocr(monkeypatch, fake_convert)
    pdf = _pdf_of(
        _make_pdf("Digital page one with a text layer"),
        _make_scanned_pdf("scan"),
        _make_scanned_pdf("scan"),
        _make_pdf("Digital page four with a text layer"),
        _make_scanned_pdf("scan"),
    )

    result = file_to_markdown(pdf, "mixed.pdf", engine="glm-ocr")

    assert calls == [2, 1]  # one request per consecutive run of image-only pages
    order = ["Digital page one", "GLM run 1", "Digital page four", "GLM run 2"]
    positions = [result.index(marker) for marker in order]
    assert positions == sorted(positions)
    snapshot = fresh_metrics.snapshot()
    assert snapshot["engines"] == {"glm-ocr": 1}
    assert snapshot["glm_ocr"]["successes"] == 2


def test_a_failed_run_falls_back_for_just_those_pages(monkeypatch, fresh_metrics):
    calls = []

    def flaky_convert(data, filename, **kwargs):
        calls.append(1)
        if len(calls) == 1:
            raise glm_ocr_client.GlmOcrUnavailable("asleep")
        return "GLM read the second run"

    _enable_glm_ocr(monkeypatch, flaky_convert)
    _fake_local_ocr(monkeypatch)
    pdf = _pdf_of(_make_scanned_pdf("scan"), _make_pdf("Digital middle page with a text layer"),
                  _make_scanned_pdf("scan"))

    result = file_to_markdown(pdf, "mixed.pdf", engine="glm-ocr")

    assert "local OCR of page 0" in result
    assert "GLM read the second run" in result
    assert "local OCR of page 2" not in result
    snapshot = fresh_metrics.snapshot()
    assert snapshot["engines"] == {"glm-ocr": 1}
    assert snapshot["glm_ocr"]["fallbacks"] == 1
    assert snapshot["glm_ocr"]["successes"] == 1


def test_a_mixed_pdf_is_local_ocr_when_every_run_fails(monkeypatch, fresh_metrics):
    def failing_convert(data, filename, **kwargs):
        raise glm_ocr_client.GlmOcrBadDocument("refused")

    _enable_glm_ocr(monkeypatch, failing_convert)
    _fake_local_ocr(monkeypatch)
    pdf = _pdf_of(_make_pdf("Digital first page with a text layer"), _make_scanned_pdf("scan"))

    result = file_to_markdown(pdf, "mixed.pdf", engine="glm-ocr")

    assert "Digital first page" in result and "local OCR of page 1" in result
    assert fresh_metrics.snapshot()["engines"] == {"ocr": 1}


def test_force_mode_sends_every_page_to_glm_ocr(monkeypatch, fresh_metrics):
    monkeypatch.setenv("WISEAU_OCR_MODE", "force")
    calls = []

    def fake_convert(data, filename, **kwargs):
        calls.append(_page_count(data))
        return "GLM read everything"

    _enable_glm_ocr(monkeypatch, fake_convert)
    pdf = _pdf_of(_make_pdf("Digital page with a text layer"), _make_scanned_pdf("scan"))

    assert file_to_markdown(pdf, "doc.pdf", engine="glm-ocr").strip() == "GLM read everything"
    assert calls == [2]


def test_an_image_upload_still_goes_to_glm_ocr_whole(monkeypatch, fresh_metrics):
    calls = []
    _enable_glm_ocr(monkeypatch, lambda data, filename, **k: calls.append(filename) or "GLM read the photo")
    pix = pymupdf.open(stream=_make_scanned_pdf("x"), filetype="pdf")[0].get_pixmap(dpi=50)

    result = file_to_markdown(pix.tobytes("png"), "photo.png", engine="glm-ocr")

    assert calls == ["photo.png"] and "GLM read the photo" in result


# --- ADR-040: an invisible OCR layer over a scan is not trusted ---------------------


def _make_scan_with_invisible_layer(picture_text: str, layer_text: str) -> bytes:
    """A scan (full-page image of ``picture_text``) carrying an invisible text layer.

    The layer says ``layer_text`` in render mode 3, the way Acrobat's Paper Capture
    (or any OCR-to-PDF tool) leaves its guess under the picture. Making the two
    differ lets a test tell which one the converter read.
    """
    scan = pymupdf.open(stream=_make_scanned_pdf(picture_text), filetype="pdf")
    page = scan[0]
    page.insert_text((72, 120), layer_text, fontsize=11, render_mode=3)
    data = scan.tobytes()
    scan.close()
    return data


def test_invisible_layer_over_a_scan_counts_as_a_scan():
    data = _make_scan_with_invisible_layer("Picture", "An earlier OCR engine guessed this text")
    page = pymupdf.open(stream=data, filetype="pdf")[0]

    assert file_parser._invisible_layer_over_scan(page)
    assert file_parser._page_needs_ocr(page)


def test_visible_text_is_never_treated_as_a_scan():
    page = pymupdf.open(stream=_make_pdf("A born-digital page with a real text layer"), filetype="pdf")[0]

    assert not file_parser._invisible_layer_over_scan(page)
    assert not file_parser._page_needs_ocr(page)


def test_hidden_text_beside_visible_text_keeps_the_native_path():
    # A born-digital page that also hides some text (a watermark, a search key)
    # still has visible text, so its layer is its own and is read directly.
    doc = pymupdf.open(stream=_make_pdf("A born-digital page with a real text layer"), filetype="pdf")
    doc[0].insert_text((72, 300), "hidden search keywords", render_mode=3)
    page = pymupdf.open(stream=doc.tobytes(), filetype="pdf")[0]

    assert not file_parser._page_needs_ocr(page)


def test_invisible_text_without_a_full_page_image_keeps_the_native_path():
    doc = pymupdf.open()
    doc.new_page().insert_text((72, 72), "Invisible text on a page with no picture at all", render_mode=3)
    page = pymupdf.open(stream=doc.tobytes(), filetype="pdf")[0]

    assert not file_parser._invisible_layer_over_scan(page)


def test_scan_with_invisible_layer_is_read_from_its_pixels(monkeypatch):
    _fake_local_ocr(monkeypatch)
    data = _make_scan_with_invisible_layer("Picture", "An earlier OCR engine guessed this text")

    result = file_to_markdown(data, "scan.pdf", engine="pymupdf")

    assert "local OCR of page 0" in result
    assert "guessed" not in result


def test_trusting_the_invisible_layer_can_be_switched_back_on(monkeypatch):
    _fake_local_ocr(monkeypatch)
    monkeypatch.setattr(file_parser, "OCR_INVISIBLE_LAYER", False)
    data = _make_scan_with_invisible_layer("Picture", "An earlier OCR engine guessed this text")

    result = file_to_markdown(data, "scan.pdf", engine="pymupdf")

    assert "guessed this text" in result
    assert "local OCR" not in result
