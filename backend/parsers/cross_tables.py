"""Tables continued as a side-by-side panel or on the next page (roadmap 3.6, ADR-054).

A long table is printed in pieces: a second panel beside the first when the page
is wide enough (PBR comparison tables, V5N1 p18), or a continuation on the next
page, often under a "Table ... —Continued" caption and with its header row
printed again. The engines see separate tables. This pass links each piece to
the one it continues with a `continues_table` relation (later → earlier):

* **Panel** (`signal="table-panel"`): two tables on one page, the later wholly
  to the right of the earlier, overlapping it vertically by at least half the
  shorter one's height, that *match*.
* **Next page** (`signal="table-page"`): a table that is the last block of its
  page (furniture, figures and captions aside) and a table that is the first
  block of the next page (the same aside), that match; or, whatever their
  columns, a caption saying "continued" right above the later one.

Two pieces **match** when the later one's header row names the same columns as
the earlier one's: at least two non-empty cells, their words alike
(`HEADER_SIMILARITY`, since OCR reads a reprinted header a little differently
each time), against the earlier piece's first or second row (a note such as
"(* = comparators)" can print above the real header). Or, when the later piece
has the same number of columns and starts with data rather than a header row
(no cell of its first row is a header): the rows simply carry on. Two tables
side by side that compare different varieties have different headers, and a
note row alone is not a header, so neither links.

Only tables with rows (the Table Transformer's cells, ADR-046, or ADR-038's
stitched rows) take part; a column count needs rows to count.

Nothing is merged in the IR: each piece keeps its rows. The serializer writes
each piece where it stands; in reader mode (`WISEAU_FURNITURE=drop`) a
continuation whose first row repeats the header of the table it continues is
written without that row. The scorers read a chain of `continues_table` pieces as
one logical table.
"""

from __future__ import annotations

import re
from typing import Optional

from . import document as ir
from . import recover

PANEL = "table-panel"
PAGE = "table-page"

_ASIDE = {ir.PAGE_HEADER, ir.PAGE_FOOTER, ir.PAGE_NUMBER, ir.FIGURE, ir.CAPTION}
_CONTINUED = re.compile(r"\bcontinued\b|\bcont(?:'|’)?d\b", re.I)


def _norm(text: str) -> str:
    return " ".join(re.findall(r"[^\W_]+", text.lower()))


HEADER_SIMILARITY = 0.8


def _key(row) -> tuple[str, ...]:
    """A row's non-empty cells, words only."""
    return tuple(t for t in (_norm(c.text) for c in row) if t)


def _width(el: ir.Element) -> int:
    return max((len(r) for r in el.rows), default=0)


def _same_header(a_row, b_row) -> bool:
    from difflib import SequenceMatcher
    ka, kb = _key(a_row), _key(b_row)
    if len(ka) < 2 or len(kb) < 2:
        return False
    return SequenceMatcher(None, " | ".join(ka), " | ".join(kb)).ratio() >= HEADER_SIMILARITY


def _alike(a: ir.Element, b: ir.Element) -> bool:
    if not a.rows or not b.rows:
        return False
    if any(_same_header(row, b.rows[0]) for row in a.rows[:2]):
        return True
    starts_with_data = not any(c.header for c in b.rows[0])
    return starts_with_data and _width(a) >= 2 and _width(a) == _width(b)


def _is_table(el: ir.Element) -> bool:
    return el.type == ir.TABLE and bool(el.rows)


def _panel(a: ir.Element, b: ir.Element) -> bool:
    if a.bbox is None or b.bbox is None:
        return False
    if b.bbox.x0 < a.bbox.x1 - 2:
        return False
    overlap = min(a.bbox.y1, b.bbox.y1) - max(a.bbox.y0, b.bbox.y0)
    shorter = min(a.bbox.y1 - a.bbox.y0, b.bbox.y1 - b.bbox.y0)
    return shorter > 0 and overlap >= 0.5 * shorter


def _edge(doc: ir.Document, indices: list[int], last: bool) -> Optional[int]:
    """The page's last (or first) element that is not furniture, a figure or a caption."""
    for i in (reversed(indices) if last else indices):
        el = doc.elements[i]
        if el.type in _ASIDE or not (el.text.strip() or el.rows):
            continue
        return i
    return None


def _continued_caption(doc: ir.Document, indices: list[int], table: int) -> bool:
    for i in indices:
        if i >= table:
            break
        el = doc.elements[i]
        if el.type == ir.CAPTION and _CONTINUED.search(recover.plain_text(el)):
            return True
    return False


def apply(doc: ir.Document) -> None:
    groups = recover.page_groups(doc)
    for page, indices in groups:
        tables = [i for i in indices if _is_table(doc.elements[i])]
        for k, i in enumerate(tables):
            for j in tables[k + 1:]:
                a, b = doc.elements[i], doc.elements[j]
                if _panel(a, b) and _alike(a, b):
                    doc.relations.append(ir.Relation("continues_table", b.id, a.id, PANEL))
                    break
    for (page, indices), (nxt, next_indices) in zip(groups, groups[1:]):
        if not page or nxt != page + 1:
            continue
        i, j = _edge(doc, indices, last=True), _edge(doc, next_indices, last=False)
        if i is None or j is None:
            continue
        a, b = doc.elements[i], doc.elements[j]
        if not (_is_table(a) and _is_table(b)):
            continue
        if _alike(a, b) or _continued_caption(doc, next_indices, j):
            doc.relations.append(ir.Relation("continues_table", b.id, a.id, PAGE))
