"""Figures as assets: the images behind a document's figures (roadmap 3.4, ADR-052).

ADR-024 keeps machine payloads out of the Markdown, so a figure in a plain
conversion writes nothing. The **bundle** (`POST /convert/bundle`) is where a
figure's image travels instead: a ZIP of `document.md`, `document.json` (the
IR) and `assets/figure-001.png`, ..., with the Markdown linking each figure as
`![caption](assets/figure-001.png)` where it stands in reading order.

Where figures come from:

* **Scanned pages**: the layout pass's picture regions (FIGURE elements,
  `layout.py`), cropped from the page at `DPI`.
* **Text-layer pages**: the page's placed images (`page.get_image_info()`), which
  PyMuPDF4LLM does not write. Each becomes an empty FIGURE element, inserted
  after the last element that ends above the image's top and overlaps it
  horizontally (the page's start when none does). Images smaller than
  `MIN_SIDE` points on a side (rules, bullets, logos' slivers) and any covering
  most of the page (a scan's page image under an OCR layer) are not figures.

Crops are rendered, not extracted, so every asset is a PNG of what the page
shows there, at a fixed resolution, the same bytes every run. Text is never
edited and no element moves: text-layer figures are additions.
"""

from __future__ import annotations

from dataclasses import replace

import pymupdf

from . import document as ir

DPI = 150
MIN_SIDE = 36.0
MAX_PAGE_SHARE = 0.85
SIGNAL = "pdf-image"


def _overlaps_x(a: ir.BBox, b: ir.BBox) -> bool:
    return min(a.x1, b.x1) - max(a.x0, b.x0) > 0


def add_text_layer_figures(doc: ir.Document, pdf: pymupdf.Document) -> None:
    """Insert FIGURE elements for the placed images of `doc`'s text-layer pages."""
    for info in doc.pages:
        if info.method != ir.TEXT_LAYER or not 1 <= info.number <= pdf.page_count:
            continue
        page = pdf[info.number - 1]
        area = page.rect.width * page.rect.height
        boxes = []
        for img in page.get_image_info():
            x0, y0, x1, y1 = img["bbox"]
            w, h = x1 - x0, y1 - y0
            if w < MIN_SIDE or h < MIN_SIDE or w * h > MAX_PAGE_SHARE * area:
                continue
            box = ir.BBox(max(x0, 0.0), max(y0, 0.0), min(x1, page.rect.width), min(y1, page.rect.height))
            if box not in boxes:
                boxes.append(box)
        for box in sorted(boxes, key=lambda b: (b.y0, b.x0)):
            _insert(doc, info.number, ir.Element(type=ir.FIGURE, text="", source=SIGNAL, bbox=box,
                                                 page=info.number, signal=SIGNAL))
    for n, el in enumerate(doc.elements, 1):  # ids stay in reading order
        if el.id != f"e{n}":
            _renumber(doc)
            break


def _insert(doc: ir.Document, page: int, figure: ir.Element) -> None:
    indices = [i for i, el in enumerate(doc.elements) if el.page == page]
    at = indices[0] if indices else len(doc.elements)
    for i in indices:
        el = doc.elements[i]
        if el.bbox is not None and el.bbox.y1 <= figure.bbox.y0 + 1 and _overlaps_x(el.bbox, figure.bbox):
            at = i + 1
    doc.elements.insert(at, figure)


def _renumber(doc: ir.Document) -> None:
    mapping = {}
    for n, el in enumerate(doc.elements, 1):
        if el.id:
            mapping[el.id] = f"e{n}"
        doc.elements[n - 1] = replace(el, id=f"e{n}")
    doc.relations = [replace(r, source=mapping.get(r.source, r.source), target=mapping.get(r.target, r.target))
                     for r in doc.relations]


def export(doc: ir.Document, pdf: pymupdf.Document) -> dict[str, bytes]:
    """Render each figure with a box on a PDF page; set its `asset`; return path -> PNG."""
    assets: dict[str, bytes] = {}
    for i, el in enumerate(doc.elements):
        if el.type != ir.FIGURE or el.bbox is None or el.page is None or not 1 <= el.page <= pdf.page_count:
            continue
        page = pdf[el.page - 1]
        clip = pymupdf.Rect(el.bbox.x0, el.bbox.y0, el.bbox.x1, el.bbox.y1) & page.rect
        if clip.is_empty or clip.width < 2 or clip.height < 2:
            continue
        path = f"assets/figure-{len(assets) + 1:03d}.png"
        assets[path] = page.get_pixmap(dpi=DPI, clip=clip, alpha=False).tobytes("png")
        doc.elements[i] = replace(el, asset=path)
    return assets
