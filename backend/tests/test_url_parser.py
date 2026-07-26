"""Tests for URL fetching and the direct-PDF routing (Phase 6).

The suite stays browserless: `initialize_driver` is replaced with a fake driver
that returns a canned `page_source` and a canned in-page download result, so the
whole decision tree — HTML page vs Chrome's PDF viewer vs a `.pdf` URL that
actually serves HTML — is exercised without Chromium. The live-browser path is
covered separately (and opt-in) by `test_browser_live.py`.
"""

from __future__ import annotations

import base64

import pymupdf
import pytest

from parsers import browser, url_parser


def _make_pdf(text: str) -> bytes:
    """Build a minimal one-page PDF containing ``text``."""
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 72), text)
    data = doc.tobytes()
    doc.close()
    return data


# Chrome's PDF viewer DOM: a single <embed>, no extractable text.
_VIEWER_HTML = (
    '<html><head></head><body style="margin: 0px; background-color: rgb(38, 38, 38);">'
    '<embed name="A1B2" style="position:absolute; left: 0; top: 0;" src="about:blank" '
    'type="application/pdf" internalid="XYZ"></body></html>'
)

_ARTICLE_HTML = (
    "<html><head><title>Doc</title></head><body><article>"
    "<h1>Annual Report</h1><p>Revenue grew by twelve percent this year.</p>"
    "</article></body></html>"
)


class _FakeDriver:
    """Minimal stand-in for a Selenium driver, recording what it was asked."""

    def __init__(self, page_source: str, download: object = None):
        self.page_source = page_source
        self._download = download
        self.visited: list[str] = []
        self.script_args: list[tuple] = []
        self.page_load_timeout: int | None = None
        self.script_timeout: int | None = None
        self.quit_called = False

    def set_page_load_timeout(self, seconds: int) -> None:
        self.page_load_timeout = seconds

    def set_script_timeout(self, seconds: int) -> None:
        self.script_timeout = seconds

    def get(self, url: str) -> None:
        self.visited.append(url)

    def execute_async_script(self, script: str, *args):
        self.script_args.append(args)
        if isinstance(self._download, Exception):
            raise self._download
        return self._download

    def quit(self) -> None:
        self.quit_called = True


def _install_driver(monkeypatch, driver: _FakeDriver) -> _FakeDriver:
    monkeypatch.setattr(url_parser, "initialize_driver", lambda: driver)
    return driver


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


# --- Filename derivation ----------------------------------------------------
@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://example.gov/docs/annual-report.pdf", "annual-report.pdf"),
        ("https://example.gov/docs/Annual%20Report.PDF", "Annual-Report.pdf"),
        ("https://example.gov/download?id=42", "download.pdf"),
        ("https://example.gov/", "document.pdf"),
        ("https://example.gov/a b*c.pdf", "a-b-c.pdf"),
        ("https://example.gov/re%22port.pdf", "re-port.pdf"),  # quotes can't reach multipart
    ],
)
def test_pdf_filename_derivation(url, expected):
    assert url_parser.pdf_filename_for(url) == expected


def test_pdf_filename_is_bounded_and_always_pdf():
    name = url_parser.pdf_filename_for("https://example.gov/" + "x" * 300 + ".pdf")
    assert name.endswith(".pdf")
    assert len(name) <= 104  # 100-char stem + ".pdf"


# --- HTML pages keep the existing path --------------------------------------
def test_html_page_uses_the_extraction_path(monkeypatch):
    driver = _install_driver(monkeypatch, _FakeDriver(_ARTICLE_HTML))
    monkeypatch.setattr(
        url_parser,
        "file_to_markdown",
        lambda *a, **k: pytest.fail("an HTML page must not enter the document pipeline"),
    )

    markdown = url_parser.url_to_markdown("https://example.com/article")

    assert "Annual Report" in markdown
    assert "twelve percent" in markdown
    assert driver.page_load_timeout == url_parser.PAGE_LOAD_TIMEOUT
    assert driver.script_args == []  # no download attempted
    assert driver.quit_called


def test_html_page_is_not_probed_for_a_download(monkeypatch):
    # A plain page must not pay for an extra in-page fetch.
    driver = _install_driver(monkeypatch, _FakeDriver(_ARTICLE_HTML))
    page = url_parser.fetch_rendered("https://example.com/article")
    assert page.is_pdf is False
    assert page.pdf_bytes is None
    assert driver.script_args == []


# --- Direct PDFs route into the document pipeline ---------------------------
def test_pdf_viewer_response_routes_to_the_document_pipeline(monkeypatch):
    pdf = _make_pdf("Budget Estimates")
    driver = _install_driver(monkeypatch, _FakeDriver(_VIEWER_HTML, download=_b64(pdf)))

    seen: dict = {}

    def fake_file_to_markdown(data: bytes, filename: str) -> str:
        seen["data"] = data
        seen["filename"] = filename
        return "# From the document pipeline\n"

    monkeypatch.setattr(url_parser, "file_to_markdown", fake_file_to_markdown)

    markdown = url_parser.url_to_markdown("https://example.gov/papers/budget.pdf")

    assert markdown == "# From the document pipeline\n"
    assert seen["data"] == pdf
    assert seen["filename"] == "budget.pdf"
    assert driver.script_args == [("https://example.gov/papers/budget.pdf",)]
    assert driver.script_timeout == browser.DOWNLOAD_TIMEOUT
    assert driver.quit_called


def test_pdf_url_without_viewer_markup_is_still_downloaded(monkeypatch):
    # Some responses download rather than display; the `.pdf` path still probes.
    pdf = _make_pdf("Tabled Paper")
    _install_driver(monkeypatch, _FakeDriver("<html><body></body></html>", download=_b64(pdf)))
    monkeypatch.setattr(url_parser, "file_to_markdown", lambda data, filename: f"{filename}:{len(data)}")

    assert url_parser.url_to_markdown("https://example.gov/x/tabled.pdf?v=2") == f"tabled.pdf:{len(pdf)}"


def test_pdf_bytes_go_through_the_docling_first_pipeline(monkeypatch):
    """A URL-fetched PDF must get the same docling-first treatment as an upload."""
    from parsers import docling_client

    pdf = _make_pdf("Ignored — docling answers")
    _install_driver(monkeypatch, _FakeDriver(_VIEWER_HTML, download=_b64(pdf)))
    monkeypatch.delenv("WISEAU_PDF_ENGINE", raising=False)  # default is docling
    monkeypatch.setattr(docling_client, "is_configured", lambda: True)
    monkeypatch.setattr(
        docling_client,
        "convert_document",
        lambda data, filename, **kw: f"# docling read {filename}\n\nFaithful body.",
    )

    markdown = url_parser.url_to_markdown("https://example.gov/reports/estimates.pdf")

    assert "# docling read estimates.pdf" in markdown
    assert "Faithful body." in markdown
    # Still normalized like every other path (invariant #3): one trailing newline.
    assert markdown.endswith("\n") and not markdown.endswith("\n\n")


def test_url_pdf_falls_back_when_docling_is_unavailable(monkeypatch):
    from parsers import docling_client

    # Long enough to carry a real text layer, so the fallback stays on PyMuPDF's
    # native path and the test needs no OCR engine.
    pdf = _make_pdf("Fallback Text read by the deterministic parser.")
    _install_driver(monkeypatch, _FakeDriver(_VIEWER_HTML, download=_b64(pdf)))
    monkeypatch.delenv("WISEAU_PDF_ENGINE", raising=False)
    monkeypatch.setattr(docling_client, "is_configured", lambda: True)

    def cold_start(data, filename, **kw):
        raise docling_client.DoclingUnavailable("space is asleep")

    monkeypatch.setattr(docling_client, "convert_document", cold_start)

    markdown = url_parser.url_to_markdown("https://example.gov/reports/fallback.pdf")

    assert "Fallback Text" in markdown  # PyMuPDF read it instead


# --- Failure modes ----------------------------------------------------------
def test_non_pdf_bytes_fall_back_to_html_extraction(monkeypatch):
    # A `.pdf` URL that actually serves an HTML consent/error page.
    _install_driver(monkeypatch, _FakeDriver(_ARTICLE_HTML, download=_b64(b"<html>not a pdf</html>")))
    monkeypatch.setattr(
        url_parser,
        "file_to_markdown",
        lambda *a, **k: pytest.fail("non-PDF bytes must not enter the document pipeline"),
    )

    markdown = url_parser.url_to_markdown("https://example.gov/broken.pdf")

    assert "Annual Report" in markdown


def test_viewer_with_unavailable_bytes_raises(monkeypatch):
    driver = _install_driver(monkeypatch, _FakeDriver(_VIEWER_HTML, download=None))

    with pytest.raises(RuntimeError) as excinfo:
        url_parser.url_to_markdown("https://example.gov/blocked.pdf")

    assert "PDF" in str(excinfo.value)
    assert driver.quit_called  # the driver is released even on the error path


def test_download_script_failure_is_swallowed(monkeypatch):
    # A WebDriver error during the in-page fetch must degrade, not propagate.
    driver = _FakeDriver(_ARTICLE_HTML, download=RuntimeError("script timeout"))
    assert browser.fetch_bytes(driver, "https://example.gov/x.pdf") is None


def test_undecodable_download_payload_is_swallowed(monkeypatch):
    driver = _FakeDriver(_ARTICLE_HTML, download="not base64!!")
    assert browser.fetch_bytes(driver, "https://example.gov/x.pdf") is None


def test_empty_download_payload_is_none(monkeypatch):
    assert browser.fetch_bytes(_FakeDriver(_ARTICLE_HTML, download=""), "https://x/y.pdf") is None
    assert browser.fetch_bytes(_FakeDriver(_ARTICLE_HTML, download=None), "https://x/y.pdf") is None


def test_driver_is_released_when_rendering_fails(monkeypatch):
    class _ExplodingDriver(_FakeDriver):
        def get(self, url):
            raise RuntimeError("page load timeout")

    driver = _install_driver(monkeypatch, _ExplodingDriver(_ARTICLE_HTML))

    with pytest.raises(RuntimeError):
        url_parser.url_to_markdown("https://example.com/slow")
    assert driver.quit_called


def test_fetch_rendered_html_still_returns_the_dom(monkeypatch):
    # Back-compat: the old helper keeps its string contract.
    _install_driver(monkeypatch, _FakeDriver(_ARTICLE_HTML))
    assert url_parser.fetch_rendered_html("https://example.com/article") == _ARTICLE_HTML


# --- Short-document repair (ADR-020) ----------------------------------------
# Trafilatura duplicates the body of any page whose extracted text falls under
# its 250-character threshold. These tests pin both halves: the repair itself
# (pure, no browser) and the end-to-end result through the real extractor.
def test_short_page_body_is_not_duplicated(monkeypatch):
    _install_driver(monkeypatch, _FakeDriver(_ARTICLE_HTML))

    markdown = url_parser.url_to_markdown("https://example.com/article")

    assert markdown.count("Revenue grew by twelve percent this year.") == 1
    assert markdown.count("Annual Report") == 1


def test_short_page_repair_is_deterministic_and_idempotent(monkeypatch):
    _install_driver(monkeypatch, _FakeDriver(_ARTICLE_HTML))
    first = url_parser.url_to_markdown("https://example.com/article")
    _install_driver(monkeypatch, _FakeDriver(_ARTICLE_HTML))
    second = url_parser.url_to_markdown("https://example.com/article")

    assert first == second
    assert url_parser._drop_repeated_run(first) == first


def test_long_page_content_survives_untouched(monkeypatch):
    # Above Trafilatura's threshold there is no duplication to repair, and the
    # repair must not invent one: every paragraph has to come through intact.
    paragraphs = "".join(
        f"<p>Paragraph {n} of the annual report describes the funding round in some detail.</p>"
        for n in range(1, 8)
    )
    html = f"<html><body><article><h1>Annual Report</h1>{paragraphs}</article></body></html>"
    _install_driver(monkeypatch, _FakeDriver(html))

    markdown = url_parser.url_to_markdown("https://example.com/long")

    for n in range(1, 8):
        assert markdown.count(f"Paragraph {n} of the annual report") == 1


def test_drop_repeated_run_removes_the_longest_adjacent_repeat():
    body = "\n\n".join(["# Notice", "First paragraph of the notice.", "Second paragraph of the notice."])
    duplicated = body + "\n\n" + "\n\n".join(
        ["First paragraph of the notice.", "Second paragraph of the notice."]
    )
    assert url_parser._drop_repeated_run(duplicated) == body


def test_drop_repeated_run_handles_a_repeat_before_a_trailing_block():
    # Trafilatura appends comments after the body, so the duplicated run is not
    # always a suffix of the document.
    blocks = ["# Notice", "The registry office closes on Monday for the holiday.", "| a | b |"]
    text = "\n\n".join(blocks + blocks[1:] + ["Comment from a reader."])
    assert url_parser._drop_repeated_run(text) == "\n\n".join(blocks + ["Comment from a reader."])


def test_drop_repeated_run_leaves_unrepeated_text_alone():
    text = "\n\n".join(["# Title", "One paragraph of prose.", "Another, entirely different one."])
    assert url_parser._drop_repeated_run(text) == text


def test_drop_repeated_run_ignores_insubstantial_repeats():
    # Two identical short lines are plausibly real content (a table cell, a
    # yes/no answer), so they are left exactly as extracted.
    text = "\n\n".join(["# Form", "Yes", "Yes"])
    assert url_parser._drop_repeated_run(text) == text


def test_drop_repeated_run_skips_large_documents():
    # The upstream bug only affects short extractions, so a big document is
    # never scanned — cheap, and it cannot damage a long page.
    block = "A paragraph long enough to clear the substance threshold easily."
    blocks = [block] * (url_parser._MAX_REPAIR_BLOCKS + 2)
    text = "\n\n".join(blocks)
    assert url_parser._drop_repeated_run(text) == text
