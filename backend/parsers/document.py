"""The canonical document IR: what a conversion found, before it is Markdown (ADR-043).

Every converter used to go straight from its engine's output to a Markdown string,
so whatever the engine knew about *where* text sat and *what kind* of text it was
(a stitched table row, an OCR line's box, a heading pymupdf4llm recognised) was
thrown away at once. This module is the intermediate representation that keeps
it: a flat, reading-ordered list of **elements**, each with the page it came from,
its bounding box when the engine reports one, the engine that produced it, and the
text as that engine produced it. `serializer.py` turns a `Document` into Markdown.

Three rules shape the model (roadmap Phase 14, item 2.1):

* **The transcription is never edited.** `Element.text` is what the engine read,
  in its reading order. Anything *inferred* about the text (its type, a table's
  rows) sits beside it, with the `signal` that inferred it and a `confidence`
  that is ``None`` unless the signal has a calibrated one: a heuristic reports
  how sure it is or says it does not know (ADR-030). Later structure recovery
  (Tier 3) adds fields and relations; it does not rewrite `text`.
* **Provenance on every element.** `source` names the engine (``pymupdf4llm``,
  ``tesseract-psm3``, ``tesseract-geometric``, ``mupdf-ocr``, ``mammoth``,
  ``docling``, ...), `page` the 1-based page, `bbox` the box in PDF points
  (``None`` when the engine does not say, as pymupdf4llm's Markdown does not).
* **Deterministic.** Elements are in reading order, ids are assigned from that
  order, and `to_dict()` writes fields in a fixed order with coordinates rounded,
  so the same conversion gives the same JSON byte for byte on the default path.

The model copies the shape of docling's ``DoclingDocument`` (elements with
provenance, a body in reading order, relations kept apart) without depending on
docling, which is opt-in (ADR-027). The default path feeds it first.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Iterable, Optional, Sequence

from .reading_order import Line

# Bumped when the JSON shape changes incompatibly.
SCHEMA_VERSION = "0.1"

# Element types. Adapters today emit TEXT (an OCR block whose role is unknown),
# PARAGRAPH, HEADING, LIST, TABLE, CODE, FIGURE and RULE; the rest are the
# vocabulary that structure recovery (roadmap Tier 3) will use.
TEXT = "text"
TITLE = "title"
HEADING = "heading"
PARAGRAPH = "paragraph"
LIST = "list"
LIST_ITEM = "list_item"
TABLE = "table"
CODE = "code"
FIGURE = "figure"
CAPTION = "caption"
FOOTNOTE = "footnote"
EQUATION = "equation"
RULE = "rule"
PAGE_HEADER = "page_header"
PAGE_FOOTER = "page_footer"
PAGE_NUMBER = "page_number"

ELEMENT_TYPES = frozenset({
    TEXT, TITLE, HEADING, PARAGRAPH, LIST, LIST_ITEM, TABLE, CODE, FIGURE, CAPTION,
    FOOTNOTE, EQUATION, RULE, PAGE_HEADER, PAGE_FOOTER, PAGE_NUMBER,
})

# Relation types between elements (roadmap 2.1). Structure recovery
# (`recover.py`, roadmap Tier 3) produces caption_of, footnote_for,
# continues_from and continues_table; the rest are vocabulary.
RELATION_TYPES = frozenset({
    "parent_of", "caption_of", "footnote_for", "continues_from", "continues_table",
    "references", "source_for",
})

# How an element's text is written: plain text (OCR) or a Markdown fragment from
# an engine that writes Markdown itself (pymupdf4llm, Mammoth, docling, GLM-OCR).
PLAIN = "plain"
MARKDOWN = "markdown"

# How a page's text was obtained.
TEXT_LAYER = "text-layer"
OCR = "ocr"
REMOTE = "remote"

_ROUND = 2  # decimal places for coordinates in JSON


@dataclass(frozen=True)
class BBox:
    """A box in PDF points, origin top-left (PyMuPDF's convention)."""

    x0: float
    y0: float
    x1: float
    y1: float

    @staticmethod
    def around(boxes: Iterable[object]) -> Optional["BBox"]:
        """The smallest box around anything with x0/y0/x1/y1, or None if empty."""
        boxes = list(boxes)
        if not boxes:
            return None
        return BBox(min(b.x0 for b in boxes), min(b.y0 for b in boxes),
                    max(b.x1 for b in boxes), max(b.y1 for b in boxes))

    def to_list(self) -> list[float]:
        return [round(v, _ROUND) for v in (self.x0, self.y0, self.x1, self.y1)]


@dataclass(frozen=True)
class Cell:
    """One cell of a table row: its text and box."""

    text: str
    bbox: Optional[BBox] = None
    row_span: int = 1
    col_span: int = 1
    # A column-header cell (the row that names the columns).
    header: bool = False


@dataclass(frozen=True)
class Element:
    """One block of the document, in reading order.

    `id` and `page` are filled in when the element is added to a `Document`;
    adapters build elements without them.
    """

    type: str
    text: str
    source: str
    text_format: str = PLAIN
    bbox: Optional[BBox] = None
    page: Optional[int] = None
    id: str = ""
    # OCR lines (boxes and text) the element was read from, in reading order.
    lines: tuple[Line, ...] = ()
    # A table's rows, each a tuple of cells, left to right.
    rows: tuple[tuple[Cell, ...], ...] = ()
    # Heading level (1 = top), when the type is HEADING.
    level: Optional[int] = None
    # What inferred `type` (and `rows`/`level`), and how sure it is. None means
    # the signal has no calibrated confidence; it is not invented.
    signal: Optional[str] = None
    confidence: Optional[float] = None
    # The signal's own raw score when it has one (e.g. a model's probability).
    # Uncalibrated, so it is not a `confidence`.
    score: Optional[float] = None
    # How `bbox` was found when the engine did not report it (e.g.
    # "text-layer-alignment", ADR-044), and the measured share of the element's
    # words that method located. None when the box came from the engine itself.
    bbox_signal: Optional[str] = None
    bbox_coverage: Optional[float] = None
    # What set `level` when a structure pass re-levelled a heading (roadmap 3.1),
    # kept apart from `signal`, which says what made the element a heading.
    level_signal: Optional[str] = None
    # What re-typed the element when a structure pass did (e.g. page furniture,
    # roadmap 3.2). `signal` still names what first typed it.
    role_signal: Optional[str] = None
    # A figure's exported image, as a path relative to the document (3.4).
    asset: Optional[str] = None

    def __post_init__(self) -> None:
        if self.type not in ELEMENT_TYPES:
            raise ValueError(f"unknown element type {self.type!r}")
        if self.text_format not in (PLAIN, MARKDOWN):
            raise ValueError(f"unknown text format {self.text_format!r}")

    def to_dict(self) -> dict:
        out: dict = {"id": self.id, "type": self.type, "page": self.page,
                     "bbox": self.bbox.to_list() if self.bbox else None,
                     "source": self.source, "text_format": self.text_format, "text": self.text}
        if self.level is not None:
            out["level"] = self.level
        if self.rows:
            out["rows"] = [[_cell_dict(c) for c in row] for row in self.rows]
        if self.lines:
            out["lines"] = [{"bbox": [round(v, _ROUND) for v in ln[:4]], "text": ln.text}
                            for ln in self.lines]
        if self.signal is not None:
            out["signal"] = self.signal
            out["confidence"] = self.confidence
        if self.score is not None:
            out["score"] = self.score
        if self.bbox_signal is not None:
            out["bbox_signal"] = self.bbox_signal
            out["bbox_coverage"] = None if self.bbox_coverage is None else round(self.bbox_coverage, 3)
        if self.level_signal is not None:
            out["level_signal"] = self.level_signal
        if self.role_signal is not None:
            out["role_signal"] = self.role_signal
        if self.asset is not None:
            out["asset"] = self.asset
        return out


def _cell_dict(cell: Cell) -> dict:
    out: dict = {"text": cell.text, "bbox": cell.bbox.to_list() if cell.bbox else None}
    if cell.row_span != 1 or cell.col_span != 1:
        out["row_span"], out["col_span"] = cell.row_span, cell.col_span
    if cell.header:
        out["header"] = True
    return out


@dataclass(frozen=True)
class Relation:
    """A typed link between two elements, by id (e.g. ``caption_of``)."""

    type: str
    source: str
    target: str
    # What inferred the relation (None for one an engine stated).
    signal: Optional[str] = None

    def __post_init__(self) -> None:
        if self.type not in RELATION_TYPES:
            raise ValueError(f"unknown relation type {self.type!r}")

    def to_dict(self) -> dict:
        out = {"type": self.type, "source": self.source, "target": self.target}
        if self.signal is not None:
            out["signal"] = self.signal
        return out


@dataclass(frozen=True)
class PageInfo:
    """One page: its number, size in points, and how its text was obtained."""

    number: int
    method: str
    source: str
    width: Optional[float] = None
    height: Optional[float] = None

    def to_dict(self) -> dict:
        size = None if self.width is None else [round(self.width, _ROUND), round(self.height, _ROUND)]
        return {"number": self.number, "method": self.method, "source": self.source, "size": size}


@dataclass
class Document:
    """A converted document: pages, elements in reading order, and relations."""

    pages: list[PageInfo] = field(default_factory=list)
    elements: list[Element] = field(default_factory=list)
    relations: list[Relation] = field(default_factory=list)

    def add_page(self, info: PageInfo, elements: Sequence[Element]) -> None:
        """Append a page and its elements, stamping each with the page and an id."""
        self.pages.append(info)
        self.extend(elements, page=info.number)

    def extend(self, elements: Sequence[Element], *, page: Optional[int] = None) -> None:
        """Append elements (not tied to a page when `page` is None) with ids."""
        for el in elements:
            n = len(self.elements) + 1
            self.elements.append(replace(el, page=page, id=f"e{n}"))

    def to_dict(self) -> dict:
        return {"schema": SCHEMA_VERSION,
                "pages": [p.to_dict() for p in self.pages],
                "elements": [e.to_dict() for e in self.elements],
                "relations": [r.to_dict() for r in self.relations]}

    def page_elements(self, page: int) -> list[Element]:
        return [el for el in self.elements if el.page == page]


def _bbox(v) -> Optional[BBox]:
    return BBox(*v) if v else None


def element_from_dict(d: dict) -> Element:
    """An element from its `to_dict()` form (coordinates as rounded there)."""
    rows = tuple(tuple(Cell(text=c["text"], bbox=_bbox(c.get("bbox")), row_span=c.get("row_span", 1),
                            col_span=c.get("col_span", 1), header=c.get("header", False)) for c in row)
                 for row in d.get("rows", ()))
    lines = tuple(Line(*ln["bbox"], ln["text"]) for ln in d.get("lines", ()))
    return Element(type=d["type"], text=d["text"], source=d["source"], text_format=d["text_format"],
                   bbox=_bbox(d.get("bbox")), page=d.get("page"), id=d.get("id", ""), lines=lines,
                   rows=rows, level=d.get("level"), signal=d.get("signal"), confidence=d.get("confidence"),
                   score=d.get("score"), bbox_signal=d.get("bbox_signal"), bbox_coverage=d.get("bbox_coverage"),
                   level_signal=d.get("level_signal"), role_signal=d.get("role_signal"), asset=d.get("asset"))


def from_dict(d: dict) -> Document:
    """A document from its `to_dict()` form (the benchmark harness reads saved IR)."""
    return Document(
        pages=[PageInfo(number=p["number"], method=p["method"], source=p["source"],
                        width=p["size"][0] if p.get("size") else None,
                        height=p["size"][1] if p.get("size") else None) for p in d["pages"]],
        elements=[element_from_dict(e) for e in d["elements"]],
        relations=[Relation(r["type"], r["source"], r["target"], r.get("signal")) for r in d.get("relations", ())])


def rows_text(rows: Sequence[Sequence[Cell]]) -> str:
    """A table read row by row, cells joined by spaces (the OCR path's reading)."""
    return "\n".join(" ".join(c.text for c in row) for row in rows)
