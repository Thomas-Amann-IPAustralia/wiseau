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
