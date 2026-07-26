"""URL -> Markdown extraction.

Renders the page with a headless browser (so client-side content is present),
then extracts the primary semantic content algorithmically with Trafilatura.
Markdownify is used only as a fallback when Trafilatura returns nothing, so the
pipeline stays deterministic rather than depending on per-site CSS selectors.

**Direct-PDF links (Phase 6; ADR-014).** A large share of real-world government
"pages" are not HTML at all — the link resolves straight to a PDF. Rendering one
in Chrome yields the PDF *viewer shell*, an empty `<embed>` document with no
text to extract, so the old path returned near-empty Markdown. Such responses are
now detected, downloaded as bytes **through the browser's own session** (keeping
the WAF clearance the render just earned — see `browser.fetch_bytes`), and handed
to `file_to_markdown`, i.e. the same docling-first-with-fallback document
pipeline `/convert/file` uses. HTML pages are untouched: they stay on the
render → Trafilatura → cleaner path.

**Short-document repair (ADR-020).** Trafilatura duplicates the body of any page
whose extracted text falls under its 250-character threshold — a documented
consequence of its wild-text rescue, not of anything here. `_drop_repeated_run`
removes the exact adjacent repeat; see that function for the full reasoning.
"""

from __future__ import annotations

import logging
import os
import re
import urllib.parse
from dataclasses import dataclass
from typing import Optional

import trafilatura
from markdownify import markdownify as html_to_md
from observability import metrics

from .browser import fetch_bytes, initialize_driver
from .cleaner import clean_markdown
from .file_parser import file_to_markdown

logger = logging.getLogger("markdown_engine.url")

PAGE_LOAD_TIMEOUT = 45  # seconds

# Every PDF starts with this signature. Byte-level verification is what makes the
# routing safe: the heuristics below only decide whether to *try* a download —
# nothing is treated as a PDF unless the bytes actually say so.
PDF_MAGIC = b"%PDF-"

# Chrome renders a PDF response inside its built-in viewer, whose DOM is a lone
# `<embed type="application/pdf">`. That marker is the signal that the URL served
# a document rather than a page.
_PDF_EMBED_RE = re.compile(r"<embed[^>]+type=[\"']application/pdf[\"']", re.IGNORECASE)

# Characters kept when deriving a filename from a URL; everything else collapses
# to "-" so a hostile path can't smuggle quotes/newlines into the multipart body.
_UNSAFE_FILENAME_CHARS = re.compile(r"[^A-Za-z0-9._-]+")
_MAX_FILENAME_LENGTH = 100

# Guard rails for the short-document repair below (see `_drop_repeated_run`).
# Only short extractions can be affected, so the scan never runs on a large
# document — which also keeps its cost trivial.
_MAX_REPAIR_BLOCKS = 60
_MIN_REPEATED_RUN_CHARS = 40


@dataclass(frozen=True)
class FetchedPage:
    """What a URL actually served: a rendered page, or a document's bytes."""

    url: str
    html: str
    pdf_bytes: Optional[bytes] = None

    @property
    def is_pdf(self) -> bool:
        """True when the URL served a PDF (verified by magic bytes)."""
        return self.pdf_bytes is not None


def _looks_like_pdf_viewer(html: str) -> bool:
    """True when the rendered DOM is Chrome's PDF viewer rather than a page."""
    return bool(_PDF_EMBED_RE.search(html or ""))


def _url_path_looks_like_pdf(url: str) -> bool:
    """True when the URL's *path* ends in `.pdf` (query strings ignored)."""
    return urllib.parse.urlparse(url).path.lower().endswith(".pdf")


def pdf_filename_for(url: str) -> str:
    """Derive a safe `.pdf` filename from a URL, for the document pipeline.

    Only the extension is load-bearing (`file_to_markdown` dispatches on it, and
    docling sniffs the format from it); the stem is for logs and error messages.
    """
    path = urllib.parse.urlparse(url).path
    name = urllib.parse.unquote(os.path.basename(path))
    name = _UNSAFE_FILENAME_CHARS.sub("-", name).strip("-")
    if name.lower().endswith(".pdf"):
        name = name[: -len(".pdf")]
    name = name[:_MAX_FILENAME_LENGTH].strip("-.") or "document"
    return f"{name}.pdf"


def _drop_repeated_run(markdown: str) -> str:
    """Remove a block run that Trafilatura emitted twice back to back.

    **Why this exists.** When Trafilatura's own extraction yields less than its
    `MIN_EXTRACTED_SIZE` (250 characters) of text, `extract_content` calls
    `recover_wild_text`, which *extends* the already-populated result body with
    every `<p>`/`<table>`/... it can find in the document — including the ones
    already extracted. Short pages therefore come back with their body present
    twice. Reproduced by calling `trafilatura.extract` directly with our options,
    so it is upstream behaviour, not something this pipeline introduces
    (ADR-020). Short government notices are exactly the shape that trips it.

    The repair is deliberately narrow: find the **longest** run of blocks that is
    immediately followed by an identical run, and drop the second copy. Anything
    that is not an exact, adjacent, substantial repeat is left alone, and a
    document long enough to be unaffected by the upstream bug is not even
    scanned. Deterministic: same input, same output (invariant #1).

    Delete this once upstream stops double-counting recovered text.
    """
    blocks = markdown.split("\n\n")
    count = len(blocks)
    if count < 2 or count > _MAX_REPAIR_BLOCKS:
        return markdown

    for size in range(count // 2, 0, -1):
        for start in range(count - 2 * size + 1):
            first = blocks[start : start + size]
            if first != blocks[start + size : start + 2 * size]:
                continue
            # A repeated one-word line ("Yes", a table cell) is plausibly real
            # content; a repeated run of substance is the upstream artifact.
            if sum(len(block) for block in first) < _MIN_REPEATED_RUN_CHARS:
                continue
            return "\n\n".join(blocks[: start + size] + blocks[start + 2 * size :])
    return markdown


def fetch_rendered(url: str) -> FetchedPage:
    """Load a URL in headless Chrome; return its rendered DOM or its PDF bytes.

    A download is attempted only when the response looks like a PDF (Chrome's
    viewer DOM, or a `.pdf` path), and the result is accepted only if it really
    starts with `%PDF-`. Anything else — including a `.pdf` URL that actually
    serves an HTML error/consent page — continues down the HTML path.
    """
    driver = initialize_driver()
    try:
        driver.set_page_load_timeout(PAGE_LOAD_TIMEOUT)
        driver.get(url)
        html = driver.page_source

        is_viewer = _looks_like_pdf_viewer(html)
        if is_viewer or _url_path_looks_like_pdf(url):
            data = fetch_bytes(driver, url)
            if data and data.startswith(PDF_MAGIC):
                return FetchedPage(url=url, html=html, pdf_bytes=data)
            if is_viewer:
                # Unambiguously a PDF, but the bytes are out of reach. Extracting
                # the empty viewer shell would return plausible-looking nothing,
                # so fail loudly instead (main.py maps this to a 502).
                raise RuntimeError(
                    "The URL served a PDF that could not be downloaded for conversion; "
                    "try uploading the file to /convert/file instead."
                )
        return FetchedPage(url=url, html=html)
    finally:
        driver.quit()


def fetch_rendered_html(url: str) -> str:
    """Load a URL in headless Chrome and return the rendered DOM as HTML."""
    return fetch_rendered(url).html


def url_to_markdown(url: str) -> str:
    """Convert a live URL into clean Markdown."""
    page = fetch_rendered(url)

    if page.is_pdf:
        filename = pdf_filename_for(url)
        logger.info(
            "URL served a PDF; converting as a document",
            extra={"wiseau": {"url": url, "bytes": len(page.pdf_bytes), "filename": filename}},
        )
        # The document pipeline: docling-first with automatic fallback, cleaned.
        # It records its own engine attribution, so none is recorded here.
        return file_to_markdown(page.pdf_bytes, filename)

    extracted = trafilatura.extract(
        page.html,
        url=url,
        output_format="markdown",
        include_links=True,
        include_tables=True,
        favor_precision=True,
    )

    if not extracted:
        logger.info(
            "Trafilatura returned empty; falling back to markdownify",
            extra={"wiseau": {"url": url}},
        )
        metrics.record_engine("markdownify")
        extracted = html_to_md(page.html, heading_style="ATX")
    else:
        metrics.record_engine("trafilatura")
        extracted = _drop_repeated_run(extracted)

    return clean_markdown(extracted)
