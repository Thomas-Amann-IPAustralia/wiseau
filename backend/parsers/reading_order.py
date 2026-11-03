"""Column-aware reading order for OCR'd pages (ADR-035).

MuPDF's OCR primitive (`Page.get_textpage_ocr`, see `ocr.py`) recognises each
printed line correctly, but it groups the lines into blocks that run straight
across a multi-column page. Read in that order, a two-column scan interleaves
its columns line by line (left, right, left, right, ...). The words are right
but the order is wrong.

This module puts the lines back in reading order from their positions alone:

1. **Find the column gutters:** vertical strips of the page that (almost) no
   line crosses.
2. **Keep only gutters that separate text columns:** every column at least a
   quarter of the text width, whose rows are continuous runs of text. A table's
   rows are mostly empty space between cells, so they fail this test and tables
   keep today's row-wise reading.
3. **Cut the page into bands** at full-width lines (a heading that crosses the
   gutter), and read each band column by column, top to bottom.

When no gutter qualifies, `order_lines` returns `None` and the caller keeps
MuPDF's own text unchanged, so single-column pages and tables are untouched.

Pure geometry, no model, with stable tie-breakers: identical lines in give
identical text out, as the default path's determinism rule requires.
"""

from __future__ import annotations

from itertools import combinations
from statistics import median
from typing import NamedTuple, Optional, Sequence


class Line(NamedTuple):
    """One recognised line: its bounding box (PDF points) and its text."""

    x0: float
    y0: float
    x1: float
    y1: float
    text: str


# A gutter must be at least this wide (points; 8 pt is about 3 mm).
MIN_GUTTER = 8.0
# A strip still counts as a gutter if at most this share of lines crosses it
# (full-width headings, footers, OCR specks).
MAX_GUTTER_COVERAGE = 0.05
# Every column must be at least this share of the text width.
MIN_COLUMN_SHARE = 0.25
# In a text column, the typical row is a continuous run of text: the recognised
# fragments cover at least this share of the row's extent (median over rows).
# Table rows are mostly gaps between cells. On the bake-off scans, text columns
# measure 0.8-1.0 and every table-cutting split has a column at 0.6 or below.
MIN_ROW_DENSITY = 0.7
# A column needs at least this many lines to count.
MIN_COLUMN_LINES = 3
# At most this many columns.
MAX_COLUMNS = 3
# A vertical gap above this multiple of the median line height starts a new
# paragraph.
PARAGRAPH_GAP = 0.8
# Below this many lines a page is too small to judge; keep MuPDF's order.
MIN_LINES = 6


def _gutters(lines: Sequence[Line], left: int, right: int) -> list[tuple[int, int]]:
    """Runs of x (start, end) that at most a few lines cross, inside the text."""
    coverage = [0] * (right - left + 1)
    for ln in lines:
        for x in range(max(int(ln.x0), left), min(int(ln.x1), right) + 1):
            coverage[x - left] += 1
    limit = max(1, int(MAX_GUTTER_COVERAGE * len(lines)))
    runs: list[tuple[int, int]] = []
    start: Optional[int] = None
    for i, c in enumerate(coverage + [limit + 1]):  # sentinel closes a final run
        if c <= limit and start is None:
            start = i
        elif c > limit and start is not None:
            if start > 0 and i < len(coverage) and i - start >= MIN_GUTTER:
                runs.append((left + start, left + i - 1))  # interior runs only
            start = None
    return runs


def _column_of(ln: Line, gutters: Sequence[tuple[int, int]]) -> Optional[int]:
    """The column a line sits in, or None if it spans a gutter."""
    for i, (g0, g1) in enumerate(gutters):
        if ln.x0 < g0 and ln.x1 > g1:
            return None
    centre = (ln.x0 + ln.x1) / 2
    return sum(1 for g0, g1 in gutters if centre > (g0 + g1) / 2)


def _valid(lines: Sequence[Line], gutters: Sequence[tuple[int, int]], left: int, right: int) -> bool:
    edges = [left] + [e for g in gutters for e in g] + [right]
    width = right - left
    for i in range(len(gutters) + 1):
        c0, c1 = edges[2 * i], edges[2 * i + 1]
        col_width = c1 - c0
        if col_width < MIN_COLUMN_SHARE * width:
            return False
        members = [ln for ln in lines if _column_of(ln, gutters) == i]
        if len(members) < MIN_COLUMN_LINES:
            return False
        if median(_density(row) for row in _rows(members)) < MIN_ROW_DENSITY:
            return False
    return True


def _choose_gutters(lines: Sequence[Line]) -> list[tuple[int, int]]:
    """The column split to use: most columns, then widest gutters, then leftmost."""
    left = int(min(ln.x0 for ln in lines))
    right = int(max(ln.x1 for ln in lines))
    candidates = _gutters(lines, left, right)
    for k in range(min(MAX_COLUMNS - 1, len(candidates)), 0, -1):
        valid = [c for c in combinations(candidates, k) if _valid(lines, c, left, right)]
        if valid:
            return list(max(valid, key=lambda c: (sum(g1 - g0 for g0, g1 in c), [-g0 for g0, _ in c])))
    return []


def _rows(lines: Sequence[Line]) -> list[list[Line]]:
    """Group lines that share a baseline into rows, top to bottom, left to right."""
    rows: list[list[Line]] = []
    for ln in sorted(lines, key=lambda l: (l.y0, l.x0, l.x1, l.text)):
        if rows:
            top = min(l.y0 for l in rows[-1])
            bottom = max(l.y1 for l in rows[-1])
            overlap = min(bottom, ln.y1) - max(top, ln.y0)
            if overlap > 0.5 * min(bottom - top, ln.y1 - ln.y0):
                rows[-1].append(ln)
                continue
        rows.append([ln])
    return [sorted(r, key=lambda l: (l.x0, l.y0, l.text)) for r in rows]


def _density(row: Sequence[Line]) -> float:
    """Share of a row's extent covered by its recognised fragments."""
    extent = max(l.x1 for l in row) - min(l.x0 for l in row)
    return min(1.0, sum(l.x1 - l.x0 for l in row) / extent)


def _render(lines: Sequence[Line], line_height: float) -> str:
    out: list[str] = []
    prev_bottom: Optional[float] = None
    for row in _rows(lines):
        top = min(l.y0 for l in row)
        if prev_bottom is not None and top - prev_bottom > PARAGRAPH_GAP * line_height:
            out.append("")
        out.append(" ".join(l.text for l in row))
        prev_bottom = max(l.y1 for l in row)
    return "\n".join(out)


def order_lines(lines: Sequence[Line]) -> Optional[str]:
    """Reading-order text for a multi-column page, or None to keep MuPDF's order.

    Returns None when the page has too few lines or no gutter separates real
    text columns: single-column pages and tables are left exactly as they are.
    """
    lines = [ln for ln in lines if ln.text.strip() and ln.x1 > ln.x0 and ln.y1 > ln.y0]
    if len(lines) < MIN_LINES:
        return None
    gutters = _choose_gutters(lines)
    if not gutters:
        return None
    line_height = median(ln.y1 - ln.y0 for ln in lines)

    # Full-width lines cut the page into bands; each band is read column by column.
    spanning = sorted((ln for ln in lines if _column_of(ln, gutters) is None),
                      key=lambda l: (l.y0, l.x0, l.text))
    columned = [ln for ln in lines if _column_of(ln, gutters) is not None]
    parts: list[str] = []
    lower = float("-inf")
    for cut in spanning + [None]:
        upper = float("inf") if cut is None else (cut.y0 + cut.y1) / 2
        band = [ln for ln in columned if lower < (ln.y0 + ln.y1) / 2 <= upper]
        for col in range(len(gutters) + 1):
            members = [ln for ln in band if _column_of(ln, gutters) == col]
            if members:
                parts.append(_render(members, line_height))
        if cut is not None:
            parts.append(cut.text)
        lower = upper
    return "\n\n".join(p for p in parts if p)
