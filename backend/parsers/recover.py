"""Structure recovery: passes over the document IR (roadmap Phase 14, Tier 3).

The engines read the words; ADR-043's IR keeps where they sat and what the
engine knew about them. These passes infer what a reader sees on top of that:

* `headings`     heading levels (3.1, ADR-049)
* `furniture`    running headers, footers and page numbers (3.2, ADR-050)
* `continuation` paragraphs broken by a column or a page (3.3, ADR-051)
* `captions`     which figure or table a caption belongs to (3.4, ADR-052)
* `footnotes`    which block a footnote's marker sits in (3.5, ADR-053)
* `tables`       a table continued as a side-by-side panel or on the next page (3.6, ADR-054)

Three rules hold for all of them (ADR-030, ADR-043):

* **The transcription is never edited.** A pass changes an element's type or
  level, or adds a relation; `text` stays what the engine read. Every inferred
  value names the signal that produced it (`level_signal`, `role_signal`,
  `Relation.signal`), and no confidence is invented (`confidence` stays None).
* **Refuse rather than guess.** A pass that finds the evidence too thin leaves
  the document as it was, and a relation is drawn only where the rule fires.
* **Deterministic.** Pure functions of the IR (plus the PDF's own outline), no
  model, no randomness, every tie broken by reading order.

Passes are opt-in: `WISEAU_STRUCTURE` names them (comma-separated, or `all`).
Unset, `recover()` returns the document unchanged and the Markdown is what it
was. The passes run in the order above, because each later one reads what an
earlier one decided (a page number is not a paragraph to continue, and so on).
"""

from __future__ import annotations

import os
from typing import Iterable, Optional, Sequence

from . import document as ir

PASSES = ("headings", "furniture", "continuation", "captions", "footnotes", "tables")


def configured() -> tuple[str, ...]:
    """The passes `WISEAU_STRUCTURE` turns on, in run order."""
    raw = os.environ.get("WISEAU_STRUCTURE", "").strip().lower()
    if not raw:
        return ()
    names = {part.strip() for part in raw.split(",") if part.strip()}
    if "all" in names:
        return PASSES
    return tuple(p for p in PASSES if p in names)


def reader_mode() -> bool:
    """`WISEAU_FURNITURE=drop`: leave detected page furniture out of the Markdown."""
    return os.environ.get("WISEAU_FURNITURE", "keep").strip().lower() == "drop"


def recover(doc: ir.Document, passes: Optional[Iterable[str]] = None, *,
            outline: Sequence = ()) -> ir.Document:
    """Run the structure passes over `doc` (in place) and return it.

    `passes` defaults to `configured()`. `outline` is the PDF's bookmarks as
    PyMuPDF's `get_toc()` gives them (`[level, title, page]`), the first signal
    for heading levels.
    """
    chosen = set(configured() if passes is None else passes)
    unknown = chosen - set(PASSES)
    if unknown:
        raise ValueError(f"unknown structure passes: {sorted(unknown)}")
    from . import captions, continuation, cross_tables, footnotes, furniture, headings
    run = {"headings": lambda d: headings.apply(d, outline=outline), "furniture": furniture.apply,
           "continuation": continuation.apply, "captions": captions.apply,
           "footnotes": footnotes.apply, "tables": cross_tables.apply}
    for name in PASSES:
        if name in chosen:
            run[name](doc)
    return doc


# --- shared helpers -------------------------------------------------------------

BODY_TYPES = {ir.TEXT, ir.PARAGRAPH}


def replace_element(doc: ir.Document, index: int, **changes) -> None:
    from dataclasses import replace
    doc.elements[index] = replace(doc.elements[index], **changes)


def page_groups(doc: ir.Document) -> list[tuple[int, list[int]]]:
    """(page, indices of its elements) in document order; unpaged elements as page 0."""
    out: list[tuple[int, list[int]]] = []
    for i, el in enumerate(doc.elements):
        page = el.page or 0
        if not out or out[-1][0] != page:
            out.append((page, []))
        out[-1][1].append(i)
    return out


def page_size(doc: ir.Document, page: int) -> tuple[Optional[float], Optional[float]]:
    for p in doc.pages:
        if p.number == page:
            return p.width, p.height
    return None, None


def plain_text(el: ir.Element) -> str:
    """An element's words without Markdown markup, for pattern tests."""
    import re
    text = el.text
    if el.text_format == ir.MARKDOWN:
        text = re.sub(r"<[^>]+>", "", text)
        text = re.sub(r"^ {0,3}#{1,6}[ \t]+", "", text, flags=re.M)
        text = re.sub(r"(\*\*|__|~~|\*|_)", "", text)
    return text.strip()
