"""Paint out scanner bands at a page's edges before OCR (roadmap 14 item 1.2).

A scanner often leaves a solid black band where the lid or the page edge was: a
strip across the top or bottom, or down one side. Tesseract reads the speckle in
and around it as junk words (`t snes ee nsn bago ...`), and those words land in the
Markdown. A band looks like nothing else on a printed page:

1. Render the page small and grey, and split it into cells about 2 mm square.
2. A cell is *solid* when most of its pixels are near-black. Printed text and thin
   rules never fill a 2 mm cell; a band, a black banner or a dark photo does.
3. Solid cells that connect into a region are a candidate. It is a **band** only
   when all of these hold:
   - it is *long*: it spans at least half the page's width or height;
   - *every* cell lies in the outer margin of the page, so banners, black bars and
     dark photos inside the page are left alone;
   - it *runs off the page*: its cells reach a page edge, at an end of the band
     (left or right for a band across the page, top or bottom for one down a side).
     A printed header bar has white margin at both ends; a scanner band does not;
   - it is *deep*: some cell lies at least 4% of the page in from the nearest edge.
     Hairline strips along the very edge of a scan produce no junk words, and
     removing them only nudges Tesseract's page segmentation, which moved text on
     PBR pages both ways (a heading and a caption were lost) for no gain.

Each band is then covered with white rectangles drawn over a copy of the page,
following its outline column by column (or row by row for a band down a side), so
a wavy band is removed without whitening a straight strip that could clip nearby
text. Holes in the band (white dust on black, which is what reads as letters) are
filled too.

Pure arithmetic on pixels, with no model: the same page always gives the same
result, which the default path's determinism rule requires.
"""

from __future__ import annotations

from dataclasses import dataclass

import pymupdf

# Render resolution and cell size: 36 dpi with 3-pixel cells is about 2 mm square.
RENDER_DPI = 36
CELL_PX = 3
# A pixel is dark below this grey level (0 = black); a cell is solid when at least
# this share of its pixels is dark.
DARK_LEVEL = 80
SOLID_CELL_SHARE = 0.7
# A band spans at least this share of the page's width (or height)...
MIN_BAND_SPAN = 0.5
# ...and lies entirely within this share of the page from one of its edges.
EDGE_MARGIN = 0.18
# ...and reaches a page edge at one of its ends: its first or last cell along its
# length is within this share of the page from that edge...
END_AT_EDGE = 0.03
# ...and some cell lies at least this share of the page in from the nearest edge.
MIN_BAND_DEPTH = 0.04
# Cells of white painted beyond the band's outline on every side.
PAD_CELLS = 1
# Within one cell column (or row) of a band, a gap of at most this many cells
# between solid cells is a hole in the band (white dust on black) and is filled.
# A longer gap is page between two parts of the band, e.g. a frame's top and
# bottom edges, and is left alone.
MAX_HOLE_CELLS = 3


@dataclass(frozen=True)
class Band:
    """A scanner band: its cells, on a grid of ``cols`` x ``rows`` cells."""

    cells: frozenset[tuple[int, int]]
    cols: int
    rows: int

    @property
    def horizontal(self) -> bool:
        xs = [x for x, _ in self.cells]
        ys = [y for _, y in self.cells]
        return (max(xs) - min(xs) + 1) / self.cols >= (max(ys) - min(ys) + 1) / self.rows


def find_bands(page: pymupdf.Page) -> list[Band]:
    """The scanner bands on ``page``, in a fixed order (top-left cell first)."""
    pix = page.get_pixmap(dpi=RENDER_DPI, colorspace=pymupdf.csGRAY)
    w, samples = pix.width, pix.samples
    cols, rows = pix.width // CELL_PX, pix.height // CELL_PX
    if cols == 0 or rows == 0:
        return []
    need = SOLID_CELL_SHARE * CELL_PX * CELL_PX
    solid = set()
    for cy in range(rows):
        for cx in range(cols):
            dark = 0
            for y in range(cy * CELL_PX, cy * CELL_PX + CELL_PX):
                row = y * w
                for x in range(cx * CELL_PX, cx * CELL_PX + CELL_PX):
                    if samples[row + x] < DARK_LEVEL:
                        dark += 1
            if dark >= need:
                solid.add((cx, cy))
    bands = []
    seen: set[tuple[int, int]] = set()
    for start in sorted(solid, key=lambda c: (c[1], c[0])):
        if start in seen:
            continue
        seen.add(start)
        region, stack = [], [start]
        while stack:
            x, y = stack.pop()
            region.append((x, y))
            for nb in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
                if nb in solid and nb not in seen:
                    seen.add(nb)
                    stack.append(nb)
        if _is_band(region, cols, rows):
            bands.append(Band(frozenset(region), cols, rows))
    return bands


def _is_band(region: list[tuple[int, int]], cols: int, rows: int) -> bool:
    xs = [x for x, _ in region]
    ys = [y for _, y in region]
    long = ((max(xs) - min(xs) + 1) / cols >= MIN_BAND_SPAN
            or (max(ys) - min(ys) + 1) / rows >= MIN_BAND_SPAN)
    if not long:
        return False
    mx, my = EDGE_MARGIN * cols, EDGE_MARGIN * rows

    def in_margin(x: int, y: int) -> bool:
        return x < mx or x + 1 > cols - mx or y < my or y + 1 > rows - my

    if not all(in_margin(x, y) for x, y in region):
        return False
    horizontal = (max(xs) - min(xs) + 1) / cols >= (max(ys) - min(ys) + 1) / rows
    if horizontal:
        runs_off = min(xs) < END_AT_EDGE * cols or max(xs) + 1 > cols - END_AT_EDGE * cols
    else:
        runs_off = min(ys) < END_AT_EDGE * rows or max(ys) + 1 > rows - END_AT_EDGE * rows
    if not runs_off:
        return False
    depth = max(min((x + 1) / cols, (cols - x) / cols, (y + 1) / rows, (rows - y) / rows) for x, y in region)
    return depth >= MIN_BAND_DEPTH


def band_rects(band: Band, page_rect: pymupdf.Rect) -> list[pymupdf.Rect]:
    """Page-space rectangles covering ``band``: one per cell column (or row), padded."""
    cw, ch = page_rect.width / band.cols, page_rect.height / band.rows
    spans: dict[int, list[int]] = {}
    for x, y in band.cells:
        key, val = (x, y) if band.horizontal else (y, x)
        spans.setdefault(key, []).append(val)
    rects = []
    for key in sorted(spans):
        a0, a1 = key - PAD_CELLS, key + 1 + PAD_CELLS
        for first, last in _runs(sorted(spans[key])):
            lo, hi = first - PAD_CELLS, last + 1 + PAD_CELLS
            if band.horizontal:
                x0, x1, y0, y1 = a0 * cw, a1 * cw, lo * ch, hi * ch
            else:
                x0, x1, y0, y1 = lo * cw, hi * cw, a0 * ch, a1 * ch
            rect = pymupdf.Rect(page_rect.x0 + x0, page_rect.y0 + y0, page_rect.x0 + x1, page_rect.y0 + y1)
            rects.append(rect & page_rect)
    return rects


def _runs(values: list[int]) -> list[tuple[int, int]]:
    """Sorted cell indices grouped into runs, bridging gaps of up to MAX_HOLE_CELLS."""
    runs = [[values[0], values[0]]]
    for v in values[1:]:
        if v - runs[-1][1] - 1 <= MAX_HOLE_CELLS:
            runs[-1][1] = v
        else:
            runs.append([v, v])
    return [(a, b) for a, b in runs]


def cleaned_page(page: pymupdf.Page) -> tuple[pymupdf.Document, pymupdf.Page] | None:
    """A copy of ``page`` with white rectangles drawn over its scanner bands.

    The copy is the original page plus vector rectangles, never a re-rasterised
    image, so at any resolution every pixel outside the bands renders exactly as
    the original's does. (Rasterising and re-inserting the image resampled the
    whole page and moved OCR results on pages whose only change was a 4 mm edge
    strip.)

    Returns ``(document, page)`` (keep the document alive while using the page), or
    ``None`` when the page has no band, so callers use the original untouched.
    """
    bands = find_bands(page)
    if not bands:
        return None
    doc = pymupdf.open()
    doc.insert_pdf(page.parent, from_page=page.number, to_page=page.number)
    out = doc[0]
    # band_rects are in the page's displayed (rotated) space; drawing is unrotated.
    to_draw = out.derotation_matrix
    for band in bands:
        for r in band_rects(band, page.rect):
            out.draw_rect(r * to_draw, color=None, fill=(1, 1, 1), overlay=True, width=0)
    return doc, out
