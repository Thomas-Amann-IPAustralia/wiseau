"""Tests for the document IR and its Markdown serializer (ADR-043).

The serializer's gate is behaviour neutrality: for every path, the IR serialized
and normalized must equal what that path returned before the IR existed. The
whole-corpus check is in `docs/benchmarks/2026-11-09-ir-neutrality.md`; these
tests pin the rules it relies on.
"""

from __future__ import annotations

import json
import random

import pymupdf
import pytest

from parsers import document as ir
from parsers import file_parser, serializer
from parsers.cleaner import clean_markdown
from parsers.reading_order import Line

# --- split_markdown: blocks, types, and the round trip -----------------------

MARKDOWN = """# Plant Breeder's Rights

Some **bold** prose that runs
over two lines.

## Section 2   \n\n\n\n- one
- two

|a|b|
|---|---|
|1|2|

```
code


with blank lines
```

![](img.png)

-----

1. first
2. second
"""


def _round_trip(md: str) -> str:
    return serializer.render_elements(serializer.split_markdown(md, "test"))


def test_split_types_blocks_by_their_markdown_syntax():
    els = serializer.split_markdown(MARKDOWN, "pymupdf4llm")
    assert [(e.type, e.level) for e in els] == [
        ("heading", 1), ("paragraph", None), ("heading", 2), ("list", None), ("table", None),
        ("code", None), ("figure", None), ("rule", None), ("list", None)]
    assert all(e.source == "pymupdf4llm" and e.text_format == ir.MARKDOWN for e in els)
    assert all(e.signal == serializer.SYNTAX_SIGNAL and e.confidence is None for e in els)


def test_a_code_fence_is_never_split_at_its_blank_lines():
    code = [e for e in serializer.split_markdown(MARKDOWN, "t") if e.type == ir.CODE]
    assert code[0].text == "```\ncode\n\nwith blank lines\n```"


def test_an_unclosed_fence_runs_to_the_end():
    els = serializer.split_markdown("text\n\n```\nopen\n\nstill code", "t")
    assert [e.type for e in els] == ["paragraph", "code"]


def test_split_then_join_is_neutral_after_the_normalizer():
    assert clean_markdown(_round_trip(MARKDOWN)) == clean_markdown(MARKDOWN)


def test_round_trip_is_neutral_on_random_whitespace():
    # The property the native path relies on: re-joining blocks with one blank
    # line changes nothing that survives clean_markdown(), whatever the spacing.
    rng = random.Random(20261109)
    pieces = ["a", "# h", "- x", "|c|", "```", "  indented", "text  ", "", " ", "\t", "~~~"]
    for _ in range(2000):
        md = "".join(rng.choice(pieces) + rng.choice(["\n", "\n\n", "\n \n", "\n\n\n", " "])
                     for _ in range(rng.randint(1, 12)))
        if not md.strip():
            # The one difference: a whitespace-only document normalizes to "\n"
            # as a string but to "" as blocks. Every caller reads both as "no
            # text" (`main.py` tests `markdown.strip()`).
            assert not clean_markdown(_round_trip(md)).strip()
            continue
        assert clean_markdown(_round_trip(md)) == clean_markdown(md), repr(md)


# --- serializer.to_markdown: page grouping ----------------------------------


def _el(text, **kw):
    return ir.Element(type=ir.TEXT, text=text, source="t", **kw)


def test_pages_are_stripped_joined_and_blank_pages_skipped():
    doc = ir.Document()
    doc.add_page(ir.PageInfo(1, ir.OCR, "t"), [_el("one\n"), _el("two")])
    doc.add_page(ir.PageInfo(2, ir.OCR, "t"), [])
    doc.add_page(ir.PageInfo(3, ir.OCR, "t"), [_el("  \n")])
    doc.add_page(ir.PageInfo(4, ir.OCR, "t"), [_el("\nthree")])
    assert serializer.to_markdown(doc) == "one\n\n\ntwo\n\nthree"


def test_a_table_renders_row_by_row():
    rows = ((ir.Cell("Name"), ir.Cell("No.")), (ir.Cell("Rose"), ir.Cell("12")))
    table = ir.Element(type=ir.TABLE, text=ir.rows_text(rows), source="t", rows=rows)
    assert serializer.render_element(table) == "Name No.\nRose 12"


# --- the model --------------------------------------------------------------


def test_ids_and_pages_are_stamped_in_reading_order():
    doc = ir.Document()
    doc.add_page(ir.PageInfo(1, ir.OCR, "t", 612, 792), [_el("a"), _el("b")])
    doc.extend([_el("c")])
    assert [(e.id, e.page) for e in doc.elements] == [("e1", 1), ("e2", 1), ("e3", None)]


def test_unknown_types_are_rejected():
    with pytest.raises(ValueError):
        ir.Element(type="banner", text="x", source="t")
    with pytest.raises(ValueError):
        ir.Relation("sits_near", "e1", "e2")


def test_json_is_deterministic_and_rounds_coordinates():
    line = Line(10.123456, 20.0, 110.98765, 30.5, "hello")
    doc = ir.Document()
    doc.add_page(ir.PageInfo(1, ir.OCR, "tesseract", 612.0, 792.0),
                 [ir.Element(type=ir.TEXT, text="hello", source="tesseract-psm3",
                             lines=(line,), bbox=ir.BBox.around([line]))])
    first = json.dumps(doc.to_dict())
    assert first == json.dumps(doc.to_dict())
    data = doc.to_dict()
    assert data["schema"] == ir.SCHEMA_VERSION
    assert data["elements"][0]["bbox"] == [10.12, 20.0, 110.99, 30.5]
    assert data["elements"][0]["lines"] == [{"bbox": [10.12, 20.0, 110.99, 30.5], "text": "hello"}]
    assert data["pages"] == [{"number": 1, "method": "ocr", "source": "tesseract", "size": [612.0, 792.0]}]


# --- file_parser: every path goes through the IR ----------------------------


def _pdf(*texts: str) -> bytes:
    doc = pymupdf.open()
    for t in texts:
        doc.new_page().insert_text((72, 72), t)
    out = doc.tobytes()
    doc.close()
    return out


def test_a_born_digital_pdf_becomes_text_layer_pages():
    doc = file_parser.file_to_document(_pdf("Hello from page one", "And page two here"), "x.pdf")
    assert [(p.number, p.method, p.source) for p in doc.pages] == [
        (1, "text-layer", "pymupdf4llm"), (2, "text-layer", "pymupdf4llm")]
    assert {e.page for e in doc.elements} == {1, 2}
    assert all(e.source == "pymupdf4llm" for e in doc.elements)


def test_file_to_markdown_is_the_serialized_ir():
    data = _pdf("Hello from page one", "And page two here")
    doc = file_parser.file_to_document(data, "x.pdf")
    assert file_parser.file_to_markdown(data, "x.pdf") == clean_markdown(serializer.to_markdown(doc))


def test_the_fast_path_matches_one_pymupdf4llm_call():
    # The old fast path returned pymupdf4llm.to_markdown(doc) as one string.
    import pymupdf4llm

    data = _pdf("# not a heading\n\nfirst page text", "second page", "third")
    old = pymupdf4llm.to_markdown(pymupdf.open(stream=data, filetype="pdf"))
    assert clean_markdown(file_parser.pdf_to_markdown(data)) == clean_markdown(old)


class _FakeOcr:
    name = "fake"

    def ocr_page(self, page, *, dpi, language):
        return f"local OCR of page {page.number}"


def test_a_mixed_pdf_records_how_each_page_was_read(monkeypatch):
    monkeypatch.setattr(file_parser, "_engine", lambda: _FakeOcr())
    native = _pdf("A real text layer on this page")
    blank = pymupdf.open()
    blank.new_page()
    data = pymupdf.open(stream=native, filetype="pdf")
    data.insert_pdf(blank)
    doc = file_parser.file_to_document(data.tobytes(), "x.pdf")
    assert [(p.number, p.method, p.source) for p in doc.pages] == [(1, "text-layer", "pymupdf4llm"), (2, "ocr", "fake")]
    assert doc.elements[-1].text == "local OCR of page 1" and doc.elements[-1].page == 2


def test_remote_markdown_is_split_into_unpaged_blocks(monkeypatch):
    from parsers import docling_client

    monkeypatch.setattr(docling_client, "is_configured", lambda: True)
    monkeypatch.setattr(docling_client, "convert_document", lambda data, name: "# Title\n\nBody text")
    doc = file_parser.file_to_document(_pdf("x" * 40), "x.pdf", engine="docling")
    assert [(e.type, e.page, e.source) for e in doc.elements] == [("heading", None, "docling"),
                                                                  ("paragraph", None, "docling")]
