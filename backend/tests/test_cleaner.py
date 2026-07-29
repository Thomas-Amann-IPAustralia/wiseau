"""Tests for the deterministic Markdown normalizer.

``clean_markdown`` is the shared exit point for every parser, so its behaviour
is the load-bearing determinism guarantee of the whole engine. These tests lock
in that contract: identical input must always yield byte-identical output.
"""

from __future__ import annotations

import pytest

from parsers.cleaner import clean_markdown


def test_empty_input_returns_empty_string():
    assert clean_markdown("") == ""


def test_output_ends_with_single_trailing_newline():
    assert clean_markdown("hello") == "hello\n"
    assert clean_markdown("hello\n\n\n") == "hello\n"


def test_smart_quotes_are_flattened_to_ascii():
    result = clean_markdown("“Hi” said ‘Mark’")
    assert result == '"Hi" said \'Mark\'\n'


def test_dashes_and_ellipsis_are_normalized():
    result = clean_markdown("a–b—c…")
    assert result == "a-b--c...\n"


def test_nbsp_zero_width_and_bom_are_handled():
    # nbsp -> regular space; zero-width space and BOM -> removed entirely.
    source = "a b​c﻿d"
    assert clean_markdown(source) == "a bcd\n"


def test_runs_of_blank_lines_collapse_to_one():
    result = clean_markdown("a\n\n\n\n\nb")
    assert result == "a\n\nb\n"


def test_trailing_whitespace_is_stripped_per_line():
    result = clean_markdown("a   \nb\t\t\nc")
    assert result == "a\nb\nc\n"


def test_carriage_returns_are_normalized_to_lf():
    assert clean_markdown("a\r\nb\rc") == "a\nb\nc\n"


def test_is_deterministic_across_repeated_runs():
    messy = "“Quote”\r\n\n\n\ntrailing   \n﻿bom​"
    first = clean_markdown(messy)
    assert all(clean_markdown(messy) == first for _ in range(5))


def test_is_idempotent():
    messy = "“Quote”\r\n\n\n\ntrailing   \n"
    once = clean_markdown(messy)
    assert clean_markdown(once) == once


@pytest.mark.parametrize("value", [None, 0, False])
def test_falsy_non_string_inputs_return_empty(value):
    # The guard clause treats any falsy input as empty rather than crashing.
    assert clean_markdown(value) == ""


# --- Inlined base64 payloads (ADR-024) --------------------------------------
#
# Extractors that inline images turn one screenshot into tens of thousands of
# unreadable characters. The normalizer keeps the *fact* of the image — and its
# media type — and drops only the payload.

_BLOB = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAAB" * 400


def test_an_inlined_image_keeps_its_alt_text_and_loses_its_payload():
    source = f"# Report\n\n![Figure 1](data:image/png;base64,{_BLOB})\n\nBody text."
    result = clean_markdown(source)

    assert "![Figure 1](data:image/png;base64,...)" in result
    assert "iVBORw0" not in result
    # Everything around the payload survives untouched.
    assert "# Report" in result
    assert "Body text." in result


def test_the_document_shrinks_to_the_size_of_its_text():
    source = f"![](data:image/png;base64,{_BLOB})\n\nOne short paragraph."
    assert len(clean_markdown(source)) < 100 < len(source)


def test_a_payload_in_an_html_attribute_is_stripped_too():
    # Markdownify passes unknown HTML through, so a data URI can arrive inside a
    # tag rather than in Markdown image syntax.
    source = f'<img src="data:image/jpeg;base64,{_BLOB}" alt="Scan">'
    result = clean_markdown(source)

    assert result == '<img src="data:image/jpeg;base64,..." alt="Scan">\n'


def test_a_bare_payload_in_running_text_is_stripped():
    result = clean_markdown(f"See data:application/pdf;base64,{_BLOB} for the source.")
    assert result == "See data:application/pdf;base64,... for the source.\n"


def test_several_payloads_are_all_stripped():
    source = "\n\n".join(f"![img {i}](data:image/png;base64,{_BLOB})" for i in range(4))
    result = clean_markdown(source)

    assert result.count("base64,...") == 4
    assert "iVBORw0" not in result


def test_a_readable_data_uri_is_left_alone():
    # Only base64 payloads are machine noise; a plain-text data URI is readable
    # and the normalizer has no business rewriting it.
    source = "[note](data:text/plain,hello%20mark)"
    assert clean_markdown(source) == source + "\n"


def test_an_ordinary_image_link_is_untouched():
    source = "![Chart](https://example.com/chart.png)"
    assert clean_markdown(source) == source + "\n"


def test_payload_stripping_is_deterministic_and_idempotent():
    source = f"![a](data:image/png;base64,{_BLOB})\n\n![b](data:image/gif;base64,{_BLOB})"
    once = clean_markdown(source)
    assert all(clean_markdown(source) == once for _ in range(5))
    assert clean_markdown(once) == once
