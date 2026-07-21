"""Opt-in live-browser test for the URL rendering pipeline.

The rest of the suite mocks ``url_to_markdown`` so CI needs no Chromium. This
module actually launches headless Chrome and drives the real
render -> Trafilatura -> cleaner path, so it is **skipped by default** and only
runs when ``WISEAU_LIVE_BROWSER=1`` is set and Selenium can start a driver.

It uses a ``data:`` URL rather than a network fetch, so it exercises the full
browser + extraction pipeline without depending on outbound egress. To run it::

    # Chromium + a matching chromedriver must be resolvable. Selenium Manager
    # will fetch a matching driver automatically for the detected browser, or
    # set CHROME_BIN / CHROMEDRIVER_PATH to pin explicit binaries.
    WISEAU_LIVE_BROWSER=1 pytest tests/test_browser_live.py
"""

from __future__ import annotations

import os

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
