"""Heading levels (roadmap 3.1, ADR-049).

The layout pass (ADR-048) finds headings on scans but writes every one as `##`;
PyMuPDF4LLM levels a born-digital page's headings by font size alone. This pass
gives headings a hierarchy from, in order of trust:

1. **The PDF's own outline** (bookmarks, `get_toc()`): a heading whose words are
   an outline entry's title on the entry's page takes the entry's level. An
   author or a publishing tool wrote that hierarchy down, so it outranks any
   inference. Outline levels are kept as they are; every other heading in that
   document sits below the deepest outline level, in the order the next signals
   give it.
2. **Numbering**: `PART 2`, `APPENDIX 1`, `CHAPTER 3` (a division of the whole
   document) rank above `1.`, which ranks above `1.1`, then `(a)` / `a)`, then
   `(i)`. Numbering ranks a heading only against headings in the same style
   (see 3): a lettered heading goes one level under the unlettered headings of
   its own size and case.
3. **Typography**: the printed size of the heading's letters, measured from its
   OCR lines (a line box is ascender to descender, so a line with no descender,
   such as one in capitals, is scaled up to compare), then capitals over mixed
   case at the same size.

Sizes are grouped into tiers: headings whose sizes are within `SIZE_STEP` of
each other share a tier. Each heading's style is (numbering division, size tier,
capitals, lettered), and styles are ranked in that order to give levels 1, 2,
3, ... (at most 6).

**It refuses when it cannot tell.** With fewer than two headings, or when every
heading has the same style, levels are left as the engine set them. Text-layer
(PyMuPDF4LLM) headings carry no line boxes, so on a born-digital page only the
outline signal is used. The heading's text is never edited: the level and the
signal that set it (`level_signal`) are recorded beside it, and the serializer
writes the `#` marker from them.
"""

from __future__ import annotations

import re
import statistics
import unicodedata
from typing import Optional, Sequence

from . import document as ir
from . import recover

OUTLINE = "pdf-outline"
TYPOGRAPHY = "heading-typography"

# Two headings whose estimated sizes differ by less than this share of the body
# text's size print in the same size.
SIZE_STEP = 0.25
# A line with no descender is drawn this much shorter than one with both
# ascenders and descenders, at the same font size (Tesseract's boxes).
NO_DESCENDER = 0.78
_DESCENDERS = set("gjpqy(),;[]{}|")

_DIVISION = re.compile(r"^\s*(?:PART|APPENDIX|SCHEDULE|CHAPTER|ANNEX|ANNEXURE|SECTION)\b\s*[\dIVXLC]*",
                       re.I)
_LETTERED = re.compile(r"^\s*\(?(?:[a-z]|[ivx]{1,4})\)\s+\S")
_DECIMAL = re.compile(r"^\s*(\d{1,2}(?:\.\d{1,2}){0,4})\.?\s+\S")


def _norm(text: str) -> str:
    text = unicodedata.normalize("NFKC", text).lower()
    return " ".join(re.findall(r"[^\W_]+", text))


def _line_size(line) -> Optional[float]:
    h = line.y1 - line.y0
    text = line.text.strip()
    if h <= 0 or not text or text.startswith("("):
        return None  # a parenthetical (a botanical name) is set in its own style
    return h if any(c in _DESCENDERS for c in text) else h / NO_DESCENDER


def _size(el: ir.Element) -> Optional[float]:
    sizes = [s for s in (_line_size(ln) for ln in el.lines) if s]
    return statistics.median(sizes) if sizes else None


def _caps(text: str) -> bool:
    letters = [c for c in text if c.isalpha()]
    return len(letters) >= 3 and sum(c.isupper() for c in letters) >= 0.8 * len(letters)


def _first_line(el: ir.Element) -> str:
    return recover.plain_text(el).split("\n", 1)[0]


def _decimal_depth(text: str) -> Optional[int]:
    m = _DECIMAL.match(text)
    return m.group(1).count(".") + 1 if m else None


def _is_heading(el: ir.Element) -> bool:
    return el.type in (ir.HEADING, ir.TITLE)


def _outline_levels(doc: ir.Document, outline: Sequence) -> dict[int, int]:
    """Element index -> outline level, for headings (or short blocks) that are an
    outline entry's title on its page."""
    found: dict[int, int] = {}
    for entry in outline:
        level, title, page = int(entry[0]), str(entry[1]), int(entry[2])
        want = _norm(title)
        if not want or page < 1:
            continue
        for i, el in enumerate(doc.elements):
            if el.page != page or i in found:
                continue
            text = recover.plain_text(el)
            if text.count("\n") > 2 or el.type not in (ir.HEADING, ir.TITLE, ir.TEXT, ir.PARAGRAPH):
                continue
            if _norm(text) == want:
                found[i] = level
                break
    return found


def _typography_levels(doc: ir.Document, indices: list[int]) -> dict[int, int]:
    """Index -> level (1-based) by numbering and typography; {} to refuse."""
    if len(indices) < 2:
        return {}
    body = [s for el in doc.elements if el.type in recover.BODY_TYPES
            for s in (_line_size(ln) for ln in el.lines) if s]
    if not body:
        return {}
    body_size = statistics.median(body)
    sizes = {i: _size(doc.elements[i]) for i in indices}
    # A heading with no measurable line (all of it a parenthetical) keeps its level.
    indices = [i for i in indices if sizes[i] is not None]
    if len(indices) < 2:
        return {}
    # Size tiers, largest first: a tier holds the headings within SIZE_STEP (of
    # the body size) of its largest one.
    order = sorted(indices, key=lambda i: (-sizes[i], i))
    tier, top = {}, None
    t = -1
    for i in order:
        if top is None or (top - sizes[i]) / body_size >= SIZE_STEP:
            t, top = t + 1, sizes[i]
        tier[i] = t
    style = {}
    for i in indices:
        text = _first_line(doc.elements[i])
        division = bool(_DIVISION.match(text))
        depth = _decimal_depth(text) or 0
        style[i] = (0 if division else 1, depth, tier[i], 0 if _caps(text) else 1,
                    1 if _LETTERED.match(text) else 0)
    ranked = sorted(set(style.values()))
    if len(ranked) < 2:
        return {}
    rank = {s: min(k + 1, 6) for k, s in enumerate(ranked)}
    return {i: rank[style[i]] for i in indices}


def apply(doc: ir.Document, *, outline: Sequence = ()) -> None:
    from dataclasses import replace
    outline_found = _outline_levels(doc, outline) if outline else {}
    for i, level in outline_found.items():
        el = doc.elements[i]
        changes = {"level": level, "level_signal": OUTLINE}
        if not _is_heading(el):
            changes.update(type=ir.HEADING, role_signal=OUTLINE)
        doc.elements[i] = replace(el, **changes)
    offset = max(outline_found.values(), default=0)
    rest = [i for i, el in enumerate(doc.elements)
            if _is_heading(el) and i not in outline_found and el.text_format == ir.PLAIN and el.lines]
    for i, level in _typography_levels(doc, rest).items():
        doc.elements[i] = replace(doc.elements[i], level=min(level + offset, 6), level_signal=TYPOGRAPHY)
