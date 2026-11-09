"""Tests for boxes on text-layer blocks (ADR-044)."""

from __future__ import annotations

import pymupdf

from parsers import document as ir
from parsers import file_parser, serializer, text_layer


def _word(x0, y0, text, block=0, line=0, n=0):
    return (x0, y0, x0 + 10 * len(text), y0 + 10, text, block, line, n)


def _els(*texts):
    return [ir.Element(type=ir.PARAGRAPH, text=t, source="pymupdf4llm", text_format=ir.MARKDOWN) for t in texts]


def test_each_block_gets_the_box_of_its_words():
    words = [_word(10, 10, "Title", 0, 0), _word(10, 40, "Body", 1, 0), _word(60, 40, "text.", 1, 0, 1)]
    title, body = text_layer.align(_els("# **Title**", "Body text."), words)
    assert (title.bbox.x0, title.bbox.y0, title.bbox.x1, title.bbox.y1) == (10, 10, 60, 20)
    assert (body.bbox.x0, body.bbox.x1) == (10, 110)
    assert body.lines[0].text == "Body text." and body.bbox_coverage == 1.0
    assert body.bbox_signal == text_layer.SIGNAL


def test_crossed_blocks_both_align():
    # The Markdown puts the running header first; the page lists it last.
    words = [_word(10, 100, w, 1, 0, i) for i, w in enumerate("one two three four five".split())]
    words += [_word(10, 10, w, 2, 0, i) for i, w in enumerate("Plant Varieties Journal".split())]
    header, body = text_layer.align(_els("<u>Plant Varieties Journal</u>", "one two three four five"), words)
    assert header.bbox_coverage == 1.0 and header.bbox.y0 == 10
    assert body.bbox_coverage == 1.0 and body.bbox.y0 == 100


def test_link_targets_and_tags_are_not_counted():
    words = [_word(10, 10, "Part", 0, 0, 0), _word(60, 10, "2", 0, 0, 1)]
    (el,) = text_layer.align(_els("<u>[Part 2](http://example.com/part2.html)</u>"), words)
    assert el.bbox_coverage == 1.0


def test_a_block_that_does_not_align_gets_no_box():
    # Refuse rather than guess (ADR-030): coverage is reported, the box is not.
    words = [_word(10, 10, "alpha", 0, 0, 0), _word(10, 30, "beta", 1, 0, 0)]
    (el,) = text_layer.align(_els("gamma delta epsilon alpha"), words)
    assert el.bbox is None and el.lines == ()
    assert el.bbox_coverage == 0.25


def test_a_lone_word_is_placed_only_when_unambiguous():
    words = [_word(10, 10, "6", 0, 0, 0), _word(10, 30, "Example", 1, 0, 0), _word(80, 30, "6", 1, 0, 1)]
    # "Example 6" takes the second "6"; the page number takes the one left.
    page_no, example = text_layer.align(_els("6", "Example 6"), words)
    assert example.bbox_coverage == 1.0 and page_no.bbox.y0 == 10
    # Out of order and with two free candidates, the lone word stays unplaced.
    words = [_word(10, 10, "6", 0, 0, 0), _word(10, 20, "6", 1, 0, 0),
             _word(10, 30, "Example", 2, 0, 0), _word(80, 30, "6", 2, 0, 1)]
    example, page_no = text_layer.align(_els("Example 6", "6"), words)
    assert example.bbox_coverage == 1.0 and page_no.bbox is None and page_no.bbox_coverage == 0.0


def test_alignment_is_deterministic():
    words = [_word(10 * i, 10, w, 0, 0, i) for i, w in enumerate("a b c a b c d".split())]
    first = text_layer.align(_els("a b", "c a b c d"), words)
    assert first == text_layer.align(_els("a b", "c a b c d"), words)


def test_text_layer_pages_carry_boxes_and_the_markdown_is_unchanged(monkeypatch):
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 72), "Hello from a real text layer")
    page.insert_text((72, 300), "A second paragraph lower down")
    data = doc.tobytes()
    with_boxes = file_parser.file_to_document(data, "x.pdf")
    assert all(e.bbox is not None and e.bbox_signal == text_layer.SIGNAL for e in with_boxes.elements)
    monkeypatch.setattr(file_parser, "TEXT_LAYER_BOXES", False)
    without = file_parser.file_to_document(data, "x.pdf")
    assert all(e.bbox is None for e in without.elements)
    assert serializer.to_markdown(with_boxes) == serializer.to_markdown(without)
    assert with_boxes.to_dict()["elements"][0]["bbox_signal"] == text_layer.SIGNAL
