"""Captions and what they caption (roadmap 3.4, ADR-052).

A caption is only useful attached to its figure or table: exported on its own,
"(Photo supplied by applicant)" or "Table of Comparison of Rose Varieties" says
nothing. This pass links each caption to the figure or table it describes with a
`caption_of` relation (caption → figure/table), and types as captions the blocks
whose own words say they are one.

**Linking**, for every caption on a page: the candidates are the page's tables
and figures that overlap it horizontally (at least `MIN_OVERLAP` of the narrower
of the two) and lie directly above or below it, within `MAX_GAP` of the page
height, with nothing between them but other captions (a table's title and its
"(* = varieties used for comparison)" note both caption it). The nearest wins;
at equal distance, the one the caption's words name (`Table ...` → a table,
`Fig.` / `Photo` → a figure). No candidate, no relation.

**Typing**: a block (text, paragraph or heading) whose first line starts like a
caption (`Table`, `Fig.`, `Figure`, `Plate`, `Photo`, `Photograph`, `(* =`,
`(Photo...`) and that the linking rule attaches to a table or figure becomes a
caption (`role_signal="caption-pattern"`). One the rule cannot attach is left
as it is: "Table 3 shows..." in running text is not a caption.

Figures come from the layout pass (picture regions, ADR-048 as amended) and,
on text-layer pages, from nothing yet: PyMuPDF4LLM writes no image elements,
so a born-digital page's captions stay unlinked. Text is never edited.
"""

from __future__ import annotations

import re
from dataclasses import replace
from typing import Optional

from . import document as ir
from . import recover

SIGNAL = "caption-geometry"
MAX_GAP = 0.08
MIN_OVERLAP = 0.3

_CAPTION_START = re.compile(
    r"^\s*(?:\(\s*\*\s*=|\(\s*photo|(?:table|fig\.?|figure|plate|photo(?:graph)?)\b)", re.I)
_NAMES_TABLE = re.compile(r"^\s*(?:\(\s*\*\s*=|table\b)", re.I)
_TARGETS = {ir.TABLE, ir.FIGURE}
_TYPABLE = {ir.TEXT, ir.PARAGRAPH, ir.HEADING}


def _overlap(a: ir.BBox, b: ir.BBox) -> float:
    w = min(a.x1, b.x1) - max(a.x0, b.x0)
    narrower = min(a.x1 - a.x0, b.x1 - b.x0)
    return w / narrower if narrower > 0 and w > 0 else 0.0


def _between(a: ir.BBox, b: ir.BBox, c: ir.BBox) -> bool:
    """`c` lies vertically between `a` and `b` and overlaps them horizontally."""
    top, bottom = (a, b) if a.y1 <= b.y0 else (b, a)
    return c.y0 >= top.y1 - 1 and c.y1 <= bottom.y0 + 1 and \
        (_overlap(c, a) >= MIN_OVERLAP or _overlap(c, b) >= MIN_OVERLAP)


def _target(doc: ir.Document, indices: list[int], i: int, height: float,
            captions: set[int]) -> Optional[int]:
    cap = doc.elements[i]
    if cap.bbox is None:
        return None
    best, best_key = None, None
    names_table = bool(_NAMES_TABLE.match(recover.plain_text(cap)))
    for j in indices:
        t = doc.elements[j]
        if t.type not in _TARGETS or t.bbox is None or _overlap(cap.bbox, t.bbox) < MIN_OVERLAP:
            continue
        if cap.bbox.y1 <= t.bbox.y0 + 1:
            gap = t.bbox.y0 - cap.bbox.y1
        elif cap.bbox.y0 >= t.bbox.y1 - 1:
            gap = cap.bbox.y0 - t.bbox.y1
        else:
            continue  # overlapping vertically: beside it, not above or below
        if gap > MAX_GAP * height:
            continue
        blocked = any(k not in captions and k != j and k != i and doc.elements[k].bbox is not None
                      and doc.elements[k].text.strip() and doc.elements[k].type not in recover_skip()
                      and _between(cap.bbox, t.bbox, doc.elements[k].bbox) for k in indices)
        if blocked:
            continue
        named = (t.type == ir.TABLE) == names_table
        key = (max(gap, 0.0), 0 if named else 1, j)
        if best_key is None or key < best_key:
            best, best_key = j, key
    return best


def recover_skip() -> set:
    return {ir.PAGE_HEADER, ir.PAGE_FOOTER, ir.PAGE_NUMBER, ir.FIGURE}


def apply(doc: ir.Document) -> None:
    for page, indices in recover.page_groups(doc):
        height = recover.page_size(doc, page)[1]
        if not height:
            continue
        candidates = {i for i in indices if doc.elements[i].type in _TYPABLE
                      and doc.elements[i].type != ir.CAPTION
                      and _CAPTION_START.match(recover.plain_text(doc.elements[i]))}
        captions = {i for i in indices if doc.elements[i].type == ir.CAPTION} | candidates
        for i in sorted(captions):
            j = _target(doc, indices, i, height, captions)
            if j is None:
                continue
            el = doc.elements[i]
            if el.type != ir.CAPTION:
                doc.elements[i] = replace(el, type=ir.CAPTION, role_signal="caption-pattern")
            doc.relations.append(ir.Relation("caption_of", el.id, doc.elements[j].id, SIGNAL))
