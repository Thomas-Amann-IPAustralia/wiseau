"""Tables on scanned pages, found and read cell by cell by Table Transformer (ADR-046).

Tesseract reads a scanned table's words well but not its *structure*: ADR-038's
row stitching keeps a table's rows together, but its "cells" are Tesseract's line
fragments, not columns, and two tables printed side by side come out interleaved.
The structural gold set (set S) puts the default path's table score at TEDS 0.26.

This module is a pass over the IR of one OCR'd page:

1. **Find tables.** Table Transformer's detection model (PubTables-1M) proposes
   table regions on the page image.
2. **Read their structure.** Its structure model (v1.1-all) proposes the
   region's rows, columns, column-header rows, projected row headers (a label
   printed across the table, "AWN LENGTH") and spanning cells.
3. **Fill the cells with the words Tesseract already read.** Every psm 3 word
   whose centre lies in the region moves into the table, into the row and column
   it overlaps most. Word by word, not line by line: psm 3 reads a line straight
   across two tables printed side by side, so such a line is split, its words in
   the table going to cells and the rest staying as a shorter line. No word is
   re-read, added or lost: the page's words are partitioned exactly.
4. **Check the region really is a table** (`is_tabular`). The detector also boxes
   two-column prose, and reading prose cell by cell interleaves its columns (the
   Tier 1 bake-off: wer +0.10). A real table has short cells; a prose column
   boxed as a table has long ones. A region that fails keeps its psm 3 reading:
   the pass refuses rather than guesses (ADR-030).
5. **Replace** the region's lines in the page's elements with one `table` element
   whose `rows` are real cells (`signal="tatr"`, `score` = the detector's own
   probability, uncalibrated), placed where the first of those lines was.

It is **opt-in** (`WISEAU_TABLES=tatr`) and needs PyTorch, `transformers` and
`timm` (`requirements-tables.txt`). Without them, or with the variable unset, the
pass does nothing and the default path is unchanged. Model weights are pinned to
exact revisions, inference runs on a fixed number of CPU threads, and every
sort has a total order, so a page converts the same way every time.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from dataclasses import replace
from statistics import median
from typing import NamedTuple, Optional, Sequence

import pymupdf

from . import document as ir
from .reading_order import Line
from .tesseract_layout import Word

logger = logging.getLogger(__name__)

# Pinned model revisions (Hugging Face commit ids): the same weights every time.
DETECT_MODEL = ("microsoft/table-transformer-detection", "2357cbe2b5a5d1c03e54f32764f06058933b65ab")
STRUCTURE_MODEL = ("microsoft/table-transformer-structure-recognition-v1.1-all",
                   "7587a7ef111d9dcbf8ac695f1376ab7014340a0c")

# The page is rendered once at this resolution; detection then resizes its long
# side to DETECT_SIZE and structure recognition a region's to STRUCTURE_SIZE
# (Microsoft's reference inference sizes).
RENDER_DPI = 150
DETECT_SIZE = 800
STRUCTURE_SIZE = 1000
# Minimum detector probability for a table region, and structure probability for
# a row, column, header or spanning cell.
DETECT_THRESHOLD = 0.8
STRUCTURE_THRESHOLD = 0.5
# Share of the page width/height added around a region before reading its structure.
PAD = 0.02
# A later detection overlapping an earlier one by more than this share of its own
# area is the same table found twice.
DUPLICATE_OVERLAP = 0.5
# CPU threads for inference. Fixed, so the arithmetic is the same on every run.
THREADS = int(os.environ.get("WISEAU_TABLE_THREADS", "1"))

# The tabularity check (`is_tabular`): see ADR-046 for how these were chosen.
MAX_WORDS_PER_CELL = 2.5
MIN_ROWS = 2
MIN_COLUMNS = 2

SIGNAL = "tatr"

# How a TATR row holding several printed lines is treated (`_split_rows`):
# "aligned" splits it when 2+ cells stack the same number of lines; "lines"
# always splits; "none" keeps TATR's rows. Spanning cells from the structure
# model are used when SPANS is True (off: on the development sets they cost
# words and structure both, ADR-046).
ROW_SPLIT = "aligned"
SPANS = False
# A projected row header's words must run on with no gap wider than this many
# word heights (a column gap is several).
PROJECTED_MAX_GAP = 1.5


class Region(NamedTuple):
    """One detected table: where it is, what it holds, and whether it passed the check."""

    box: tuple[float, float, float, float]   # PDF points
    score: float
    rows: tuple[tuple[ir.Cell, ...], ...]
    lines: tuple[Line, ...]                  # the psm 3 lines it takes words from
    words: tuple[Word, ...]                  # the words it takes
    features: dict
    accepted: bool


def enabled() -> bool:
    """True when `WISEAU_TABLES=tatr` and the model stack is importable."""
    if os.environ.get("WISEAU_TABLES", "").strip().lower() != "tatr":
        return False
    try:
        import timm  # noqa: F401
        import torch  # noqa: F401
        import transformers  # noqa: F401
    except ImportError:
        return False
    return True


# --- models ---------------------------------------------------------------------

_lock = threading.Lock()
_models: dict = {}


def _load():
    """Both models, loaded once per process (a few seconds; about 600 MB)."""
    with _lock:
        if not _models:
            import torch
            from transformers import TableTransformerForObjectDetection
            from transformers.utils import logging as hf_logging

            started = time.perf_counter()
            # The checkpoints carry three unused batch-norm counters; transformers
            # warns about them at length on every load. They are expected.
            hf_logging.set_verbosity_error()
            torch.set_num_threads(THREADS)
            loaded = {}
            for key, (name, revision) in (("detect", DETECT_MODEL), ("structure", STRUCTURE_MODEL)):
                loaded[key] = TableTransformerForObjectDetection.from_pretrained(
                    name, revision=revision, use_pretrained_backbone=False).eval()
            loaded["mean"] = torch.tensor([0.485, 0.456, 0.406]).view(3, 1, 1)
            loaded["std"] = torch.tensor([0.229, 0.224, 0.225]).view(3, 1, 1)
            _models.update(loaded)
            logger.info("Table Transformer loaded", extra={"wiseau": {
                "threads": THREADS, "seconds": round(time.perf_counter() - started, 1)}})
        return _models


def _predict(model_key: str, image, max_size: int, threshold: float) -> list[tuple[str, float, tuple]]:
    """(label, probability, box in the image's pixels) for every query above threshold.

    Microsoft's reference inference: long side resized to ``max_size``, ImageNet
    normalisation, the best non-background class per query.
    """
    import numpy as np
    import torch
    from PIL import Image

    m = _load()
    model = m[model_key]
    scale = max_size / max(image.size)
    resized = image.resize((round(image.width * scale), round(image.height * scale)), Image.BILINEAR)
    x = torch.from_numpy(np.asarray(resized, np.float32) / 255.0).permute(2, 0, 1)
    x = ((x - m["mean"]) / m["std"]).unsqueeze(0)
    with torch.inference_mode():
        out = model(pixel_values=x)
    probs = out.logits.softmax(-1)[0, :, :-1]
    scores, labels = probs.max(-1)
    found = []
    for sc, lab, (cx, cy, w, h) in zip(scores.tolist(), labels.tolist(), out.pred_boxes[0].tolist()):
        if sc >= threshold:
            found.append((model.config.id2label[lab], sc,
                          ((cx - w / 2) * image.width, (cy - h / 2) * image.height,
                           (cx + w / 2) * image.width, (cy + h / 2) * image.height)))
    return sorted(found, key=lambda f: (f[0], f[2][1], f[2][0], -f[1]))


def _render(page: pymupdf.Page):
    from PIL import Image

    pix = page.get_pixmap(dpi=RENDER_DPI, alpha=False, colorspace=pymupdf.csRGB)
    return Image.frombytes("RGB", (pix.width, pix.height), pix.samples)


# --- geometry -------------------------------------------------------------------


def _overlap(a0: float, a1: float, b0: float, b1: float) -> float:
    return max(0.0, min(a1, b1) - max(a0, b0))


def _area(b) -> float:
    return max(0.0, b[2] - b[0]) * max(0.0, b[3] - b[1])


def _intersection(a, b) -> float:
    return _overlap(a[0], a[2], b[0], b[2]) * _overlap(a[1], a[3], b[1], b[3])


def _best(span: tuple[float, float], bands: Sequence[tuple[float, float]]) -> int:
    """The band a span overlaps most; the nearest by centre if it overlaps none."""
    c = (span[0] + span[1]) / 2
    return max(range(len(bands)),
               key=lambda i: (_overlap(span[0], span[1], *bands[i]), -abs(c - (bands[i][0] + bands[i][1]) / 2), -i))


def _line_of(words: Sequence[Word]) -> Line:
    return Line(min(w.x0 for w in words), min(w.y0 for w in words), max(w.x1 for w in words),
                max(w.y1 for w in words), " ".join(w.text for w in words))


def _cell_text(words: Sequence[Word]) -> str:
    """A cell's words as read: visual lines top to bottom, each left to right."""
    if not words:
        return ""
    height = median(w.y1 - w.y0 for w in words)
    lines: list[tuple[float, list[Word]]] = []
    for w in sorted(words, key=lambda w: ((w.y0 + w.y1) / 2, w.x0, w.text)):
        c = (w.y0 + w.y1) / 2
        if lines and c - lines[-1][0] <= 0.5 * height:
            lines[-1][1].append(w)
        else:
            lines.append((c, [w]))
    return " ".join(w.text for _, ws in lines for w in sorted(ws, key=lambda w: (w.x0, w.text)))


# --- one region ------------------------------------------------------------------


def _structure(page_img, box_px, scale: float) -> dict:
    """Rows, columns, header rows, projected row headers and spans of one region, in points."""
    W, H = page_img.size
    px, py = PAD * W, PAD * H
    cx0, cy0 = max(0.0, box_px[0] - px), max(0.0, box_px[1] - py)
    cx1, cy1 = min(float(W), box_px[2] + px), min(float(H), box_px[3] + py)
    crop = page_img.crop((int(cx0), int(cy0), int(cx1), int(cy1)))
    found: dict[str, list[tuple[float, float, float, float]]] = {}
    for label, _score, (x0, y0, x1, y1) in _predict("structure", crop, STRUCTURE_SIZE, STRUCTURE_THRESHOLD):
        found.setdefault(label, []).append(((x0 + int(cx0)) * scale, (y0 + int(cy0)) * scale,
                                            (x1 + int(cx0)) * scale, (y1 + int(cy0)) * scale))
    return found


def _visual_lines(words: Sequence[Word]) -> list[list[Word]]:
    """Words grouped into printed lines by vertical centre, top to bottom."""
    if not words:
        return []
    height = median(w.y1 - w.y0 for w in words)
    lines: list[tuple[float, list[Word]]] = []
    for w in sorted(words, key=lambda w: ((w.y0 + w.y1) / 2, w.x0, w.text)):
        c = (w.y0 + w.y1) / 2
        if lines and c - lines[-1][0] <= 0.5 * height:
            lines[-1][1].append(w)
        else:
            lines.append((c, [w]))
    return [ws for _, ws in lines]


def _sub_rows(row_words: Sequence[Word], cols) -> Optional[dict[Word, int]]:
    """Sub-row of each word when a TATR row holds stacked records, else None (ROW_SPLIT).

    Table Transformer often draws one row around a characteristic and its
    sub-rows ("Mean / Range / Std deviation" beside three values each). A row is
    split when at least two of its cells hold the same number n >= 2 of printed
    lines: lines that line up across cells are records. Sub-row k is the k-th
    line of each such cell, counted per column, so a slightly skewed scan cannot
    shift a value into its neighbour's row; other words go to the sub-row whose
    centre is nearest. A single wrapped cell ("dark red-pink / with paler
    border" beside one-line cells) does not split its row. "lines" splits every
    row with 2+ printed lines the same way; "none" never splits.
    """
    if ROW_SPLIT == "none":
        return None
    by_col: dict[int, list[list[Word]]] = {}
    for c in range(len(cols)):
        lines = _visual_lines([w for w in row_words if _best((w.x0, w.x1), cols) == c])
        if lines:
            by_col[c] = lines
    counts = [len(v) for v in by_col.values() if len(v) >= 2]
    agreed = [n for n in sorted(set(counts), reverse=True) if counts.count(n) >= 2]
    if ROW_SPLIT == "lines" and counts:
        agreed = [max(counts)]
    if not agreed:
        return None
    n = agreed[0]
    ref = [v for v in by_col.values() if len(v) == n]
    centres = [median(sum((w.y0 + w.y1) / 2 for w in col[k]) / len(col[k]) for col in ref) for k in range(n)]
    out: dict[Word, int] = {}
    for lines in by_col.values():
        if len(lines) == n:
            for k, ws in enumerate(lines):
                for w in ws:
                    out[w] = k
        else:
            for ws in lines:
                cy = sum((w.y0 + w.y1) / 2 for w in ws) / len(ws)
                k = min(range(n), key=lambda i: (abs(centres[i] - cy), i))
                for w in ws:
                    out[w] = k
    return out


def _runs_on(words: Sequence[Word]) -> bool:
    """True when a row's words read as one run of text, with no column-sized gap."""
    ws = sorted(words, key=lambda w: (w.x0, w.y0, w.text))
    if len(ws) < 2:
        return True
    height = median(w.y1 - w.y0 for w in ws)
    return all(b.x0 - a.x1 <= PROJECTED_MAX_GAP * height for a, b in zip(ws, ws[1:]))


def _grid(found: dict, words: Sequence[Word]):
    """Words into cells: (rows of cells, header row indices), empty rows/columns dropped."""
    rows = sorted(((b[1], b[3]) for b in found.get("table row", [])), key=lambda r: (r[0] + r[1], r))
    cols = sorted(((b[0], b[2]) for b in found.get("table column", [])), key=lambda c: (c[0] + c[1], c))
    if not rows or not cols:
        return None
    projected = found.get("table projected row header", [])
    headers = found.get("table column header", [])
    spans = found.get("table spanning cell", [])

    # Rows are assigned per psm 3 line, not per word: on a slightly skewed scan
    # TATR's row boxes cut across a printed line, and its words must stay together.
    by_line: dict[tuple[int, int, int], list[Word]] = {}
    for w in words:
        by_line.setdefault(w.key, []).append(w)
    row_of: dict[Word, int] = {}
    for ws in by_line.values():
        r = _best((median(w.y0 for w in ws), median(w.y1 for w in ws)), rows)
        for w in ws:
            row_of[w] = r
    members: dict[int, list[Word]] = {}
    for w in words:
        members.setdefault(row_of[w], []).append(w)
    # A row TATR calls a label printed across the table (projected row header)
    # keeps its words in the first cell, but only if they really run on: TATR
    # sometimes gives the label to a data row.
    label_rows = {r for r, ws in members.items()
                  if any(_overlap(*rows[r], b[1], b[3]) >= 0.5 * (rows[r][1] - rows[r][0]) for b in projected)
                  and _runs_on(ws)}
    sub_of: dict[Word, int] = {}
    for r, ws in members.items():
        if r not in label_rows:
            sub_of.update(_sub_rows(ws, cols) or {})

    cells: dict[tuple[int, int, int], list[Word]] = {}
    span_of: dict[tuple[int, int, int], tuple[int, int]] = {}
    for w in words:
        r = row_of[w]
        key_r = (r, sub_of.get(w, 0))
        c = 0 if r in label_rows else _best((w.x0, w.x1), cols)
        if SPANS:
            cx, cy = (w.x0 + w.x1) / 2, (w.y0 + w.y1) / 2
            for b in spans:
                if b[0] <= cx <= b[2] and b[1] <= cy <= b[3]:
                    c0, c1 = _best((b[0], b[0]), cols), _best((b[2], b[2]), cols)
                    c = min(c0, c1)
                    span_of[(*key_r, c)] = (c, max(c0, c1))
                    break
        cells.setdefault((*key_r, c), []).append(w)
    used_rows = sorted({(r, k) for r, k, _ in cells})
    used_cols = sorted({c for _, _, c in cells})
    header_rows = {i for i, (r, _k) in enumerate(used_rows)
                   if any(_overlap(*rows[r], b[1], b[3]) >= 0.5 * (rows[r][1] - rows[r][0]) for b in headers)}
    out = []
    for r, k in used_rows:
        row = []
        for c in used_cols:
            ws = cells.get((r, k, c), [])
            cs = 1
            if (r, k, c) in span_of:
                c0, c1 = span_of[(r, k, c)]
                cs = max(1, sum(1 for x in used_cols if c0 <= x <= c1))
            row.append(ir.Cell(_cell_text(ws), ir.BBox.around(ws) if ws else None, 1, cs))
        out.append(tuple(row))
    return tuple(out), header_rows


def features(rows: Sequence[Sequence[ir.Cell]]) -> dict:
    """What the tabularity check reads: size, and how much text a cell holds."""
    filled = [c for row in rows for c in row if c.text]
    words = [len(c.text.split()) for c in filled]
    tokens = [t for c in filled for t in c.text.split()]
    return {
        "rows": len(rows),
        "columns": max((len(r) for r in rows), default=0),
        "filled": len(filled),
        "words_per_cell": round(sum(words) / len(words), 3) if words else 0.0,
        "median_words_per_cell": median(words) if words else 0,
        "numeric_share": round(sum(any(ch.isdigit() for ch in t) for t in tokens) / len(tokens), 3) if tokens else 0.0,
        "long_cell_share": round(sum(n >= 6 for n in words) / len(words), 3) if words else 0.0,
    }


def is_tabular(f: dict) -> bool:
    """A table, not prose boxed as one: at least 2 x 2, and short cells on average.

    A prose column read as a table cell holds a line or more of running text (the
    two-column pages the Tier 1 bake-off lost on average 6-10 words a cell); a
    table cell holds a value or a short label.
    """
    return (f["rows"] >= MIN_ROWS and f["columns"] >= MIN_COLUMNS
            and f["words_per_cell"] <= MAX_WORDS_PER_CELL)


def _page_lines(words: Sequence[Word], present: Sequence[Line]) -> dict[Line, list[Word]]:
    """The page's psm 3 lines that its elements hold, each with its words."""
    by_key: dict[tuple[int, int, int], list[Word]] = {}
    for w in words:
        by_key.setdefault(w.key, []).append(w)
    have = set(present)
    return {ln: ws for ws in by_key.values() if (ln := _line_of(ws)) in have}


class Detection(NamedTuple):
    """One table the detector proposed, with its structure, before any words."""

    score: float
    box: tuple[float, float, float, float]          # PDF points
    structure: dict                                 # label -> boxes in PDF points


def detect(page: pymupdf.Page) -> list[Detection]:
    """Table Transformer on the page image: table regions and their structure.

    The expensive half of the pass (model inference); `regions_from` does the rest.
    """
    img = _render(page)
    scale = 72.0 / RENDER_DPI
    out: list[Detection] = []
    for label, score, box_px in _predict("detect", img, DETECT_SIZE, DETECT_THRESHOLD):
        if label != "table":
            continue
        box = tuple(v * scale for v in box_px)
        if any(_intersection(box, d.box) > DUPLICATE_OVERLAP * max(_area(box), 1e-6) for d in out):
            continue
        out.append(Detection(score, box, _structure(img, box_px, scale)))
    return out


def regions_from(detections: Sequence[Detection], words: Sequence[Word], present: Sequence[Line]) -> list[Region]:
    """The detections read into cells from psm 3's words, each checked.

    ``words`` are psm 3's words (PDF points) and ``present`` the lines the page's
    elements hold (photo noise already dropped): only those lines' words can move
    into a table, and a word moves into at most one, so the page's text is
    partitioned, never duplicated.
    """
    lines = _page_lines(words, present)
    regions: list[Region] = []
    taken: set[Word] = set()
    for det in detections:
        box = det.box
        member_words, member_lines = [], []
        for ln, ws in lines.items():
            inside = [w for w in ws if w not in taken
                      and box[0] <= (w.x0 + w.x1) / 2 <= box[2] and box[1] <= (w.y0 + w.y1) / 2 <= box[3]]
            if inside:
                member_words.extend(inside)
                member_lines.append(ln)
        if not member_words:
            continue
        grid = _grid(det.structure, member_words)
        if grid is None:
            regions.append(Region(box, det.score, (), tuple(member_lines), tuple(member_words),
                                  {"rows": 0, "columns": 0}, False))
            continue
        rows, header_rows = grid
        if header_rows:
            rows = tuple(tuple(replace(c, header=True) for c in row) if i in header_rows else row
                         for i, row in enumerate(rows))
        f = features(rows)
        ok = is_tabular(f)
        if ok:
            taken.update(member_words)
        regions.append(Region(box, det.score, rows, tuple(member_lines), tuple(member_words), f, ok))
    return regions


def read_tables(page: pymupdf.Page, words: Sequence[Word], present: Sequence[Line]) -> list[Region]:
    """Every table the detector finds on ``page``, read into cells and checked."""
    return regions_from(detect(page), words, present)


# --- the IR pass ------------------------------------------------------------------


def table_element(region: Region) -> ir.Element:
    return ir.Element(type=ir.TABLE, text=ir.rows_text(region.rows), source=SIGNAL, lines=region.lines,
                      bbox=ir.BBox(*region.box), rows=region.rows, signal=SIGNAL, score=round(region.score, 4))


def apply(elements: Sequence[ir.Element], regions: Sequence[Region],
          words: Sequence[Word]) -> list[ir.Element]:
    """The page's elements with each accepted region's words replaced by its table.

    A line the table took every word of disappears from its element; a line it
    took some words of stays with the rest of its words. The table goes where the
    first line it took from was: what is left of that element above the table
    comes before it, the rest after.
    """
    accepted = [r for r in regions if r.accepted]
    if not accepted:
        return list(elements)
    owner = {w: n for n, r in enumerate(accepted) for w in r.words}
    lines = _page_lines(words, [ln for el in elements for ln in el.lines])
    placed: set[int] = set()
    out: list[ir.Element] = []
    for el in elements:
        hits: list[int] = []
        residual: dict[Line, Optional[Line]] = {}
        for ln in el.lines:
            ws = lines.get(ln)
            if not ws:
                continue
            gone = [owner[w] for w in ws if w in owner]
            if not gone:
                continue
            hits.extend(n for n in gone if n not in hits)
            rest = [w for w in ws if w not in owner]
            residual[ln] = _line_of(rest) if rest else None
        if not hits:
            out.append(el)
            continue
        kept = [residual.get(ln, ln) for ln in el.lines]
        kept = [ln for ln in kept if ln is not None]
        top = min(accepted[n].box[1] for n in hits)
        above = [ln for ln in kept if (ln.y0 + ln.y1) / 2 < top]
        below = [ln for ln in kept if (ln.y0 + ln.y1) / 2 >= top]
        if above:
            out.append(_remainder(el, above, residual))
        for n in hits:
            if n not in placed:
                placed.add(n)
                out.append(table_element(accepted[n]))
        if below:
            out.append(_remainder(el, below, residual))
    return out


def _remainder(el: ir.Element, lines: Sequence[Line], residual: dict) -> ir.Element:
    """What is left of a psm 3 element after a table took some of its words."""
    if el.rows:  # an ADR-038 stitched table: its cells are lines; keep what is left of each
        keep = set(lines)
        rows = []
        for row in el.rows:
            cells = []
            for c in row:
                ln = Line(c.bbox.x0, c.bbox.y0, c.bbox.x1, c.bbox.y1, c.text)
                ln = residual.get(ln, ln)
                if ln is not None and ln in keep:
                    cells.append(ir.Cell(ln.text, ir.BBox.around([ln])))
            if cells:
                rows.append(tuple(cells))
        rows = tuple(rows)
        return replace(el, rows=rows, text=ir.rows_text(rows), lines=tuple(lines), bbox=ir.BBox.around(lines))
    return replace(el, text="\n".join(ln.text for ln in lines), lines=tuple(lines), bbox=ir.BBox.around(lines))
