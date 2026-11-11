"""Paragraphs broken by a column or a page (roadmap 3.3, ADR-051).

A paragraph that runs off the foot of a column carries on at the top of the
next one, or on the next page. The engines see two blocks; a reader sees one.
This pass finds those breaks and records them as `continues_from` relations
(later block → earlier block). It never rewrites text:

* **Column break** (both blocks on one page, the later one to the right of the
  earlier and starting higher up): `signal="continuation-column"`. The
  serializer writes the two as one paragraph, the second's text straight after
  the first's on a new line, so the Markdown block is whole. Both elements keep
  their own text and boxes in the IR.
* **Page break** (the earlier block is its page's last body block, the later
  one the next page's first): `signal="continuation-page"`. Recorded only; the
  Markdown keeps the page boundary, as the structural gold set does.

A line-end hyphen stays in the text (`pig-` / `mented`): whether it is a soft
hyphen or a real one is not something this pass can know, so the join is the
recorded operation and the reader (or a later step) decides.

The test, all of it required:

* the earlier block is body text (not a heading, list, table, caption, ...),
  and its last line does not end a sentence: no `.`, `!`, `?` or `:` (after any
  closing quote or bracket), or it ends in a hyphen;
* the later block is body text whose first word starts with a lower-case
  letter: a new paragraph starts with a capital, a number or a quote mark far
  more often than mid-sentence text does;
* between the two, in reading order, there is nothing but figures, captions,
  page furniture, empty elements and anything inside the page's top or bottom
  edge (`NOISE_BAND`; scanner noise there is common). A heading or a table between them ends the
  paragraph.

When the test fails the pair is left alone: no relation is better than a wrong
one, which would join two paragraphs that are not one.
"""

from __future__ import annotations

import re
from typing import Optional

from . import document as ir
from . import recover

COLUMN = "continuation-column"
PAGE = "continuation-page"

_SKIPPABLE = {ir.FIGURE, ir.CAPTION, ir.PAGE_HEADER, ir.PAGE_FOOTER, ir.PAGE_NUMBER}
# Markdown-written body blocks that are not running prose.
_NOT_PROSE = re.compile(r"^\s*(?:#|\||>|[-*+]\s|\d{1,3}[.)]\s|```|<table)")
_ENDS_SENTENCE = re.compile(r"[.!?:][\"'’”)\]*_]*\s*$")
# A block that ends by closing a bracket has closed a parenthetical (a photo
# credit, "(Photograph supplied by applicant)"; OCR often reads ")" as "}").
_ENDS_BRACKET = re.compile(r"[)\]}]\s*$")
_ENDS_HYPHEN = re.compile(r"[A-Za-z][-‐]\s*$")
_STARTS_LOWER = re.compile(r"^[\s\"'‘“(*_]*[a-z]")


# Fewer words than this is a label, a line number or noise, not running prose.
# (Added after held-out set T: T14's margin line number "30" was joined to a
# paragraph.)
MIN_WORDS = 3


def _is_prose(el: ir.Element) -> bool:
    if el.type not in recover.BODY_TYPES:
        return False
    text = el.text.strip()
    if len(re.findall(r"[^\W\d_]{2,}", text)) < MIN_WORDS:
        return False
    return not (el.text_format == ir.MARKDOWN and _NOT_PROSE.match(text))


def _last_line(el: ir.Element) -> str:
    text = recover.plain_text(el)
    return text.rsplit("\n", 1)[-1] if text else ""


def _open_end(el: ir.Element) -> bool:
    last = _last_line(el)
    if not last.strip():
        return False
    if _ENDS_HYPHEN.search(last):
        return True
    return not (_ENDS_SENTENCE.search(last) or _ENDS_BRACKET.search(last))


def _lower_start(el: ir.Element) -> bool:
    return bool(_STARTS_LOWER.match(recover.plain_text(el)))


def _column_break(a: ir.Element, b: ir.Element) -> bool:
    if a.bbox is None or b.bbox is None:
        return False
    return b.bbox.x0 >= a.bbox.x1 - 0.1 * (a.bbox.x1 - a.bbox.x0) and b.bbox.y0 < a.bbox.y0


# The outer edge of the page, as a share of its height: a block entirely in it
# is scanner noise or furniture. Narrower than the furniture band, because a
# short paragraph continued from the previous page can end inside that.
NOISE_BAND = 0.06


def _in_margin(doc: ir.Document, el: ir.Element) -> bool:
    """Entirely inside the page's outer top or bottom edge (`NOISE_BAND`)."""
    height = recover.page_size(doc, el.page)[1]
    if el.bbox is None or not height:
        return False
    return el.bbox.y1 <= NOISE_BAND * height or el.bbox.y0 >= (1 - NOISE_BAND) * height


def _next_body(doc: ir.Document, start: int) -> Optional[int]:
    """The next element after `start` that is not skippable, or None."""
    for j in range(start + 1, len(doc.elements)):
        el = doc.elements[j]
        if el.type in _SKIPPABLE or not el.text.strip() or _in_margin(doc, el):
            continue
        return j
    return None


def apply(doc: ir.Document) -> None:
    for i, a in enumerate(doc.elements):
        if not _is_prose(a) or not _open_end(a) or _in_margin(doc, a):
            continue
        j = _next_body(doc, i)
        if j is None:
            continue
        b = doc.elements[j]
        if not _is_prose(b) or not _lower_start(b):
            continue
        if a.page is not None and b.page == a.page and _column_break(a, b):
            signal = COLUMN
        elif a.page is not None and b.page is not None and b.page == a.page + 1:
            signal = PAGE
        else:
            continue
        doc.relations.append(ir.Relation("continues_from", b.id, a.id, signal))
