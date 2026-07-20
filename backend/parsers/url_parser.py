"""URL -> Markdown extraction.

Renders the page with a headless browser (so client-side content is present),
then extracts the primary semantic content algorithmically with Trafilatura.
Markdownify is used only as a fallback when Trafilatura returns nothing, so the
pipeline stays deterministic rather than depending on per-site CSS selectors.
"""

from __future__ import annotations

import logging

import trafilatura
from markdownify import markdownify as html_to_md

from .browser import initialize_driver
from .cleaner import clean_markdown

logger = logging.getLogger("markdown_engine.url")

PAGE_LOAD_TIMEOUT = 45  # seconds


def fetch_rendered_html(url: str) -> str:
    """Load a URL in headless Chrome and return the rendered DOM as HTML."""
    driver = initialize_driver()
    try:
        driver.set_page_load_timeout(PAGE_LOAD_TIMEOUT)
        driver.get(url)
        return driver.page_source
    finally:
        driver.quit()


def url_to_markdown(url: str) -> str:
    """Convert a live URL into clean Markdown."""
    html = fetch_rendered_html(url)

    extracted = trafilatura.extract(
        html,
        url=url,
        output_format="markdown",
        include_links=True,
        include_tables=True,
        favor_precision=True,
    )

    if not extracted:
        logger.info("Trafilatura returned empty for %s; falling back to markdownify", url)
        extracted = html_to_md(html, heading_style="ATX")

    return clean_markdown(extracted)
