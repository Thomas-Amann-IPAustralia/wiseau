"""Tests for Tesseract's own page layout with table rows stitched back (ADR-038).

Most tests build Tesseract-shaped blocks by hand, so they need no binary. The
end-to-end test at the bottom OCRs a rendered table and is skipped without
Tesseract.
"""

from __future__ import annotations

import io
import shutil
import subprocess

import pymupdf
import pytest

from parsers import ocr, tesseract_layout
from parsers.reading_order import Line
from parsers.tesseract_layout import Block, keeps_words, parse_tsv, plausible_word_count, render

HEADER = "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext\n"


def _block(*lines: tuple[float, float, float, float, str]) -> Block:
    ls = [Line(*ln) for ln in lines]
    return Block(tuple(ls), min(l.x0 for l in ls), min(l.y0 for l in ls),
                 max(l.x1 for l in ls), max(l.y1 for l in ls))


def _column(x0: float, x1: float, texts: list[str], top: float = 100, step: float = 20) -> Block:
    return _block(*[(x0, top + i * step, x1, top + i * step + 12, t) for i, t in enumerate(texts)])


# A two-column list as psm 3 segments it: names, a label column, a number column.
NAMES = _column(300, 380, ["'Empress'", "'Regal'", "'Saxon'"])
LABELS = _column(420, 480, ["Application No.", "Application No.", "Application No."])
NUMBERS = _column(490, 530, ["90/083", "91/022", "91/023"])


def test_parse_tsv_groups_words_into_lines_and_blocks():
    tsv = HEADER + "".join([
        "5\t1\t1\t1\t1\t1\t10\t20\t30\t10\t95\tHello\n",
        "5\t1\t1\t1\t1\t2\t45\t20\t30\t10\t95\tworld\n",
        "5\t1\t1\t1\t2\t1\t10\t40\t30\t10\t95\tagain\n",
        "4\t1\t2\t1\t1\t0\t300\t20\t40\t10\t-1\t\n",      # a line row, not a word
        "5\t1\t2\t1\t1\t1\t300\t20\t40\t10\t95\t \n",      # an empty word is skipped
        "5\t1\t3\t1\t1\t1\t300\t20\t40\t10\t95\t42\n",
    ])
    blocks = parse_tsv(tsv, scale=0.5)
    assert [[ln.text for ln in b.lines] for b in blocks] == [["Hello world", "again"], ["42"]]
    assert blocks[0].lines[0] == Line(5.0, 10.0, 37.5, 15.0, "Hello world")


def test_side_by_side_cells_are_read_row_by_row():
    text = render([NAMES, LABELS, NUMBERS])
    assert text.splitlines() == [
        "'Empress' Application No. 90/083",
        "'Regal' Application No. 91/022",
        "'Saxon' Application No. 91/023",
    ]


def test_prose_columns_keep_their_column_order():
    left = _column(100, 500, ["the first column runs on with long lines of words"] * 4)
    right = _column(560, 960, ["and the second column does the same with its own words"] * 4)
    assert render([left, right]).split("\n\n") == [
        "\n".join(ln.text for ln in left.lines), "\n".join(ln.text for ln in right.lines)]


def test_distant_blocks_are_not_stitched():
    """A short heading in the left column is not a cell of a list in the right one."""
    heading = _column(100, 160, ["ROSE", "Rosa"], top=120)
    names = _column(700, 780, ["'Empress'", "'Regal'", "'Saxon'"])
    numbers = _column(800, 840, ["90/083", "91/022", "91/023"])
    wide = _column(100, 640, ["Applicant: a long line of prose that sets the text width"])
    assert render([heading, names, numbers, wide]).split("\n\n")[0] == "ROSE\nRosa"


def test_lines_of_different_heights_do_not_make_a_row():
    big = _block((300, 100, 400, 140, "HEADING"))
    small = _block((420, 110, 480, 122, "note"))
    wide = _column(100, 900, ["a long line of prose that sets the text width to the page"])
    assert render([big, small, wide]).split("\n\n")[:2] == ["HEADING", "note"]


def test_a_stitched_group_takes_the_place_of_its_first_block():
    before = _column(100, 900, ["Table of Comparison of things that sets the width"], top=40)
    after = _column(100, 900, ["Text that follows the table and is long enough"], top=400)
    parts = render([before, NAMES, LABELS, NUMBERS, after]).split("\n\n")
    assert parts[0].startswith("Table") and parts[1].startswith("'Empress'") and parts[2].startswith("Text")
    assert len(parts) == 3


def test_output_does_not_depend_on_the_order_cells_were_found():
    assert render([NAMES, LABELS, NUMBERS]) == render([NAMES, NUMBERS, LABELS])


def test_empty_page_renders_nothing():
    assert render([]) == ""


def test_guard_rejects_a_layout_that_lost_words():
    guard = "Agapanthus 2/89 Alstroemeria 10/89 Anthurium 4/90 Asparagus 2/91"
    assert keeps_words(guard, guard)
    assert not keeps_words("Agapanthus Alstroemeria Anthurium Asparagus", guard)


def test_plausible_words_leave_out_speckle():
    # Printed words, short words, numbers and codes count...
    assert plausible_word_count("Address of Applicant: 12 mm, V2 4CB 1985") == 8
    # ...speckle read as letters does not (ADR-042).
    assert plausible_word_count("ee eee Ss NaS oO t snes ee nsn bago") == 2  # snes, bago


def test_guard_ignores_junk_when_counting():
    # P85 p5 (ADR-042): the guard read more junk out of a stamp box than the layout
    # did, so a raw count made the worse text win.
    layout = "Address of Applicant: New Murphy Road and Concord Pike"
    guard = "Address of Applicant: Concord Pike ee eee ee Ss oO eee ee nn"
    assert keeps_words(layout, guard)
    assert not keeps_words(layout, guard, plausible=False)  # the rule before ADR-042
    assert not keeps_words("Address", guard + " New Murphy Road")


def test_missing_binary_returns_none(monkeypatch):
    monkeypatch.setattr(tesseract_layout, "TESSERACT_CMD", "no-such-tesseract-binary")
    doc = pymupdf.open()
    doc.new_page()
    assert tesseract_layout.read_page(doc[0], dpi=72, language="eng") is None


# --- The engine's choice between the two layouts ----------------------------


@pytest.fixture
def page():
    doc = pymupdf.open()
    return doc.new_page()


@pytest.fixture
def geometric(monkeypatch):
    monkeypatch.setattr(ocr.TesseractEngine, "_geometric_elements",
                        staticmethod(lambda page, **kw: ocr.text_elements("one two three four five six seven",
                                                                          source="test")))


def _layout_blocks(text):
    """psm 3 blocks reading ``text``, one block per paragraph (or None: Tesseract failed)."""
    if text is None:
        return lambda *a, **kw: None
    blocks = [Block((Line(0, 10 * i, 100, 10 * i + 8, t),), 0, 10 * i, 100, 10 * i + 8)
              for i, t in enumerate(text.split("\n"))]
    return lambda *a, **kw: tesseract_layout.Layout(blocks, [])


def test_layout_off_returns_the_geometric_text(monkeypatch, page, geometric):
    monkeypatch.setattr(ocr, "TESSERACT_LAYOUT", False)
    monkeypatch.setattr(tesseract_layout, "read_page_layout", lambda *a, **kw: pytest.fail("layout ran"))
    assert ocr.TesseractEngine().ocr_page(page, dpi=72, language="eng") == "one two three four five six seven"


def test_layout_on_returns_the_layout_text(monkeypatch, page, geometric):
    monkeypatch.setattr(ocr, "TESSERACT_LAYOUT", True)
    monkeypatch.setattr(tesseract_layout, "read_page_layout", _layout_blocks("one two three\nfour five six seven"))
    assert ocr.TesseractEngine().ocr_page(page, dpi=72, language="eng") == "one two three\n\nfour five six seven"


@pytest.mark.parametrize("layout_text", [None, "one two"])
def test_layout_failure_or_lost_words_falls_back(monkeypatch, page, geometric, layout_text):
    monkeypatch.setattr(ocr, "TESSERACT_LAYOUT", True)
    monkeypatch.setattr(tesseract_layout, "read_page_layout", _layout_blocks(layout_text))
    assert ocr.TesseractEngine().ocr_page(page, dpi=72, language="eng") == "one two three four five six seven"


def test_the_engine_returns_ir_elements_with_provenance(monkeypatch, page, geometric):
    # ADR-043: the chosen reading arrives as IR elements carrying their source.
    monkeypatch.setattr(ocr, "TESSERACT_LAYOUT", True)
    monkeypatch.setattr(tesseract_layout, "read_page_layout", _layout_blocks("one two three\nfour five six seven"))
    elements = ocr.TesseractEngine().read_elements(page, dpi=72, language="eng")
    assert [(e.type, e.source, e.text) for e in elements] == [
        ("text", "tesseract-psm3", "one two three"), ("text", "tesseract-psm3", "four five six seven")]
    assert elements[0].bbox is not None and elements[0].lines[0].text == "one two three"


def test_tesseract_runs_on_one_thread(monkeypatch):
    # ADR-042: an OpenMP Tesseract under load ran past TIMEOUT_S, so the output
    # depended on the machine's load. Each run is held to one thread.
    seen = {}

    def fake_run(cmd, **kw):
        seen.update(kw["env"])
        raise subprocess.TimeoutExpired(cmd, 1)

    monkeypatch.setattr(tesseract_layout.subprocess, "run", fake_run)
    doc = pymupdf.open()
    doc.new_page(width=100, height=100)
    assert tesseract_layout.read_page(doc[0], dpi=72, language="eng") is None
    assert seen["OMP_THREAD_LIMIT"] == "1"


# --- End to end, with the real binary ---------------------------------------


@pytest.mark.skipif(shutil.which("tesseract") is None or pymupdf.get_tessdata() is None,
                    reason="tesseract binary / tessdata not installed")
def test_a_rendered_list_reads_across_each_row():
    """Smoke test of the real binary path: TSV in, rows out.

    Tesseract happens to keep this synthetic list's rows whole, so this passes with
    stitching switched off too. Stitching itself is covered by the synthetic-block
    tests above, and was measured on real scans in the bake-off (ADR-038).
    """
    Image = pytest.importorskip("PIL.Image")
    ImageDraw = pytest.importorskip("PIL.ImageDraw")
    ImageFont = pytest.importorskip("PIL.ImageFont")
    try:
        font = ImageFont.truetype("DejaVuSans.ttf", 34)
    except OSError:
        pytest.skip("DejaVuSans not available")
    rows = [("Agapanthus", "Application", "90/083"), ("Alstroemeria", "Application", "91/022"),
            ("Anthurium", "Application", "91/023"), ("Asparagus", "Application", "91/039"),
            ("Carnation", "Application", "91/041")]
    # The gap rule is relative to the text width, so the page carries a full-width
    # line, as a real page does; without it a 200 px gap is a quarter of the "page".
    img = Image.new("RGB", (2000, 220 + 70 * len(rows)), "white")
    draw = ImageDraw.Draw(img)
    draw.text((80, 50), "Table of Comparison of Several Varieties as Received in the Year",
              fill="black", font=font)
    for i, (a, b, c) in enumerate(rows):
        y = 160 + 70 * i
        draw.text((80, y), a, fill="black", font=font)
        draw.text((500, y), b, fill="black", font=font)
        draw.text((780, y), c, fill="black", font=font)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    doc = pymupdf.open()
    pg = doc.new_page(width=img.width * 72 / 200, height=img.height * 72 / 200)
    pg.insert_image(pg.rect, stream=buf.getvalue())
    text = tesseract_layout.read_page(pg, dpi=200, language="eng")
    assert text is not None
    lines = [ln for ln in text.splitlines() if ln.strip()][1:]
    assert lines[0].split() == ["Agapanthus", "Application", "90/083"]
    assert lines[-1].split() == ["Carnation", "Application", "91/041"]
