"""Tesseract's own page layout for scanned pages, with table rows stitched back (ADR-038).

ADR-035 orders MuPDF's OCR lines into columns by geometry alone. That fails on
mixed layouts: a table inside one column of a two-column page, or a table above
two-column prose. Tesseract's own page segmentation (``--psm 3``) reads those
pages well, because it finds text blocks first and orders the blocks. On the 26
hand-checked scans it beat the ADR-035 path on 18 pages.

It loses on pages that must be read across each row: comparison tables and
name/number lists. There it cuts every table column into its own block, so the
page comes out one column at a time. This module repairs that one failure:

1. **Run Tesseract once** on the rendered page (psm 3, TSV output) and rebuild
   its blocks and lines with their boxes.
2. **Stitch table cells back into rows.** Two blocks are cells of one table when
   both look like cells (at most ``MAX_CELL_WORDS`` words per line on median, and
   at most ``MAX_CELL_WIDTH`` of the text width), they overlap vertically, sit
   close side by side, and most lines of the smaller one have a line of similar
   height at the same height in the other. Prose columns have long lines, so they
   never qualify, and keep Tesseract's column order.
3. **Render each stitched group row by row**, at the position of its first block.

The caller (``ocr.TesseractEngine``) keeps the ADR-035/037 text as a guard: if
this layout reads markedly fewer words than that path did (``KEEP_WORD_SHARE``),
Tesseract's layout analysis threw text away (it can mistake a column of dates
for an image), and the guard text is used instead.

Determinism: Tesseract's output is fixed for fixed pixels, and everything here is
geometry with total sort orders, so identical pages give identical text (ADR-013:
the default path stays deterministic).
"""

from __future__ import annotations

import csv
import io
import re
import subprocess
from statistics import median
from typing import NamedTuple, Optional, Sequence

import pymupdf

from . import photo_regions
from .reading_order import Line

# The Tesseract command-line binary (the same install MuPDF's OCR uses).
TESSERACT_CMD = "tesseract"
# Seconds before a Tesseract run is abandoned and the guard text is used.
TIMEOUT_S = 120

# A block looks like a table cell when its lines have at most this many words on
# median...
MAX_CELL_WORDS = 4
# ...and it is at most this share of the page's text width.
MAX_CELL_WIDTH = 0.40
# Two cells are neighbours when the horizontal gap between them is at most this
# share of the text width.
MAX_CELL_GAP = 0.20
# ...and at least this share of the smaller block's lines line up with a line of
# the other block (centres within half a line height, heights within this ratio).
MIN_ROW_ALIGN = 0.6
MAX_HEIGHT_RATIO = 1.6
# A line spanning at least this share of the image is scanner-edge noise, and is
# left out when measuring the text width.
EDGE_LINE_SHARE = 0.9

# The guard: this layout must read at least this share of the words the ADR-035
# path read, or that path's text is used.
KEEP_WORD_SHARE = 0.85

_WORD = re.compile(r"[^\W_]+")


class Block(NamedTuple):
    """One Tesseract text block: its lines, in Tesseract's order, and its box."""

    lines: tuple[Line, ...]
    x0: float
    y0: float
    x1: float
    y1: float


def word_count(text: str) -> int:
    """Runs of letters and digits: the unit the guard compares."""
    return len(_WORD.findall(text))


def parse_tsv(tsv: str, scale: float = 1.0) -> list[Block]:
    """Tesseract TSV → blocks of lines, boxes multiplied by ``scale``.

    Words are grouped by (block, paragraph, line) number, in Tesseract's order.
    Paragraphs are only a level inside a block, so a block's lines run on.
    """
    reader = csv.DictReader(io.StringIO(tsv), delimiter="\t", quoting=csv.QUOTE_NONE)
    lines: dict[tuple[int, int, int], list] = {}
    for row in reader:
        if row.get("level") != "5" or not (row.get("text") or "").strip():
            continue
        key = (int(row["block_num"]), int(row["par_num"]), int(row["line_num"]))
        x0, y0 = int(row["left"]), int(row["top"])
        x1, y1 = x0 + int(row["width"]), y0 + int(row["height"])
        if key not in lines:
            lines[key] = [x0, y0, x1, y1, []]
        box = lines[key]
        box[0], box[1] = min(box[0], x0), min(box[1], y0)
        box[2], box[3] = max(box[2], x1), max(box[3], y1)
        box[4].append(row["text"].strip())
    blocks: dict[int, list[Line]] = {}
    for (block, _, _), (x0, y0, x1, y1, words) in lines.items():
        blocks.setdefault(block, []).append(
            Line(x0 * scale, y0 * scale, x1 * scale, y1 * scale, " ".join(words)))
    return [_block(ls) for ls in blocks.values()]


def _block(lines: Sequence[Line]) -> Block:
    return Block(tuple(lines), min(ln.x0 for ln in lines), min(ln.y0 for ln in lines),
                 max(ln.x1 for ln in lines), max(ln.y1 for ln in lines))


def _text_width(blocks: Sequence[Block]) -> float:
    lines = [ln for b in blocks for ln in b.lines]
    page_right = max(ln.x1 for ln in lines)
    body = [ln for ln in lines if ln.x1 - ln.x0 < EDGE_LINE_SHARE * page_right] or lines
    return max(1.0, max(ln.x1 for ln in body) - min(ln.x0 for ln in body))


def _is_cell(block: Block, width: float) -> bool:
    words = median(len(ln.text.split()) for ln in block.lines)
    return words <= MAX_CELL_WORDS and (block.x1 - block.x0) / width <= MAX_CELL_WIDTH


def _same_row(a: Line, b: Line) -> bool:
    ha, hb = a.y1 - a.y0, b.y1 - b.y0
    if max(ha, hb) > MAX_HEIGHT_RATIO * max(1e-6, min(ha, hb)):
        return False
    return abs((a.y0 + a.y1) / 2 - (b.y0 + b.y1) / 2) <= 0.5 * min(ha, hb)


def _row_aligned(a: Block, b: Block) -> bool:
    small, big = (a, b) if len(a.lines) <= len(b.lines) else (b, a)
    hits = sum(1 for ln in small.lines if any(_same_row(ln, m) for m in big.lines))
    return hits / len(small.lines) >= MIN_ROW_ALIGN


def _neighbouring_cells(a: Block, b: Block, width: float) -> bool:
    if min(a.y1, b.y1) <= max(a.y0, b.y0):  # no vertical overlap
        return False
    gap = max(a.x0, b.x0) - min(a.x1, b.x1)
    if gap < 0 or gap / width > MAX_CELL_GAP:  # overlapping, or too far apart
        return False
    return _row_aligned(a, b)


def _groups(blocks: Sequence[Block]) -> list[list[int]]:
    """Connected groups of neighbouring cells (indices, each group in block order)."""
    width = _text_width(blocks)
    parent = list(range(len(blocks)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    cells = [i for i, b in enumerate(blocks) if _is_cell(b, width)]
    for n, i in enumerate(cells):
        for j in cells[n + 1:]:
            if _neighbouring_cells(blocks[i], blocks[j], width):
                parent[find(i)] = find(j)
    groups: dict[int, list[int]] = {}
    for i in range(len(blocks)):
        groups.setdefault(find(i), []).append(i)
    return sorted(groups.values(), key=lambda g: g[0])


def _rows(lines: Sequence[Line]) -> list[str]:
    """Lines of a stitched table, read row by row (left to right within a row)."""
    line_height = median(ln.y1 - ln.y0 for ln in lines)
    rows: list[tuple[float, list[Line]]] = []
    for ln in sorted(lines, key=lambda l: ((l.y0 + l.y1) / 2, l.x0, l.text)):
        centre = (ln.y0 + ln.y1) / 2
        if rows and centre - rows[-1][0] <= 0.5 * line_height:
            rows[-1][1].append(ln)
        else:
            rows.append((centre, [ln]))
    return [" ".join(ln.text for ln in sorted(row, key=lambda l: (l.x0, l.text))) for _, row in rows]


def render(blocks: Sequence[Block]) -> str:
    """Blocks in Tesseract's order, stitched table groups read row by row."""
    if not blocks:
        return ""
    out = []
    for group in _groups(blocks):
        if len(group) == 1:
            out.append("\n".join(ln.text for ln in blocks[group[0]].lines))
        else:
            out.append("\n".join(_rows([ln for i in group for ln in blocks[i].lines])))
    return "\n\n".join(out)


def read_page(page: pymupdf.Page, *, dpi: int, language: str,
              mask: Optional[photo_regions.PhotoMask] = None) -> Optional[str]:
    """OCR ``page`` with Tesseract's own layout analysis; ``None`` if Tesseract fails.

    ``mask`` (ADR-037) drops lines read out of photographs before stitching.
    """
    png = page.get_pixmap(dpi=dpi).tobytes("png")
    try:
        done = subprocess.run([TESSERACT_CMD, "stdin", "stdout", "--psm", "3", "-l", language, "tsv"],
                              input=png, capture_output=True, timeout=TIMEOUT_S, check=True)
    except (OSError, subprocess.SubprocessError):
        return None
    blocks = parse_tsv(done.stdout.decode("utf-8", errors="replace"), scale=72.0 / dpi)
    if mask is not None and mask.cells:
        kept = [[ln for ln in b.lines if not photo_regions.is_photo_noise(ln, mask)] for b in blocks]
        blocks = [_block(ls) for ls in kept if ls]
    return render(blocks)


def keeps_words(text: str, guard_text: str) -> bool:
    """True unless ``text`` lost a large share of the words ``guard_text`` has."""
    return word_count(text) >= KEEP_WORD_SHARE * word_count(guard_text)
