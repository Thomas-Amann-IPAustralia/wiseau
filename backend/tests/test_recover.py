"""Tests for the structure-recovery passes (roadmap Tier 3, ADR-049..054).

The passes are pure functions of the IR, so these tests build small IRs by hand.
What each pass achieves on real pages is measured by the benchmark harness
(`benchmarks/bakeoff/recover_eval.py`: set S for development, set T held out)
and audited on whole documents (`docs/benchmarks/2026-11-11-structure-recovery.md`).
"""

from __future__ import annotations

import io
import json
import zipfile
from collections import Counter

import pymupdf
import pytest

from parsers import captions, continuation, cross_tables, document as ir, figures, footnotes, furniture
from parsers import headings, recover, serializer
from parsers.file_parser import file_to_bundle, file_to_markdown
from parsers.reading_order import Line

W, H = 595.0, 842.0


def _el(kind, lines, *, page=1, **kw):
    """An OCR element from [((x0, y0, x1, y1), text), ...]."""
    lines = [Line(*b, t) for b, t in lines]
    return ir.Element(type=kind, text="\n".join(ln.text for ln in lines), source="tesseract-psm3",
                      lines=tuple(lines), bbox=ir.BBox.around(lines), **kw)


def _md(kind, text, box, **kw):
    """A text-layer element (PyMuPDF4LLM Markdown) with a box."""
    return ir.Element(type=kind, text=text, source="pymupdf4llm", text_format=ir.MARKDOWN,
                      bbox=ir.BBox(*box), signal="markdown-syntax", **kw)


def _doc(*pages, method=ir.OCR):
    doc = ir.Document()
    for n, elements in enumerate(pages, 1):
        doc.add_page(ir.PageInfo(n, method, "x", W, H), elements)
    return doc


def _words(doc, **kw):
    return Counter(serializer.to_markdown(doc, **kw).split())


# --- the orchestrator -------------------------------------------------------------


def test_nothing_runs_unless_configured(monkeypatch):
    monkeypatch.delenv("WISEAU_STRUCTURE", raising=False)
    assert recover.configured() == ()
    monkeypatch.setenv("WISEAU_STRUCTURE", "furniture, headings")
    assert recover.configured() == ("headings", "furniture")  # run order, not the order named
    monkeypatch.setenv("WISEAU_STRUCTURE", "all")
    assert recover.configured() == recover.PASSES


def test_unknown_pass_is_an_error():
    with pytest.raises(ValueError):
        recover.recover(_doc([]), ["nope"])


def test_default_markdown_unchanged_by_the_new_serializer_paths(monkeypatch):
    monkeypatch.delenv("WISEAU_STRUCTURE", raising=False)
    pdf = pymupdf.open()
    page = pdf.new_page()
    page.insert_text((72, 100), "A born-digital paragraph.")
    md = file_to_markdown(pdf.tobytes(), "x.pdf")
    assert md.strip() == "A born-digital paragraph."


def test_document_round_trips_through_its_json():
    doc = _doc([_el(ir.HEADING, [((50, 100, 150, 110), "Diagnosis")], level=2, signal="pp-doclayoutv3")])
    doc.relations.append(ir.Relation("caption_of", "e1", "e1", "x"))
    again = ir.from_dict(json.loads(json.dumps(doc.to_dict())))
    assert again.to_dict() == doc.to_dict()


# --- 3.1 headings ------------------------------------------------------------------

BODY = [((50, 300 + 12 * k, 290, 309 + 12 * k), "some body text with descenders, yes") for k in range(6)]


def _heading(box, text):
    return _el(ir.HEADING, [(box, text)], level=2, signal="pp-doclayoutv3")


def test_larger_and_capital_headings_rank_higher():
    doc = _doc([_heading((50, 60, 120, 74), "ROSE"),          # caps, 14 pt box, no descender
                _heading((50, 80, 150, 89), "Diagnosis"),      # 9 pt with a descender
                _el(ir.TEXT, BODY),
                _heading((50, 400, 160, 409), "Origin")])
    headings.apply(doc)
    levels = {el.text: el.level for el in doc.elements if el.type == ir.HEADING}
    assert levels == {"ROSE": 1, "Diagnosis": 2, "Origin": 2}
    assert all(el.level_signal == headings.TYPOGRAPHY for el in doc.elements if el.type == ir.HEADING)
    md = serializer.to_markdown(doc)
    assert "# ROSE" in md.split("\n") and "## Diagnosis" in md.split("\n")


def test_one_style_refuses():
    doc = _doc([_heading((50, 80, 150, 89), "Diagnosis"), _el(ir.TEXT, BODY),
                _heading((50, 400, 160, 409), "Origin")])
    headings.apply(doc)
    assert all(el.level_signal is None and el.level == 2 for el in doc.elements if el.type == ir.HEADING)


def test_division_words_and_lettering():
    doc = _doc([_heading((50, 60, 200, 69), "Applications"),
                _heading((50, 80, 200, 89), "APPENDIX 1"),
                _heading((50, 100, 200, 109), "a) Descriptions finalised"),
                _el(ir.TEXT, BODY)])
    headings.apply(doc)
    lv = {el.text: el.level for el in doc.elements if el.type == ir.HEADING}
    assert lv["APPENDIX 1"] < lv["Applications"] < lv["a) Descriptions finalised"]


def test_outline_levels_win_and_text_is_not_edited():
    doc = _doc([_md(ir.PARAGRAPH, "**Claims**", (70, 80, 200, 92)),
                _md(ir.HEADING, "## Background", (70, 100, 200, 112), level=2)], method=ir.TEXT_LAYER)
    headings.apply(doc, outline=[[1, "Claims", 1], [2, "Background", 1], [1, "Missing", 1]])
    claims, background = doc.elements
    assert (claims.type, claims.level, claims.role_signal) == (ir.HEADING, 1, headings.OUTLINE)
    assert claims.text == "**Claims**" and background.level == 2
    assert serializer.render_element(claims) == "# **Claims**"  # its emphasis kept, as PyMuPDF4LLM writes it
    assert serializer.render_element(background) == "## Background"


# --- 3.2 furniture -----------------------------------------------------------------


def test_page_numbers_and_repeated_heads():
    pages = []
    for n in range(1, 5):
        pages.append([_md(ir.PARAGRAPH, "<u>PLANT JOURNAL 2003 VOL 16</u>", (420, 56, 575, 64)),
                      _md(ir.PARAGRAPH, f"Body text of page {n}.", (76, 100, 500, 700)),
                      _md(ir.PARAGRAPH, str(80 + n), (565, 821, 575, 830))])
    doc = _doc(*pages, method=ir.TEXT_LAYER)
    furniture.apply(doc)
    kinds = Counter((el.type, el.role_signal) for el in doc.elements)
    assert kinds[(ir.PAGE_HEADER, "furniture-repeat")] == 4
    assert kinds[(ir.PAGE_NUMBER, "furniture-number")] == 4
    assert kinds[(ir.PARAGRAPH, None)] == 4
    # Faithful by default; reader mode leaves it out.
    assert "PLANT" in serializer.to_markdown(doc)
    reader = serializer.to_markdown(doc, reader=True)
    assert "PLANT" not in reader and "81" not in reader and "Body text of page 1." in reader


def test_furniture_in_the_middle_of_a_page_is_text_again():
    el = _el(ir.PAGE_HEADER, [((240, 130, 400, 150), "ALKOXYLATED FATTY ALCOHOLS")], signal="pp-doclayoutv3")
    doc = _doc([el])
    furniture.apply(doc)
    assert doc.elements[0].type == ir.TEXT and doc.elements[0].role_signal == "furniture-outside"


def test_a_section_title_at_the_top_is_not_a_running_head():
    doc = _doc([_md(ir.PARAGRAPH, "**APPENDIX 6**", (70, 40, 200, 52))], method=ir.TEXT_LAYER)
    furniture.apply(doc)
    assert doc.elements[0].type == ir.PARAGRAPH


# --- 3.3 continuation ----------------------------------------------------------------


def test_column_break_is_joined_page_break_is_recorded():
    left = _el(ir.TEXT, [((50, 600, 290, 610), "The variety arose as a chance seedling of"),
                         ((50, 612, 290, 622), "a cross made in")])
    right = _el(ir.TEXT, [((310, 60, 550, 70), "propagation over four years formed the variety."),
                          ((310, 72, 550, 82), "It is compact.")])
    end = _el(ir.TEXT, [((310, 700, 550, 710), "Trials were run in Victoria and the")])
    nxt = _el(ir.TEXT, [((50, 70, 290, 80), "results are shown in the table below.")])
    doc = _doc([left, right, end], [nxt])
    continuation.apply(doc)
    rels = {(r.source, r.target, r.signal) for r in doc.relations}
    assert rels == {("e2", "e1", continuation.COLUMN), ("e4", "e3", continuation.PAGE)}
    page1 = serializer.to_markdown(doc).split("\n\n")
    assert page1[0] == left.text + "\n" + right.text  # one paragraph, text unchanged
    assert _words(doc) == Counter(" ".join(e.text for e in (left, right, end, nxt)).split())


def test_finished_sentences_capitals_and_labels_do_not_join():
    a = _el(ir.TEXT, [((50, 600, 290, 610), "This sentence is finished.")])
    b = _el(ir.TEXT, [((310, 60, 550, 70), "another one starts here in lower case")])
    c = _el(ir.TEXT, [((50, 700, 290, 710), "This one runs on and on with")])
    d = _el(ir.TEXT, [((310, 80, 550, 90), "Capitalised start of a new paragraph")])
    e = _el(ir.TEXT, [((50, 760, 70, 770), "30")])
    f = _el(ir.TEXT, [((310, 100, 550, 110), "vapors which combine with water")])
    doc = _doc([a, b], [c, d], [e, f])
    continuation.apply(doc)
    assert doc.relations == []


def test_a_heading_between_ends_the_paragraph():
    a = _el(ir.TEXT, [((50, 600, 290, 610), "runs on without a full stop and")])
    h = _heading((310, 50, 400, 58), "Origin")
    b = _el(ir.TEXT, [((310, 60, 550, 70), "lower case text after a heading here")])
    doc = _doc([a, h, b])
    continuation.apply(doc)
    assert doc.relations == []


# --- 3.4 captions and figures ------------------------------------------------------


def _table(box, header, rows=(), **kw):
    cells = (tuple(ir.Cell(t, header=True) for t in header),) + tuple(tuple(ir.Cell(t) for t in r) for r in rows)
    return ir.Element(type=ir.TABLE, text=ir.rows_text(cells), source="tatr", rows=cells, bbox=ir.BBox(*box),
                      signal="tatr", **kw)


def test_caption_links_to_the_table_below_and_figure_above():
    title = _heading((50, 100, 280, 110), "Table of Comparison of Rose Varieties")
    note = _el(ir.TEXT, [((50, 112, 280, 121), "(* = varieties used for comparison)")])
    table = _table((50, 130, 280, 400), ["", "A", "B"], [["X", "1", "2"]])
    fig = ir.Element(type=ir.FIGURE, text="", source="pp-doclayoutv3", bbox=ir.BBox(310, 100, 550, 300))
    credit = _el(ir.CAPTION, [((310, 305, 550, 315), "Flowers of 'Coco' (Photo supplied by applicant)")])
    stray = _el(ir.TEXT, [((50, 600, 280, 610), "Table 3 shows the results of the trial")])
    doc = _doc([title, note, table, fig, credit, stray])
    captions.apply(doc)
    rels = {(r.source, r.target) for r in doc.relations if r.type == "caption_of"}
    assert rels == {("e1", "e3"), ("e2", "e3"), ("e5", "e4")}
    assert doc.elements[0].type == ir.CAPTION and doc.elements[0].level is None  # no longer a heading
    assert doc.elements[5].type == ir.TEXT  # running text that only mentions a table


def test_bundle_has_markdown_ir_and_figures(monkeypatch):
    monkeypatch.setenv("WISEAU_STRUCTURE", "all")
    pdf = pymupdf.open()
    page = pdf.new_page()
    page.insert_text((72, 80), "A page with a picture.")
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 60, 60), False)
    pix.set_rect(pix.irect, (200, 30, 30))
    page.insert_image(pymupdf.Rect(72, 120, 272, 320), pixmap=pix)
    page.insert_text((72, 340), "Figure 1 A red square")
    data = pdf.tobytes()
    payload, summary = file_to_bundle(data, "x.pdf")
    assert summary["figures"] == 1
    z = zipfile.ZipFile(io.BytesIO(payload))
    assert z.namelist() == ["document.md", "document.json", "assets/figure-001.png"]
    md = z.read("document.md").decode()
    assert "](assets/figure-001.png)" in md and "base64" not in md
    tree = json.loads(z.read("document.json"))
    assert any(e["type"] == "figure" and e.get("asset") == "assets/figure-001.png" for e in tree["elements"])
    assert z.read("assets/figure-001.png").startswith(b"\x89PNG")
    assert file_to_bundle(data, "x.pdf")[0] == payload  # deterministic
    # The plain conversion carries no image link (ADR-024).
    assert "assets/" not in file_to_markdown(data, "x.pdf")


def test_text_layer_figures_are_inserted_in_reading_order():
    doc = _doc([_md(ir.PARAGRAPH, "Above", (72, 60, 300, 80)), _md(ir.PARAGRAPH, "Below", (72, 400, 300, 420))],
               method=ir.TEXT_LAYER)
    pdf = pymupdf.open()
    page = pdf.new_page(width=W, height=H)
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 20, 20), False)
    page.insert_image(pymupdf.Rect(72, 120, 272, 320), pixmap=pix)
    page.insert_image(pymupdf.Rect(72, 340, 80, 348), pixmap=pix)  # too small to be a figure
    figures.add_text_layer_figures(doc, pdf)
    assert [e.type for e in doc.elements] == [ir.PARAGRAPH, ir.FIGURE, ir.PARAGRAPH]
    assert [e.id for e in doc.elements] == ["e1", "e2", "e3"]


# --- 3.5 footnotes -----------------------------------------------------------------


def test_footnotes_split_linked_and_written_as_markdown_footnotes():
    body = _md(ir.PARAGRAPH, "Argentina <sup>2</sup>\nAustria <sup>2,4</sup>", (76, 130, 230, 700))
    notes = _md(ir.PARAGRAPH, "2 Bound by the 1978 Act.\n4 Member of the European\nCommunity.",
                (252, 85, 400, 224))
    doc = _doc([body, notes], method=ir.TEXT_LAYER)
    before = notes.text
    footnotes.apply(doc)
    parts = [e for e in doc.elements if e.type == ir.FOOTNOTE]
    assert [p.text for p in parts] == ["2 Bound by the 1978 Act.", "4 Member of the European\nCommunity."]
    assert "\n".join(p.text for p in parts) == before  # the block, split at lines, unchanged
    assert {(r.source, r.target) for r in doc.relations} == {(parts[0].id, "e1"), (parts[1].id, "e1")}
    md = serializer.to_markdown(doc)
    assert "Argentina [^2]" in md and "Austria [^2][^4]" in md
    assert "[^2]: Bound by the 1978 Act." in md and "[^4]: Member of the European" in md


def test_no_footnotes_on_scans_or_without_markers():
    scan = _doc([_el(ir.TEXT, [((50, 100, 300, 110), "Argentina2")]),
                 _el(ir.TEXT, [((50, 700, 300, 710), "2 Bound by the 1978 Act."),
                               ((50, 712, 300, 722), "3 Bound by the 1991 Act.")])])
    footnotes.apply(scan)
    plain = _doc([_md(ir.PARAGRAPH, "1 First item\n2 Second item", (70, 80, 300, 120))], method=ir.TEXT_LAYER)
    footnotes.apply(plain)
    assert scan.relations == [] and plain.relations == []
    assert all(e.type != ir.FOOTNOTE for e in scan.elements + plain.elements)


# --- 3.6 continued tables ----------------------------------------------------------


def test_panels_and_next_page_continuations():
    left = _table((50, 80, 285, 280), ["", "Gala", "* Matua"], [["AWN", "1", "2"]])
    right = _table((310, 90, 550, 282), ["", "Gala", "* Matua"], [["LEAF", "3", "4"]])
    other = _table((50, 500, 285, 700), ["", "Norwin", "Barwon", "Dooen"], [["HABIT", "a", "b", "c"]])
    page2 = _table((50, 60, 285, 300), ["", "Norwin", "*Barwon", "Dooen"], [["LEAFLET", "d", "e", "f"]])
    num = _el(ir.PAGE_NUMBER, [((540, 790, 550, 800), "17")])
    doc = _doc([left, right, other, num], [page2])
    cross_tables.apply(doc)
    rels = {(r.source, r.target, r.signal) for r in doc.relations}
    assert rels == {("e2", "e1", cross_tables.PANEL), ("e5", "e3", cross_tables.PAGE)}
    # Faithful: every row as read. Reader mode: the repeated header row once.
    assert serializer.to_markdown(doc).count("Gala") == 2
    assert serializer.to_markdown(doc, reader=True).count("Gala") == 1
    assert serializer.to_markdown(doc, reader=True).count("Norwin") == 1


def test_side_by_side_tables_of_different_things_do_not_link():
    a = _table((50, 80, 285, 280), ["(* = comparators)", "", ""], [["", "Sheer Bliss", "Pristine"]])
    b = _table((310, 80, 550, 280), ["(* = comparators)", "", ""], [["", "Aotearoa", "Queen"]])
    c = _table((50, 400, 285, 600), ["", "Class Act", "Iceberg"], [["X", "1", "2"]])
    d = _table((310, 400, 550, 600), ["", "Brigadoon", "Pristine"], [["Y", "3", "4"]])
    doc = _doc([a, b, c, d])
    cross_tables.apply(doc)
    assert doc.relations == []
