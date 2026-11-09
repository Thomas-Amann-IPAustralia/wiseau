"""Tests for the Table Transformer pass (ADR-046) and the table dialect (ADR-045).

The model is not run here: `regions_from` takes TATR's structure as data, so
these tests feed it synthetic rows and columns and check what the pass does with
Tesseract's words. The model run itself is measured by the benchmark harness.
"""

from __future__ import annotations

from collections import Counter

import pytest

from parsers import document as ir
from parsers import ocr, serializer, tables
from parsers.reading_order import Line
from parsers.tesseract_layout import Word


def _words(rows, x0=100, dx=60, y0=100, dy=12, block=1):
    """A grid of words: ``rows`` is a list of lists of cell texts; one psm 3 line per row."""
    out = []
    for r, cells in enumerate(rows):
        for c, text in enumerate(cells):
            for k, part in enumerate(text.split()):
                x = x0 + c * dx + k * 18
                out.append(Word(x, y0 + r * dy, x + 15, y0 + r * dy + 8, part, (block, 1, r + 1)))
    return out


def _layout(words):
    """One psm 3 text element per block, its lines built from the words."""
    by_key = {}
    for w in words:
        by_key.setdefault(w.key, []).append(w)
    lines = [tables._line_of(ws) for ws in by_key.values()]
    return [ir.Element(type=ir.TEXT, text="\n".join(ln.text for ln in lines), source=ocr.PSM3,
                       lines=tuple(lines), bbox=ir.BBox.around(lines))]


def _structure(n_rows, n_cols, x0=95, dx=60, y0=97, dy=12, header=True):
    rows = [(x0, y0 + r * dy, x0 + n_cols * dx, y0 + (r + 1) * dy) for r in range(n_rows)]
    cols = [(x0 + c * dx, y0, x0 + (c + 1) * dx, y0 + n_rows * dy) for c in range(n_cols)]
    found = {"table row": rows, "table column": cols}
    if header:
        found["table column header"] = [rows[0]]
    return found


def _detection(found):
    xs = [b[0] for b in found["table column"]] + [b[2] for b in found["table column"]]
    ys = [b[1] for b in found["table row"]] + [b[3] for b in found["table row"]]
    return tables.Detection(0.99, (min(xs), min(ys), max(xs), max(ys)), found)


GRID = [["", "Abacus", "Muir"], ["mean", "7.74", "4.09"], ["range", "3-10", "2-10"]]


def test_words_fill_the_cells_they_sit_in():
    words = _words(GRID)
    (region,) = tables.regions_from([_detection(_structure(3, 3))], words, _layout(words)[0].lines)
    assert region.accepted
    assert [[c.text for c in row] for row in region.rows] == [["", "Abacus", "Muir"],
                                                              ["mean", "7.74", "4.09"],
                                                              ["range", "3-10", "2-10"]]
    assert all(c.header for c in region.rows[0]) and not any(c.header for c in region.rows[1])


def test_the_pass_partitions_the_words_exactly():
    # Two prose lines above the table, in the same psm 3 block: nothing is lost
    # or duplicated, and the prose stays above the table.
    prose = _words([["Some prose here"], ["more of it"]], y0=40, block=1)
    grid = _words(GRID, block=1)
    grid = [w._replace(key=(1, 1, w.key[2] + 10)) for w in grid]
    words = prose + grid
    layout = _layout(words)
    regions = tables.regions_from([_detection(_structure(3, 3))], words, layout[0].lines)
    out = tables.apply(layout, regions, words)
    assert [e.type for e in out] == ["text", "table"]
    rendered = serializer.render_elements(out)
    assert Counter(rendered.replace("|", " ").replace("-", " ").split()) >= Counter("Some prose here more of it".split())
    flat = Counter(t for e in out for t in (e.text.split()))
    assert flat == Counter(w.text for w in words)


def test_a_line_straddling_two_tables_is_split_between_them():
    # psm 3 reads one line straight across two tables printed side by side.
    left = _words([["", "A"], ["mean", "1"], ["range", "2"]], x0=100)
    right = _words([["", "B"], ["mean", "3"], ["range", "4"]], x0=300)
    words = left + right   # same keys: each printed line is one psm 3 line across both
    layout = _layout(words)
    dets = [_detection(_structure(3, 2, x0=95)), _detection(_structure(3, 2, x0=295))]
    out = tables.apply(layout, tables.regions_from(dets, words, layout[0].lines), words)
    assert [e.type for e in out] == ["table", "table"]
    assert [[c.text for c in r] for r in out[1].rows] == [["", "B"], ["mean", "3"], ["range", "4"]]


def test_prose_boxed_as_a_table_is_refused():
    long = "this is a long line of running prose text"
    words = _words([[long, long], [long, long], [long, long]], dx=200)
    layout = _layout(words)
    (region,) = tables.regions_from([_detection(_structure(3, 2, dx=200))], words, layout[0].lines)
    assert not region.accepted and region.features["words_per_cell"] > tables.MAX_WORDS_PER_CELL
    assert tables.apply(layout, [region], words) == layout


def test_stacked_records_are_split_into_rows():
    # One TATR row around "mean / range" and two values each: two records.
    words = []
    for k, (label, a, b) in enumerate([("mean", "7.74", "4.09"), ("range", "3-10", "2-10")]):
        for c, text in enumerate((label, a, b)):
            x = 100 + c * 60
            words.append(Word(x, 112 + 10 * k, x + 15, 120 + 10 * k, text, (1, 1, 2 + k)))
    found = {"table row": [(95, 97, 275, 109), (95, 109, 275, 135)],
             "table column": [(95 + c * 60, 97, 155 + c * 60, 135) for c in range(3)]}
    head = [Word(160, 100, 175, 108, "Abacus", (1, 1, 1)), Word(220, 100, 235, 108, "Muir", (1, 1, 1))]
    words = head + words
    (region,) = tables.regions_from([_detection(found)], words, _layout(words)[0].lines)
    assert [[c.text for c in r] for r in region.rows] == [["", "Abacus", "Muir"], ["mean", "7.74", "4.09"],
                                                          ["range", "3-10", "2-10"]]


def test_off_by_default(monkeypatch):
    monkeypatch.delenv("WISEAU_TABLES", raising=False)
    assert not tables.enabled()


# --- the dialect (ADR-045) ------------------------------------------------------


def _table(rows, signal):
    rows = tuple(tuple(c if isinstance(c, ir.Cell) else ir.Cell(c) for c in r) for r in rows)
    return ir.Element(type=ir.TABLE, text=ir.rows_text(rows), source="t", rows=rows, signal=signal)


def test_a_tatr_table_with_a_header_is_a_pipe_table():
    head = (ir.Cell("", header=True), ir.Cell("Abacus", header=True))
    md = serializer.render_element(_table([head, ("mean", "7.74 mm")], "tatr"))
    assert md == "|  | Abacus |\n|---|---|\n| mean | 7.74 mm |"


def test_without_a_header_row_it_is_html():
    md = serializer.render_element(_table([("a", "1"), ("b", "2")], "tatr"))
    assert md == "<table>\n<tr><td>a</td><td>1</td></tr>\n<tr><td>b</td><td>2</td></tr>\n</table>"


def test_pipes_and_angle_brackets_are_escaped():
    head = (ir.Cell("x|y", header=True), ir.Cell("<b>", header=True))
    assert serializer.render_element(_table([head, ("1", "2")], "tatr")).startswith("| x\\|y | <b> |")
    assert "&lt;b&gt;" in serializer.render_element(_table([("<b>", "1")], "tatr"))


@pytest.mark.parametrize("stitched", [False, True])
def test_stitched_tables_stay_plain_unless_asked(monkeypatch, stitched):
    monkeypatch.setattr(serializer, "STITCHED_AS_TABLES", stitched)
    el = _table([("a", "1"), ("b", "2")], serializer.STITCH_SIGNAL)
    assert serializer.render_element(el).startswith("<table>") == stitched
    if not stitched:
        assert serializer.render_element(el) == "a 1\nb 2"
