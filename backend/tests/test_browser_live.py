"""Opt-in live-browser test for the URL rendering pipeline.

The rest of the suite mocks ``url_to_markdown`` so CI needs no Chromium. This
module actually launches headless Chrome and drives the real
render -> Trafilatura -> cleaner path, so it is **skipped by default** and only
runs when ``WISEAU_LIVE_BROWSER=1`` is set and Selenium can start a driver.

Nothing here needs outbound egress: the HTML case uses a ``data:`` URL and the
direct-PDF case (ADR-017) serves its fixtures over loopback HTTP, so the full
browser + extraction pipeline is exercised in a sandbox. To run it::

    # Chromium + a matching chromedriver must be resolvable. Selenium Manager
    # will fetch a matching driver automatically for the detected browser, or
    # set CHROME_BIN / CHROMEDRIVER_PATH to pin explicit binaries.
    WISEAU_LIVE_BROWSER=1 pytest tests/test_browser_live.py
"""

from __future__ import annotations

import functools
import http.server
import os
import socketserver
import threading

import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("WISEAU_LIVE_BROWSER") != "1",
    reason="live-browser test is opt-in; set WISEAU_LIVE_BROWSER=1 to run it",
)

# A self-contained page: the extractor must pull the article content out of it.
_DATA_URL = (
    "data:text/html,"
    "<html><head><title>Doc</title></head><body><article>"
    "<h1>Live Render Works</h1>"
    "<p>Rendered by headless Chromium and extracted deterministically.</p>"
    "</article></body></html>"
)


@pytest.fixture(scope="module")
def _driver_available():
    """Skip (rather than fail) if a browser/driver cannot be started here."""
    from parsers.browser import initialize_driver

    try:
        driver = initialize_driver()
    except Exception as exc:  # pragma: no cover - environment dependent
        pytest.skip(f"no usable Chrome/chromedriver in this environment: {exc}")
    driver.quit()


def test_live_render_extracts_heading_and_body(_driver_available):
    from parsers.url_parser import url_to_markdown

    markdown = url_to_markdown(_DATA_URL)
    assert "# Live Render Works" in markdown
    assert "Rendered by headless Chromium" in markdown


def test_live_render_is_deterministic(_driver_available):
    from parsers.url_parser import url_to_markdown

    assert url_to_markdown(_DATA_URL) == url_to_markdown(_DATA_URL)


# --- Direct-PDF URLs (ADR-017) ----------------------------------------------
# The faked-driver suite (`test_url_parser.py`) cannot answer the one question
# that matters here: whether Chrome's PDF *viewer* context can `fetch()` its own
# URL, which is how the bytes are recovered. This serves a real PDF over loopback
# HTTP and drives the whole path against real Chromium. Still no external egress.
_PDF_LINES = (
    "Estimates Statement 2026 - Program 1.2",
    "Total departmental funding rose to 412.6 million dollars this year.",
)


@pytest.fixture(scope="module")
def _pdf_server(tmp_path_factory):
    """Serve a generated PDF and an HTML page on loopback; yield the base URL."""
    import pymupdf

    root = tmp_path_factory.mktemp("served")
    doc = pymupdf.open()
    page = doc.new_page()
    for offset, line in enumerate(_PDF_LINES):
        page.insert_text((72, 72 + offset * 28), line, fontsize=13)
    doc.save(str(root / "report.pdf"))
    doc.close()
    (root / "index.html").write_text(
        "<html><head><title>Page</title></head><body><article><h1>An HTML Page</h1>"
        "<p>This one must stay on the Trafilatura path, not the document path.</p>"
        "</article></body></html>"
    )

    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(root))
    socketserver.TCPServer.allow_reuse_address = True
    httpd = socketserver.TCPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{httpd.server_address[1]}"
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_live_direct_pdf_url_is_converted_as_a_document(_driver_available, _pdf_server):
    from parsers.url_parser import url_to_markdown

    markdown = url_to_markdown(f"{_pdf_server}/report.pdf")
    for line in _PDF_LINES:
        assert line in markdown


def test_live_html_page_is_not_treated_as_a_document(_driver_available, _pdf_server):
    from parsers.url_parser import url_to_markdown

    markdown = url_to_markdown(f"{_pdf_server}/index.html")
    assert "# An HTML Page" in markdown
    assert "Trafilatura path" in markdown
