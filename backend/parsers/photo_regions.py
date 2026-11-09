"""Find photographs on a scanned page, so their OCR noise can be dropped (ADR-037).

A scanned page is one image, so the PDF cannot say where its photographs are,
and Tesseract happily "reads" a photo of a carnation as `ie ST hee se ng`. Print
and photographs look different, though. Print is near-black ink on near-white
paper; a photograph is mostly mid-tones. So:

1. Render the page small and grey, and split it into cells about 2 mm square.
2. A cell is *tone* when most of its pixels are mid-grey: neither paper nor ink.
3. Tone cells that form a large connected region are a *photo region*. Headings
   in coloured ink and thin coloured rules make only small regions, and are ignored.

`is_photo_noise` then drops an OCR line only when it lies mostly inside a photo
region *and* does not read as a run of real words. Text printed on a coloured band
(a banner) is real words, so it survives. A photo on a near-black background is
not mid-tone and is not detected; that case is left for later.

Pure arithmetic on pixels, with no model: the same page always gives the same
regions, which the default path's determinism rule requires.
"""

from __future__ import annotations

import re

import pymupdf

from .reading_order import Line

# Render resolution and cell size: 36 dpi with 3-pixel cells is about 2 mm square.
RENDER_DPI = 36
CELL_PX = 3
# A pixel is mid-tone when its grey level is strictly between these (0 = black).
TONE_LOW = 50
TONE_HIGH = 200
# A cell is tone when at least this share of its pixels is mid-tone.
TONE_CELL_SHARE = 0.6
# A connected tone region counts as a photo at this many cells (about 6 cm²).
MIN_PHOTO_CELLS = 150
# A line is inside a photo when at least this share of its cells are photo cells.
LINE_IN_PHOTO = 0.5
# ...and it is kept anyway when it has this many word-like tokens, making up at
# least this share of its characters.
KEEP_MIN_WORDS = 3
KEEP_WORD_SHARE = 0.8

SHORT_WORDS = frozenset({"a", "i", "an", "of", "to", "in", "on", "by", "is", "at", "or", "as",
                          "be", "it", "we", "no", "mm", "cm"})


class PhotoMask:
    """The photo cells of one page, and the scale from PDF points to cells."""

    def __init__(self, cells: frozenset[tuple[int, int]], scale: float) -> None:
        self.cells = cells
        self.scale = scale

    def coverage(self, line: Line) -> float:
        """Share of the cells under a line's box that are photo cells."""
        if not self.cells:
            return 0.0
        s = self.scale
        x0, y0, x1, y1 = int(line.x0 * s), int(line.y0 * s), int(line.x1 * s), int(line.y1 * s)
        total = (x1 - x0 + 1) * (y1 - y0 + 1)
        hits = sum((y, x) in self.cells for y in range(y0, y1 + 1) for x in range(x0, x1 + 1))
        return hits / total


def photo_mask(page: pymupdf.Page) -> PhotoMask:
    """Locate the photo regions on a page image."""
    pix = page.get_pixmap(dpi=RENDER_DPI, colorspace=pymupdf.csGRAY)
    width, samples = pix.width, pix.samples
    cols, rows = pix.width // CELL_PX, pix.height // CELL_PX
    need = TONE_CELL_SHARE * CELL_PX * CELL_PX
    tone = [[False] * cols for _ in range(rows)]
    for cy in range(rows):
        for cx in range(cols):
            hits = 0
            for y in range(cy * CELL_PX, cy * CELL_PX + CELL_PX):
                base = y * width
                for x in range(cx * CELL_PX, cx * CELL_PX + CELL_PX):
                    if TONE_LOW < samples[base + x] < TONE_HIGH:
                        hits += 1
            tone[cy][cx] = hits >= need

    photo: set[tuple[int, int]] = set()
    seen = [[False] * cols for _ in range(rows)]
    for y in range(rows):          # row-major scan: a fixed visiting order
        for x in range(cols):
            if not tone[y][x] or seen[y][x]:
                continue
            region, stack = [], [(y, x)]
            seen[y][x] = True
            while stack:
                a, b = stack.pop()
                region.append((a, b))
                for c, d in ((a + 1, b), (a - 1, b), (a, b + 1), (a, b - 1)):
                    if 0 <= c < rows and 0 <= d < cols and tone[c][d] and not seen[c][d]:
                        seen[c][d] = True
                        stack.append((c, d))
            if len(region) >= MIN_PHOTO_CELLS:
                photo.update(region)
    return PhotoMask(frozenset(photo), RENDER_DPI / 72 / CELL_PX)


def _word_like(token: str) -> bool:
    if token.isdigit():
        return True
    if token.lower() in SHORT_WORDS:
        return True
    return len(token) >= 3 and token.isalpha() and re.search(r"[aeiouyAEIOUY]", token) is not None


def reads_as_words(text: str) -> bool:
    """True when a line is a run of real-looking words, not OCR noise."""
    tokens = re.findall(r"[^\W_]+", text)
    words = [t for t in tokens if _word_like(t)]
    chars = sum(len(t) for t in tokens)
    return (len(words) >= KEEP_MIN_WORDS and chars > 0
            and sum(len(t) for t in words) / chars >= KEEP_WORD_SHARE)


def is_photo_noise(line: Line, mask: PhotoMask) -> bool:
    """True when an OCR line is text 'read' out of a photograph."""
    return mask.coverage(line) >= LINE_IN_PHOTO and not reads_as_words(line.text)
