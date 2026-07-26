"""Opt-in live-browser test for the URL rendering pipeline.

The rest of the suite mocks ``url_to_markdown`` so CI needs no Chromium. This
module actually launches headless Chrome and drives the real
render -> Trafilatura -> cleaner path, so it is **skipped by default** and only
runs when ``WISEAU_LIVE_BROWSER=1`` is set and Selenium can start a driver.

Nothing here needs outbound egress: every fixture — the HTML pages and the
direct-PDF case (ADR-017) — is served over loopback HTTP, so the full browser +
extraction pipeline is exercised in a sandbox. Loopback is a non-public address,
which the private-address guard (ADR-021) refuses by default, so these tests set
``WISEAU_ALLOW_PRIVATE_URLS=1`` — the same opt-out a self-hosted deployment uses
to convert its own intranet. To run them::

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


@pytest.fixture(autouse=True)
def _allow_loopback(monkeypatch):
    """Every fixture here is served from loopback, which the guard blocks by default."""
    monkeypatch.setenv("WISEAU_ALLOW_PRIVATE_URLS", "1")


# A self-contained page: the extractor must pull the article content out of it.
_ARTICLE_HTML = (
    "<html><head><title>Doc</title></head><body><article>"
    "<h1>Live Render Works</h1>"
    "<p>Rendered by headless Chromium and extracted deterministically.</p>"
    "</article></body></html>"
)

# The direct-PDF case. The faked-driver suite (`test_url_parser.py`) cannot answer
# the one question that matters here: whether Chrome's PDF *viewer* context can
# `fetch()` its own URL, which is how the bytes are recovered.
_PDF_LINES = (
    "Estimates Statement 2026 - Program 1.2",
    "Total departmental funding rose to 412.6 million dollars this year.",
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


@pytest.fixture(scope="module")
def _fixture_server(tmp_path_factory):
    """Serve the article, a generated PDF, and an HTML page on loopback."""
    import pymupdf

    root = tmp_path_factory.mktemp("served")
    doc = pymupdf.open()
    page = doc.new_page()
    for offset, line in enumerate(_PDF_LINES):
        page.insert_text((72, 72 + offset * 28), line, fontsize=13)
    doc.save(str(root / "report.pdf"))
    doc.close()
    (root / "article.html").write_text(_ARTICLE_HTML)
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


def test_live_render_extracts_heading_and_body(_driver_available, _fixture_server):
    from parsers.url_parser import url_to_markdown

    markdown = url_to_markdown(f"{_fixture_server}/article.html")
    assert "# Live Render Works" in markdown
    assert "Rendered by headless Chromium" in markdown


def test_live_render_is_deterministic(_driver_available, _fixture_server):
    from parsers.url_parser import url_to_markdown

    url = f"{_fixture_server}/article.html"
    assert url_to_markdown(url) == url_to_markdown(url)


def test_live_direct_pdf_url_is_converted_as_a_document(_driver_available, _fixture_server):
    from parsers.url_parser import url_to_markdown

    markdown = url_to_markdown(f"{_fixture_server}/report.pdf")
    for line in _PDF_LINES:
        assert line in markdown


def test_live_html_page_is_not_treated_as_a_document(_driver_available, _fixture_server):
    from parsers.url_parser import url_to_markdown

    markdown = url_to_markdown(f"{_fixture_server}/index.html")
    assert "# An HTML Page" in markdown
    assert "Trafilatura path" in markdown


def test_live_render_refuses_loopback_without_the_opt_out(_driver_available, _fixture_server, monkeypatch):
    """With the guard enforced, the very same loopback URL must be refused.

    This is the guard doing its job against a real address rather than a stubbed
    resolver: `127.0.0.1` is reachable from the container and from nowhere a
    caller sits, which is the SSRF case ADR-021 closes.
    """
    from parsers.url_parser import BlockedUrlError, url_to_markdown

    monkeypatch.delenv("WISEAU_ALLOW_PRIVATE_URLS", raising=False)
    with pytest.raises(BlockedUrlError):
        url_to_markdown(f"{_fixture_server}/article.html")
