"""Page furniture: running headers, footers and page numbers (roadmap 3.2, ADR-050).

A page's furniture is printed for the reader of the paper copy: the journal's
name and issue on every page, the page number, a printer's line. It is not part
of the document's text in reading order, and in a long conversion it breaks
paragraphs and tables apart every page. This pass types it, so the serializer
can leave it out in reader mode (`WISEAU_FURNITURE=drop`). By default it is
still written, as read: faithful mode keeps every word, and the element's type
is the page-level annotation.

Furniture lives in the page's margins: an element entirely within the top
`BAND` of the page height, or entirely within the bottom one. Within a band:

* **Page number**: the element's text is a page number and nothing else
  (`17`, `- 7 -`, `Page 3`, `3 of 20`, `1a`, `xii`).
* **Repeated across pages** (documents of two or more pages): an element whose
  text, with its digits masked, recurs in the same band on at least
  `MIN_REPEAT_PAGES` pages and at least a quarter of the document's pages: a
  running header (top band) or footer (bottom band).
* **A running head on its own**, on a text-layer page only (a single page, or
  one the repetition test cannot see): one short line in the top band that
  carries a number (an issue, a page, a document number) and no sentence:
  header. On a scan the layout model already has a `header` category, and a
  line at the top of a column there is as often the start of an entry
  ("Variety: 'Victoria' Application No. 88/025") as a running head.

Headings, titles, tables and figures are never re-typed. And the converse: an
element the layout model called furniture that lies outside both margins
(`BAND_OUTER`) is returned to plain text, because a running header in the
middle of a page is a misreading.

Text is never edited. A re-typed element records `role_signal`:
`furniture-number`, `furniture-repeat`, `furniture-head` or `furniture-outside`.
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import replace

from . import document as ir
from . import recover

# The top and bottom margin bands, as a share of the page height.
BAND = 0.10
# Outside this share at both ends an element is not furniture, whatever typed it.
BAND_OUTER = 0.12
MIN_REPEAT_PAGES = 3
MAX_HEAD_CHARS = 120

_NUMBER = re.compile(
    r"^[\s\-–—|.]*(?:page\s+)?(?:\d{1,4}[a-z]?|[ivxlc]{1,7})(?:\s+of\s+\d{1,4})?[\s\-–—|.]*$", re.I)
_DIGITS = re.compile(r"\d+")
_SENTENCE = re.compile(r"[a-z]{3,}[,;]?\s+[a-z]{3,}\s+[a-z]{3,}\s+[a-z]{3,}")
FURNITURE = {ir.PAGE_HEADER, ir.PAGE_FOOTER, ir.PAGE_NUMBER}
_NEVER = {ir.HEADING, ir.TITLE, ir.TABLE, ir.FIGURE, ir.CAPTION, ir.FOOTNOTE, ir.CODE, ir.EQUATION}


def _band(el: ir.Element, height) -> str | None:
    if el.bbox is None or not height:
        return None
    if el.bbox.y1 <= BAND * height:
        return "top"
    if el.bbox.y0 >= (1 - BAND) * height:
        return "bottom"
    return None


def _outside(el: ir.Element, height) -> bool:
    return (el.bbox is not None and bool(height) and el.bbox.y0 > BAND_OUTER * height
            and el.bbox.y1 < (1 - BAND_OUTER) * height)


def _key(text: str) -> str:
    return " ".join(_DIGITS.sub("#", text.lower()).split())


def _one_line(el: ir.Element, text: str) -> bool:
    return (len(el.lines) <= 1 if el.lines else "\n" not in text) and len(text) <= MAX_HEAD_CHARS


def apply(doc: ir.Document) -> None:
    groups = recover.page_groups(doc)
    candidates: list[tuple[int, str, str]] = []  # (index, band, plain text)
    for page, indices in groups:
        height = recover.page_size(doc, page)[1]
        for i in indices:
            el = doc.elements[i]
            if el.type in FURNITURE and _outside(el, height):
                recover.replace_element(doc, i, type=ir.TEXT, role_signal="furniture-outside")
                continue
            band = _band(el, height)
            if band is None or el.type in _NEVER:
                continue
            text = recover.plain_text(el)
            if text:
                candidates.append((i, band, text))

    n_pages = len([p for p, _ in groups if p])
    pages_by_key: dict[tuple[str, str], set[int]] = defaultdict(set)
    for i, band, text in candidates:
        pages_by_key[(band, _key(text))].add(doc.elements[i].page)
    need = max(MIN_REPEAT_PAGES, (n_pages + 3) // 4)

    for i, band, text in candidates:
        el = doc.elements[i]
        if _NUMBER.match(text):
            kind, signal = ir.PAGE_NUMBER, "furniture-number"
        elif n_pages >= 2 and len(pages_by_key[(band, _key(text))]) >= need:
            kind, signal = (ir.PAGE_HEADER if band == "top" else ir.PAGE_FOOTER), "furniture-repeat"
        elif band == "top" and el.text_format == ir.MARKDOWN and _one_line(el, text) and _DIGITS.search(text) and not _SENTENCE.search(text):
            kind, signal = ir.PAGE_HEADER, "furniture-head"
        else:
            continue
        if el.type != kind:
            doc.elements[i] = replace(el, type=kind, role_signal=signal)
