"""Tests for column-aware reading order of OCR'd lines (ADR-035).

Pure geometry: the lines are built by hand, so these need no Tesseract.
`test_ocr.py` covers the same behaviour end to end through real OCR.
"""

from __future__ import annotations

from parsers.reading_order import Line, order_lines

LH = 12.0  # line height
STEP = 14.0  # line pitch


def _column(x0: float, x1: float, words: list[str], top: float = 100.0) -> list[Line]:
    return [Line(x0, top + i * STEP, x1, top + i * STEP + LH, w) for i, w in enumerate(words)]


LEFT = [f"left {i}" for i in range(8)]
RIGHT = [f"right {i}" for i in range(8)]


def _interleaved(*columns: list[Line]) -> list[Line]:
    """Lines in MuPDF's OCR order: top to bottom, straight across the columns."""
    return sorted((ln for col in columns for ln in col), key=lambda l: (l.y0, l.x0))


def _text_lines(text: str) -> list[str]:
    return [t for t in text.splitlines() if t]


def test_two_columns_are_read_one_after_the_other():
    lines = _interleaved(_column(70, 280, LEFT), _column(310, 520, RIGHT))
    assert _text_lines(order_lines(lines)) == LEFT + RIGHT


def test_full_width_heading_splits_the_page_into_bands():
    top_left, top_right = _column(70, 280, LEFT[:4]), _column(310, 520, RIGHT[:4])
    heading = [Line(70, 160, 520, 172, "FULL WIDTH HEADING")]
    low_left = _column(70, 280, LEFT[4:], top=180)
    low_right = _column(310, 520, RIGHT[4:], top=180)
    lines = _interleaved(top_left, top_right, heading, low_left, low_right)
    assert _text_lines(order_lines(lines)) == (
        LEFT[:4] + RIGHT[:4] + ["FULL WIDTH HEADING"] + LEFT[4:] + RIGHT[4:]
    )


def test_a_line_fragmented_by_ocr_is_rejoined_on_its_row():
    # OCR often returns one printed line as several fragments.
    left = _column(70, 280, LEFT)
    left[2:3] = [Line(70, left[2].y0, 160, left[2].y1, "left"), Line(165, left[2].y0, 280, left[2].y1, "2")]
    lines = _interleaved(left, _column(310, 520, RIGHT))
    assert _text_lines(order_lines(lines)) == LEFT + RIGHT


def test_three_columns():
    third = [f"third {i}" for i in range(8)]
    lines = _interleaved(_column(40, 200, LEFT), _column(220, 380, RIGHT), _column(400, 560, third))
    assert _text_lines(order_lines(lines)) == LEFT + RIGHT + third


def test_single_column_is_left_alone():
    assert order_lines(_column(70, 520, LEFT)) is None


def test_table_is_left_alone():
    # Short cells with wide gaps between them: rows are mostly empty space, so
    # no gutter qualifies and the table keeps MuPDF's row-wise reading.
    lines = []
    for i in range(10):
        y = 100 + i * STEP
        lines += [Line(70, y, 120, y + LH, f"name{i}"), Line(230, y, 260, y + LH, f"{i}.1"),
                  Line(330, y, 360, y + LH, f"{i}.2"), Line(450, y, 480, y + LH, f"{i}.3")]
    assert order_lines(lines) is None


def test_narrow_side_column_is_not_a_text_column():
    # A margin of page numbers is under a quarter of the text width.
    margin = _column(500, 520, [str(i) for i in range(8)])
    assert order_lines(_interleaved(_column(70, 450, LEFT), margin)) is None


def test_too_few_lines_is_left_alone():
    assert order_lines(_interleaved(_column(70, 280, LEFT[:2]), _column(310, 520, RIGHT[:2]))) is None


def test_paragraph_gap_starts_a_new_paragraph():
    left = _column(70, 280, LEFT[:4]) + _column(70, 280, LEFT[4:], top=100 + 4 * STEP + 20)
    out = order_lines(_interleaved(left, _column(310, 520, RIGHT)))
    assert "left 3\n\nleft 4" in out


def test_output_does_not_depend_on_input_order():
    lines = _interleaved(_column(70, 280, LEFT), _column(310, 520, RIGHT))
    assert order_lines(lines) == order_lines(list(reversed(lines)))


def test_an_over_tall_line_box_does_not_chain_rows():
    # ADR-037: one OCR box two printed lines tall (26 pt) sat between two
    # neighbours and merged all three into one output row.
    left = [
        Line(70, 400, 280, 414, "outer edges corresponding to RHS 55D; petal"),
        Line(70, 408, 280, 434, "margins having fine serrations; no thick central"),
        Line(70, 425, 280, 434, "cluster of petals; an epicalyx with 2 pairs of"),
    ] + _column(70, 280, LEFT, top=440)
    out = order_lines(_interleaved(left, _column(310, 520, RIGHT, top=400)))
    lines = _text_lines(out)
    assert lines[:3] == [
        "outer edges corresponding to RHS 55D; petal",
        "margins having fine serrations; no thick central",
        "cluster of petals; an epicalyx with 2 pairs of",
    ]


def test_legacy_row_rendering_is_still_available(monkeypatch):
    from parsers import reading_order
    monkeypatch.setattr(reading_order, "ROWS_BY_CENTRE", False)
    lines = _interleaved(_column(70, 280, LEFT), _column(310, 520, RIGHT))
    assert _text_lines(order_lines(lines)) == LEFT + RIGHT
