"""Tests for the layout-region pass (ADR-048).

The detector is not run here: `layout.apply` takes regions as data, so these
tests feed it synthetic regions and check what the pass does with the page's
OCR lines. The model run itself is measured by the benchmark harness
(`benchmarks/bakeoff/layout_regions.py`, sets S and T).
"""

from __future__ import annotations

from collections import Counter

import pymupdf
import pytest

from parsers import document as ir
from parsers import layout, ocr, serializer
from parsers.reading_order import Line


def _el(lines, source=ocr.PSM3, **kw):
    lines = [Line(*b, t) for b, t in lines]
    return ir.Element(type=kw.pop("type", ir.TEXT), text="\n".join(ln.text for ln in lines), source=source,
                      lines=tuple(lines), bbox=ir.BBox.around(lines), **kw)


def _region(box, category, score=0.9):
    return layout.Region(box, category, score)


def _words(elements):
    return Counter(w for el in elements for w in serializer.render_element(el).replace("#", " ").split())


# One psm 3 block that ran a heading into its paragraph, and a second paragraph.
HEADING = ((50, 100, 150, 110), "Diagnosis")
BODY_1 = ((50, 112, 300, 122), "This variety is distinct from all other")
BODY_2 = ((50, 124, 300, 134), "varieties in having the following.")
OTHER = ((50, 200, 300, 210), "A second paragraph.")


def test_regions_split_a_merged_block_and_type_the_heading():
    elements = [_el([HEADING, BODY_1, BODY_2]), _el([OTHER])]
    regions = [_region((45, 98, 160, 111), "paragraph_title"),
               _region((45, 111, 305, 136), "text"),
               _region((45, 198, 305, 212), "text")]
    out = layout.apply(elements, regions)
    assert [el.type for el in out] == [ir.HEADING, ir.TEXT, ir.TEXT]
    assert out[0].text == "Diagnosis" and out[0].level == 2 and out[0].signal == layout.SIGNAL
    assert out[1].text == "This variety is distinct from all other\nvarieties in having the following."
    assert serializer.render_element(out[0]) == "## Diagnosis"
    assert _words(out) == _words(elements)


def test_every_line_kept_once_with_its_text_unchanged():
    elements = [_el([HEADING, BODY_1, BODY_2]), _el([OTHER])]
    regions = [_region((45, 98, 160, 111), "paragraph_title"), _region((45, 111, 305, 136), "text")]
    out = layout.apply(elements, regions)
    before = [ln for el in elements for ln in el.lines]
    after = [ln for el in out for ln in el.lines]
    assert Counter(before) == Counter(after)
    # The line no region holds stays in its own element, unchanged.
    assert out[-1] is elements[1]


def test_line_outside_regions_stays_after_the_region_that_took_its_neighbour():
    elements = [_el([HEADING, BODY_1, BODY_2])]
    regions = [_region((45, 98, 160, 111), "paragraph_title")]  # only the heading is boxed
    out = layout.apply(elements, regions)
    assert [el.text for el in out] == ["Diagnosis", "This variety is distinct from all other\n"
                                       "varieties in having the following."]
    assert out[1].signal is None and out[1].type == ir.TEXT


def test_a_line_needs_half_its_area_inside_a_region():
    elements = [_el([HEADING])]  # x 50-150
    assert layout.apply(elements, [_region((101, 98, 300, 111), "paragraph_title")])[0] is elements[0]
    assert layout.apply(elements, [_region((100, 98, 300, 111), "paragraph_title")])[0].type == ir.HEADING


def test_ties_go_to_the_smaller_region():
    elements = [_el([BODY_1])]
    regions = [_region((0, 0, 600, 800), "text"), _region((45, 111, 305, 123), "footer")]
    assert layout.apply(elements, regions)[0].type == ir.PAGE_FOOTER


def test_tables_move_whole_and_keep_their_rows():
    rows = ((ir.Cell("mean"), ir.Cell("7.74")), (ir.Cell("range"), ir.Cell("3-10")))
    for signal in ("tatr", "tesseract-row-stitch"):
        table = _el([((50, 300, 150, 310), "mean 7.74"), ((50, 312, 150, 322), "range 3-10")],
                    type=ir.TABLE, rows=rows, signal=signal)
        # A layout model calling the table's two columns separate text regions.
        regions = [_region((45, 98, 160, 111), "paragraph_title"),
                   _region((45, 298, 100, 324), "text"), _region((100, 298, 155, 324), "text")]
        out = layout.apply([_el([HEADING]), table], regions)
        assert out[0].type == ir.HEADING and out[-1] is table and len(out) == 2, signal


def test_categories_map_to_types():
    cases = {"doc_title": (ir.TITLE, 1), "paragraph_title": (ir.HEADING, 2), "figure_title": (ir.CAPTION, None),
             "vision_footnote": (ir.CAPTION, None), "header": (ir.PAGE_HEADER, None),
             "footer": (ir.PAGE_FOOTER, None), "number": (ir.PAGE_NUMBER, None),
             "footnote": (ir.FOOTNOTE, None), "text": (ir.TEXT, None), "image": (ir.FIGURE, None),
             "chart": (ir.FIGURE, None), "aside_text": (ir.TEXT, None)}
    for category, (kind, level) in cases.items():
        out = layout.apply([_el([HEADING])], [_region((45, 98, 160, 111), category)])
        assert (out[0].type, out[0].level) == (kind, level), category


def test_lines_read_out_of_a_picture_keep_their_text():
    # Typed as a figure, but the words stay and the Markdown is the text, as before.
    out = layout.apply([_el([HEADING])], [_region((45, 98, 160, 111), "image")])
    assert out[0].type == ir.FIGURE and out[0].text == HEADING[1]
    assert serializer.render_element(out[0]) == HEADING[1]


def test_a_picture_with_no_lines_is_an_empty_figure_in_reading_order():
    out = layout.apply([_el([HEADING, BODY_1])],
                       [_region((45, 98, 160, 111), "paragraph_title"), _region((40, 300, 300, 500), "image"),
                        _region((45, 113, 400, 125), "text")])
    assert [e.type for e in out] == [ir.HEADING, ir.FIGURE, ir.TEXT]
    assert out[1].text == "" and out[1].bbox == ir.BBox(40, 300, 300, 500)
    # It writes nothing, so the page's Markdown is unchanged.
    assert serializer.render_elements(out) == serializer.render_elements([out[0], out[2]])


def test_title_renders_as_level_one_on_one_line():
    el = layout.apply([_el([((50, 50, 300, 60), "PART 1 — ITEMS OF"), ((50, 62, 300, 72), "GENERAL INTEREST")])],
                      [_region((45, 48, 305, 74), "doc_title")])[0]
    assert serializer.render_element(el) == "# PART 1 — ITEMS OF GENERAL INTEREST"


def test_no_regions_changes_nothing():
    elements = [_el([HEADING, BODY_1])]
    assert layout.apply(elements, []) == elements


def test_headings_from_other_sources_render_as_before():
    el = ir.Element(type=ir.HEADING, text="# **TABLE 2**", source="pymupdf4llm", level=1,
                    text_format=ir.MARKDOWN, signal="markdown-syntax")
    assert serializer.render_element(el) == "# **TABLE 2**"


def test_disabled_without_the_variable(monkeypatch):
    monkeypatch.delenv("WISEAU_LAYOUT", raising=False)
    assert not layout.enabled()
    monkeypatch.setenv("WISEAU_LAYOUT", "nope")
    assert not layout.enabled()


def test_pass_failure_keeps_the_page(monkeypatch):
    """A failing detector degrades to the page without the pass, never an error."""
    monkeypatch.setattr(layout, "enabled", lambda: True)
    monkeypatch.setattr(layout, "_load", lambda: {})   # no real model (the engine loads up front)

    def boom(page, elements, regions=None):
        raise RuntimeError("no model")

    monkeypatch.setattr(layout, "read_layout", boom)
    expected = [_el([HEADING])]
    monkeypatch.setattr(ocr.TesseractEngine, "_read_elements", lambda self, page, **kw: expected)
    doc = pymupdf.open()
    page = doc.new_page()
    assert ocr.TesseractEngine().read_elements(page, dpi=300, language="eng") == expected


def test_pass_runs_on_the_ocr_path_when_enabled(monkeypatch):
    monkeypatch.setattr(layout, "enabled", lambda: True)
    monkeypatch.setattr(layout, "_load", lambda: {})   # no real model (the engine loads up front)
    monkeypatch.setattr(ocr.TesseractEngine, "_read_elements",
                        lambda self, page, **kw: [_el([HEADING, BODY_1, BODY_2])])
    # The engine renders the page itself and runs detection beside OCR (ADR-056).
    monkeypatch.setattr(layout, "detect_rendered", lambda rendered: [_region((45, 98, 160, 111), "paragraph_title"),
                                                                     _region((45, 111, 305, 136), "text")])
    doc = pymupdf.open()
    out = ocr.TesseractEngine().read_elements(doc.new_page(), dpi=300, language="eng")
    assert [el.type for el in out] == [ir.HEADING, ir.TEXT]


def test_models_load_on_the_calling_thread_before_detection(monkeypatch):
    """ADR-056: the engine loads the enabled models itself, before any detection runs
    beside OCR, so no inference overlaps a load (a concurrent load left a model with
    `meta` tensors on 2 CPUs)."""
    import threading

    events = []
    monkeypatch.setattr(layout, "enabled", lambda: True)
    monkeypatch.setattr(layout, "_load", lambda: events.append(("load", threading.current_thread().name)))
    monkeypatch.setattr(layout, "detect_rendered", lambda rendered: events.append(("detect", None)) or [])
    monkeypatch.setattr(ocr.TesseractEngine, "_read_elements", lambda self, page, **kw: [_el([HEADING])])
    doc = pymupdf.open()
    ocr.TesseractEngine().read_elements(doc.new_page(), dpi=300, language="eng")
    assert events[0] == ("load", threading.current_thread().name)
    assert ("detect", None) in events[1:]


def test_model_loads_hold_the_shared_lock(monkeypatch):
    """Both passes' loaders take `model_lock.LOAD_LOCK`, so two loads never overlap (ADR-056)."""
    pytest.importorskip("torch")
    transformers = pytest.importorskip("transformers")
    from parsers import model_lock, tables

    held = []

    def record(*args, **kwargs):
        held.append(model_lock.LOAD_LOCK.locked())
        raise RuntimeError("stop before any download")

    monkeypatch.setattr(transformers.AutoModelForObjectDetection, "from_pretrained", record)
    monkeypatch.setattr(transformers.TableTransformerForObjectDetection, "from_pretrained", record)
    monkeypatch.setattr(tables, "_config_5x", lambda name, revision: None)
    monkeypatch.setattr(layout, "_model", {})    # nothing loaded yet in this process
    monkeypatch.setattr(tables, "_models", {})
    for load in (layout._load, tables._load):
        with pytest.raises(RuntimeError):
            load()
    assert held == [True, True]
    assert not model_lock.LOAD_LOCK.locked()
