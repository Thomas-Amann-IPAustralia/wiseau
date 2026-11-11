"""Layout regions on scanned pages: block boundaries and types from PP-DocLayoutV3 (ADR-048).

Tesseract reads a scan's words well, and ADR-035/038 put them in a good order,
but the page's *structure* is lost: no line is known to be a heading, and blocks
run together wherever psm 3 joined them (set S: a third of gold blocks merged,
one heading in ten found). A layout detector sees the page the way a reader
does. This module is a pass over the IR of one OCR'd page:

1. **Find regions.** PP-DocLayoutV3 (a detector, not a generative model: no
   text is produced) proposes boxes with a category (`paragraph_title`, `text`,
   `table`, `header`, `figure_title`, ...) in its predicted reading order.
2. **Regroup the page's lines into them.** Each OCR line goes to the region
   holding at least half of its area. Each region becomes one element, typed by
   its category (`paragraph_title` → heading, `doc_title` → title, `header` /
   `footer` / `number` → page furniture, ...), in the model's order. A line no
   region holds stays in what is left of its own element, placed after the
   region that took that element's previous line.
3. **Never touch a word, or a table.** Lines move whole and their text is
   unchanged, so the page's words are exactly the OCR's. A Table Transformer
   table (ADR-046) and an ADR-038 stitched table move as one unit, keeping their
   rows: a layout model that calls a table's columns separate text regions would
   otherwise read it column by column.

The serializer writes a heading or title this pass typed as `##` / `#`
(`serializer.render_element`). The other types change the IR but not the
Markdown.

Validated on two blind structural gold sets (`docs/benchmarks/
2026-11-10-layout-regions.md`, `...-set-t.md`). On the 14 held-out pages of set
T, over the table pass: block F1 0.668 → 0.830, merged blocks 24% → 5%,
headings found 0% → 78% (95% of them real). Reading order, tables and every
word are unchanged.

It is **opt-in** (`WISEAU_LAYOUT=ppdl`) and needs PyTorch, transformers 5.x
and OpenCV (`requirements-tables.txt`). Without them, or with the variable
unset, the pass does nothing and the default path is unchanged. The model is
pinned to an exact revision, inference runs on a fixed number of CPU threads,
and every tie has a total order, so a page converts the same way every time.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from dataclasses import replace
from typing import NamedTuple, Optional, Sequence

import pymupdf

from . import document as ir
from . import model_lock

logger = logging.getLogger(__name__)

# Pinned model (Hugging Face commit id): the revision the GLM-OCR service pins.
LAYOUT_MODEL = ("PaddlePaddle/PP-DocLayoutV3_safetensors", "e4f489dc536556fc1be02b973ba06756e0d4a2ac")
# The page is rendered at this resolution; the processor then resizes it to 800 × 800.
RENDER_DPI = 200
# Minimum detector probability (the processor's own default).
THRESHOLD = 0.5
# A line belongs to a region holding at least this share of its area.
OVERLAP = 0.5
# CPU threads for inference. Fixed, so the arithmetic is the same on every run.
# torch's thread count is process-wide, so it follows the table pass's setting
# unless set here.
THREADS = int(os.environ.get("WISEAU_LAYOUT_THREADS", os.environ.get("WISEAU_TABLE_THREADS", "1")))

SIGNAL = "pp-doclayoutv3"
# Layout category -> (IR element type, heading level). Unlisted categories are text.
CATEGORY_TYPES = {
    "doc_title": (ir.TITLE, 1),
    "paragraph_title": (ir.HEADING, 2),
    "figure_title": (ir.CAPTION, None),
    "header": (ir.PAGE_HEADER, None), "header_image": (ir.PAGE_HEADER, None),
    "footer": (ir.PAGE_FOOTER, None), "footer_image": (ir.PAGE_FOOTER, None),
    "number": (ir.PAGE_NUMBER, None),
    "footnote": (ir.FOOTNOTE, None),
    # A photo credit under a figure ("(Photo supplied by applicant)").
    "vision_footnote": (ir.CAPTION, None),
    "display_formula": (ir.EQUATION, None), "formula": (ir.EQUATION, None),
    # A picture. Lines read out of it keep their text (no word is dropped), so
    # the Markdown is unchanged; the type lets a caption attach to it (3.4).
    "image": (ir.FIGURE, None), "chart": (ir.FIGURE, None),
}
# Regions that are pictures. One that holds no OCR line (ADR-037 drops the
# noise read out of photographs) becomes an empty FIGURE element where the
# detector reads it, so captions and exported images have something to attach
# to (roadmap 3.4). It writes no Markdown of its own.
FIGURE_CATEGORIES = {"image", "chart"}
# Elements the pass moves whole: tables with rows (ADR-046's and ADR-038's).
KEPT_SIGNALS = {"tatr", "tesseract-row-stitch"}


class Region(NamedTuple):
    """One detected layout region, in the detector's reading order."""

    box: tuple[float, float, float, float]   # PDF points
    category: str
    score: float


def enabled() -> bool:
    """True when `WISEAU_LAYOUT=ppdl` and the model stack is importable."""
    if os.environ.get("WISEAU_LAYOUT", "").strip().lower() != "ppdl":
        return False
    try:
        import cv2  # noqa: F401  (the processor's post-processing)
        import torch  # noqa: F401
        from transformers import PPDocLayoutV3ForObjectDetection  # noqa: F401  (transformers 5.x)
    except ImportError:
        return False
    return True


# --- model ----------------------------------------------------------------------

_lock = threading.Lock()
_model: dict = {}


def _load() -> dict:
    """The detector and its processor, loaded once per process (~1 s; ~130 MB of weights)."""
    with _lock:
        if not _model:
            with model_lock.LOAD_LOCK:   # never two model loads at once (ADR-056)
                import torch
                from transformers import AutoImageProcessor, AutoModelForObjectDetection
                from transformers.utils import logging as hf_logging

                started = time.perf_counter()
                hf_logging.set_verbosity_error()
                hf_logging.disable_progress_bar()  # one log line below, not a weight-loading bar
                torch.set_num_threads(THREADS)
                name, revision = LAYOUT_MODEL
                _model["model"] = AutoModelForObjectDetection.from_pretrained(name, revision=revision).eval()
                _model["processor"] = AutoImageProcessor.from_pretrained(name, revision=revision)
                logger.info("PP-DocLayoutV3 loaded", extra={"wiseau": {
                    "threads": THREADS, "seconds": round(time.perf_counter() - started, 1)}})
        return _model


def render(page: pymupdf.Page):
    """The page image the detector reads, and its pixel-to-point scale.

    Split from `detect_rendered` so the OCR engine can render on its own thread
    (MuPDF is not thread-safe) and run the model beside OCR (ADR-056).
    """
    from PIL import Image

    pix = page.get_pixmap(dpi=RENDER_DPI, alpha=False)
    image = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
    return image, page.rect.width / pix.width, page.rect.height / pix.height


def detect_rendered(rendered) -> list[Region]:
    """The regions in a `render` result, in the detector's reading order (no MuPDF call)."""
    import torch

    image, sx, sy = rendered
    m = _load()
    with torch.inference_mode():
        inputs = m["processor"](images=image, return_tensors="pt")
        outputs = m["model"](**inputs)
        result = m["processor"].post_process_object_detection(
            outputs, threshold=THRESHOLD, target_sizes=[(image.height, image.width)])[0]
    regions = []
    for score, label, box in zip(result["scores"], result["labels"], result["boxes"]):
        x0, y0, x1, y1 = (float(v) for v in box.tolist())
        regions.append(Region((x0 * sx, y0 * sy, x1 * sx, y1 * sy),
                              m["model"].config.id2label[int(label)], float(score)))
    return regions


def detect(page: pymupdf.Page) -> list[Region]:
    """The page's layout regions, in the detector's reading order."""
    return detect_rendered(render(page))


# --- geometry -------------------------------------------------------------------


def _inter(a, b) -> float:
    w = min(a[2], b[2]) - max(a[0], b[0])
    h = min(a[3], b[3]) - max(a[1], b[1])
    return w * h if w > 0 and h > 0 else 0.0


def _area(b) -> float:
    return max(0.0, b[2] - b[0]) * max(0.0, b[3] - b[1])


def best_region(box, regions: Sequence[Region]) -> Optional[int]:
    """The region holding the largest share (>= OVERLAP) of ``box``'s area; ties go
    to the smaller region (the more specific one), then to the earlier one."""
    area = _area(box)
    if area <= 0:
        return None
    best, best_key = None, None
    for i, r in enumerate(regions):
        share = _inter(box, r.box) / area
        if share >= OVERLAP:
            key = (share, -_area(r.box))
            if best_key is None or key > best_key:
                best, best_key = i, key
    return best


def _kept(el: ir.Element) -> bool:
    return el.type == ir.TABLE and el.signal in KEPT_SIGNALS


def _box(el: ir.Element):
    return (el.bbox.x0, el.bbox.y0, el.bbox.x1, el.bbox.y1) if el.bbox else None


# --- the pass -------------------------------------------------------------------


def apply(elements: Sequence[ir.Element], regions: Sequence[Region]) -> list[ir.Element]:
    """The page's elements regrouped into ``regions`` (see the module docstring).

    Every line of ``elements`` appears exactly once in the result, its text
    unchanged; kept tables appear whole.
    """
    if not regions:
        return list(elements)
    region_lines: dict[int, list] = {}
    region_source: dict[int, str] = {}
    rests: dict[int, list] = {}
    keys: dict[tuple, float] = {}
    last_region = -1.0  # the reading-order rank of the region that took the latest line
    sub = 0
    for e, el in enumerate(elements):
        if _kept(el) or not el.lines:
            box = _box(el)
            i = best_region(box, regions) if box else None
            sub += 1
            keys[("kept", e)] = (float(i) if i is not None else last_region + 0.5) + sub * 1e-6
            continue
        for ln in el.lines:
            i = best_region((ln.x0, ln.y0, ln.x1, ln.y1), regions)
            if i is None:
                if e not in rests:
                    sub += 1
                    keys[("rest", e)] = last_region + 0.5 + sub * 1e-6
                rests.setdefault(e, []).append(ln)
            else:
                region_lines.setdefault(i, []).append(ln)
                region_source.setdefault(i, el.source)
                keys.setdefault(("region", i), float(i))
                last_region = float(i)
    for i, r in enumerate(regions):
        if r.category in FIGURE_CATEGORIES and i not in region_lines:
            keys[("figure", i)] = float(i)
    out: list[ir.Element] = []
    for kind, n in sorted(keys, key=keys.get):
        if kind == "kept":
            out.append(elements[n])
        elif kind == "figure":
            x0, y0, x1, y1 = regions[n].box
            out.append(ir.Element(type=ir.FIGURE, text="", source=SIGNAL, bbox=ir.BBox(x0, y0, x1, y1),
                                  signal=SIGNAL, score=round(regions[n].score, 4)))
        elif kind == "rest":
            el, lines = elements[n], rests[n]
            if len(lines) == len(el.lines):  # no region took any of it: unchanged
                out.append(el)
            else:
                # What is left of an element is plain text: rows would re-emit the
                # lines a region took (kept tables never get here).
                out.append(replace(el, type=ir.TEXT if el.type == ir.TABLE else el.type, rows=(),
                                   text="\n".join(ln.text for ln in lines), lines=tuple(lines),
                                   bbox=ir.BBox.around(lines)))
        else:
            lines = region_lines[n]
            kind_, level = CATEGORY_TYPES.get(regions[n].category, (ir.TEXT, None))
            out.append(ir.Element(type=kind_, text="\n".join(ln.text for ln in lines),
                                  source=region_source[n], lines=tuple(lines), bbox=ir.BBox.around(lines),
                                  level=level, signal=SIGNAL, score=round(regions[n].score, 4)))
    return out


def read_layout(page: pymupdf.Page, elements: Sequence[ir.Element],
                regions: Optional[Sequence[Region]] = None) -> list[ir.Element]:
    """Regroup ``elements`` into ``page``'s regions: ``regions`` when already
    detected (ADR-056), else detected here."""
    return apply(elements, detect(page) if regions is None else regions)
