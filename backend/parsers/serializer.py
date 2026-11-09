"""IR -> Markdown, deterministically (ADR-043; roadmap Phase 14, item 2.2).

The one place a `document.Document` becomes the Markdown string every consumer
reads. It is deliberately **behaviour-neutral** for now: its output, after
`clean_markdown()`, matches what each converter returned before the IR existed,
byte for byte, on every page of the 15 test PDFs and every scored benchmark page
(`docs/benchmarks/2026-11-09-ir-neutrality.md`). That is the gate the roadmap set
before the IR may replace the old string path. An explicit dialect (pipe tables
for simple tables, HTML for merged cells) is the next step, and changes output on
purpose, so it ships separately.

The rules, all pure string joins with no state:

* An element renders as its `text`, except a table with `rows`, which renders row
  by row (cells joined by spaces). OCR text is plain and is not escaped, as
  before; Markdown fragments from engines that write Markdown pass through.
* Elements of one page are joined by a blank line; the page is stripped, and a
  page with no text is skipped.
* Pages are joined by a blank line, in order. Elements without a page (a remote
  engine's or Mammoth's whole-document Markdown) form one group of their own.

`split_markdown()` is the inverse helper the Markdown-writing adapters use: it
cuts an engine's Markdown into top-level blocks at blank lines (never inside a
code fence) and types each block by its leading syntax. Re-joining the blocks
with a blank line gives back the same Markdown once `clean_markdown()` has run,
because the normalizer already strips trailing whitespace and collapses runs of
blank lines.
"""

from __future__ import annotations

import re
from itertools import groupby

from . import document as ir

# A blank-line boundary: a newline, then one or more lines holding only spaces or tabs.
_BLANK_RUN = re.compile(r"\n(?:[ \t]*\n)+")
_FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})")
_HEADING = re.compile(r"^ {0,3}(#{1,6})(?:[ \t]|$)")
_LIST = re.compile(r"^ {0,3}(?:[-*+]|\d{1,9}[.)])[ \t]")
_RULE = re.compile(r"^ {0,3}(?:(?:-[ \t]*){3,}|(?:\*[ \t]*){3,}|(?:_[ \t]*){3,})$")
_FIGURE = re.compile(r"^ {0,3}!\[")

# The `signal` recorded on a block typed by its Markdown syntax.
SYNTAX_SIGNAL = "markdown-syntax"


# The explicit dialect (ADR-045). A table whose cells are real columns is written
# as a table: a pipe table when its first row is a header and no cell spans
# others, else an HTML table (which Markdown allows and the scorers strip).
# Signals whose cells are columns; ADR-038's stitched tables are not among them
# by default, because their "cells" are Tesseract line fragments.
CELL_SIGNALS = {"tatr"}
STITCHED_AS_TABLES = False
STITCH_SIGNAL = "tesseract-row-stitch"


def render_element(el: ir.Element) -> str:
    """One element as Markdown."""
    if el.type == ir.TABLE and el.rows:
        if el.signal in CELL_SIGNALS or (STITCHED_AS_TABLES and el.signal == STITCH_SIGNAL):
            return table_markdown(el.rows)
        return ir.rows_text(el.rows)
    return el.text


def table_markdown(rows) -> str:
    """Rows of cells as a pipe table, or as HTML when a pipe table cannot say it."""
    rows = [list(r) for r in rows if r]
    if not rows:
        return ""
    spans = any(c.row_span > 1 or c.col_span > 1 for r in rows for c in r)
    width = max(len(r) for r in rows)
    if spans or not all(c.header for c in rows[0]) or len(rows) < 2:
        return _html_table(rows)
    def line(cells):
        cells = [_pipe_cell(c.text) for c in cells] + [""] * (width - len(cells))
        return "| " + " | ".join(cells) + " |"
    out = [line(rows[0]), "|" + "---|" * width]
    out += [line(r) for r in rows[1:]]
    return "\n".join(out)


def _pipe_cell(text: str) -> str:
    return " ".join(text.split()).replace("|", "\\|")


def _html_cell(text: str) -> str:
    return " ".join(text.split()).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _html_table(rows) -> str:
    covered: set[tuple[int, int]] = set()
    out = ["<table>"]
    for r, row in enumerate(rows):
        cells = []
        for c, cell in enumerate(row):
            if (r, c) in covered:
                continue
            for dr in range(cell.row_span):
                for dc in range(cell.col_span):
                    if dr or dc:
                        covered.add((r + dr, c + dc))
            tag = "th" if cell.header else "td"
            attrs = (f' rowspan="{cell.row_span}"' if cell.row_span > 1 else "") + \
                    (f' colspan="{cell.col_span}"' if cell.col_span > 1 else "")
            cells.append(f"<{tag}{attrs}>{_html_cell(cell.text)}</{tag}>")
        out.append("<tr>" + "".join(cells) + "</tr>")
    out.append("</table>")
    return "\n".join(out)


def render_elements(elements) -> str:
    """Elements of one page, joined by blank lines and stripped (no page grouping)."""
    return "\n\n".join(render_element(el) for el in elements).strip()


def to_markdown(doc: ir.Document) -> str:
    """The document as Markdown: pages in order, each page's elements in order."""
    parts = []
    for _, group in groupby(doc.elements, key=lambda el: el.page):
        text = render_elements(group)
        if text:
            parts.append(text)
    return "\n\n".join(parts)


def _blocks(markdown: str) -> list[str]:
    """Top-level blocks of `markdown`, split at blank lines outside code fences."""
    out: list[str] = []
    current: list[str] = []
    fence: str | None = None
    for chunk in _BLANK_RUN.split(markdown):
        current.append(chunk)
        for line in chunk.split("\n"):
            m = _FENCE.match(line)
            if m is None:
                continue
            marker = m.group(1)
            if fence is None:
                fence = marker
            elif marker[0] == fence[0] and len(marker) >= len(fence) and not line.strip()[len(marker):].strip():
                fence = None
        if fence is None:
            out.append("\n\n".join(current))
            current = []
    if current:  # an unclosed fence runs to the end
        out.append("\n\n".join(current))
    return [b for b in out if b.strip()]


def _block_type(block: str) -> tuple[str, int | None]:
    first = block.lstrip("\n").split("\n", 1)[0]
    lines = [ln for ln in block.split("\n") if ln.strip()]
    if _FENCE.match(first):
        return ir.CODE, None
    m = _HEADING.match(first)
    if m and len(lines) == 1:
        return ir.HEADING, len(m.group(1))
    if lines and all(ln.lstrip().startswith("|") for ln in lines):
        return ir.TABLE, None
    if _RULE.match(first) and len(lines) == 1:
        return ir.RULE, None
    if _LIST.match(first):
        return ir.LIST, None
    if _FIGURE.match(first) and len(lines) == 1:
        return ir.FIGURE, None
    return ir.PARAGRAPH, None


def split_markdown(markdown: str, source: str) -> list[ir.Element]:
    """An engine's Markdown as typed IR elements, one per top-level block.

    The element text is the block exactly as the engine wrote it. The type is read
    off its leading Markdown syntax (`signal="markdown-syntax"`), so it is only as
    good as the engine's own Markdown; it has no calibrated confidence.
    """
    elements = []
    for block in _blocks(markdown or ""):
        kind, level = _block_type(block)
        elements.append(ir.Element(type=kind, text=block, source=source, text_format=ir.MARKDOWN,
                                   level=level, signal=SYNTAX_SIGNAL))
    return elements
