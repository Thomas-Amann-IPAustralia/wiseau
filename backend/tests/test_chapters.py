"""Tests for chapter detection and splitting (ADR-030).

The splitter is a pile of heuristics, so these tests are mostly *documents*: a
fixture per shape a real PDF arrives in — a contents page with dot leaders, one
rendered as a table, one that lost its "Contents" heading, chapter openings that
kept no markup — plus the cases where splitting would be wrong and must not
happen (an article, a back-of-book index, headings inside a code fence).

Two invariants are pinned here and should stay pinned: the chapters *partition*
the document, and the same input always yields the same split.
"""

from __future__ import annotations

import re

import pytest

from parsers.chapters import split_into_chapters

# Long enough that a section reads as a chapter rather than a stray line, and
# varied enough that fuzzy title matching cannot pass by accident.
BODY = (
    "It began, as these things do, with a misplaced decimal point in a spreadsheet.\n"
    "The auditors arrived on a Tuesday and stayed until the following spring.\n"
    "Nobody in the office admitted to having touched the ledger that quarter.\n"
)


def _squash(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _titles(split) -> list[str]:
    return [chapter.title for chapter in split.chapters]


# --- The contents page ------------------------------------------------------
DOT_LEADER_BOOK = f"""# The Ledger of Small Mistakes

A novel in three parts.

## Contents

Chapter 1: The Arrival ................ 3
Chapter 2: The Ledger ................. 45
Chapter 3: The Reckoning .............. 88
Afterword ............................. 120

## Chapter 1: The Arrival

{BODY}

## Chapter 2: The Ledger

{BODY}

## Chapter 3: The Reckoning

{BODY}

## Afterword

{BODY}
"""


def test_a_contents_page_with_dot_leaders_drives_the_split():
    split = split_into_chapters(DOT_LEADER_BOOK)

    assert split.method == "toc"
    assert _titles(split) == [
        "The Ledger of Small Mistakes",
        "Chapter 1: The Arrival",
        "Chapter 2: The Ledger",
        "Chapter 3: The Reckoning",
        "Afterword",
    ]


def test_the_contents_page_stays_with_the_front_matter():
    # The reader who splits a book still wants its index; it belongs to the
    # material before chapter one, not inside it.
    front = split_into_chapters(DOT_LEADER_BOOK).chapters[0]
    assert front.level == 0
    assert "Contents" in front.markdown
    assert "The Arrival" not in front.markdown.split("## Contents")[0]


def test_chapters_partition_the_document():
    split = split_into_chapters(DOT_LEADER_BOOK)
    rejoined = "\n".join(chapter.markdown for chapter in split.chapters)
    # Nothing dropped, nothing duplicated — only whitespace is renormalized.
    assert _squash(rejoined) == _squash(DOT_LEADER_BOOK)


def test_the_split_is_deterministic():
    first = split_into_chapters(DOT_LEADER_BOOK)
    second = split_into_chapters(DOT_LEADER_BOOK)
    assert first == second


def test_sub_entries_do_not_become_chapters():
    # A contents page lists sub-sections too; splitting on those would return
    # fragments where the reader asked for chapters.
    document = f"""# Annual Report

## Table of Contents

1. Introduction .......... 3
   1.1 Scope .......... 4
   1.2 Method .......... 6
2. Findings .......... 11
   2.1 Revenue .......... 12
3. Recommendations .......... 30

## 1. Introduction

{BODY}

### 1.1 Scope

{BODY}

### 1.2 Method

{BODY}

## 2. Findings

{BODY}

### 2.1 Revenue

{BODY}

## 3. Recommendations

{BODY}
"""
    split = split_into_chapters(document)

    assert split.method == "toc"
    assert _titles(split)[1:] == ["1. Introduction", "2. Findings", "3. Recommendations"]
    assert "1.1 Scope" in split.chapters[1].markdown


def test_a_contents_page_rendered_as_a_table_is_understood():
    document = f"""# Operations Handbook

## Table of Contents

| Section | Page |
| --- | --- |
| Safety first | 3 |
| Daily checks | 18 |
| Shutdown procedure | 40 |

## Safety first

{BODY}

## Daily checks

{BODY}

## Shutdown procedure

{BODY}
"""
    split = split_into_chapters(document)

    assert split.method == "toc"
    assert _titles(split) == [
        "Operations Handbook",
        "Safety first",
        "Daily checks",
        "Shutdown procedure",
    ]


def test_a_contents_page_of_links_is_understood():
    document = f"""# Policy Manual

## Contents

- [Purpose](#purpose)
- [Eligibility](#eligibility)
- [Appeals](#appeals)

## Purpose

{BODY}

## Eligibility

{BODY}

## Appeals

{BODY}
"""
    split = split_into_chapters(document)

    assert split.method == "toc"
    assert _titles(split) == ["Policy Manual", "Purpose", "Eligibility", "Appeals"]


def test_a_contents_page_that_lost_its_heading_is_still_found():
    # Extraction drops the word "Contents" often enough (it is set as art, or in
    # a running header) that the run of page numbers has to be the signal.
    document = f"""ANNUAL REVIEW

Foreword ................. 2
Operations ............... 11
Finance .................. 26
Outlook .................. 40

Foreword

{BODY}

Operations

{BODY}

Finance

{BODY}

Outlook

{BODY}
"""
    split = split_into_chapters(document)

    assert split.method == "toc"
    assert _titles(split)[-4:] == ["Foreword", "Operations", "Finance", "Outlook"]


def test_chapter_openings_that_kept_no_markup_are_matched_by_title():
    # A single-font PDF: the contents page knows the titles, the body does not
    # mark them up at all. Matching the text is the only way through.
    document = f"""THE ART OF THE DEAL

Contents

Opening moves .......... 5
The middle game ........ 40
Endgame ................ 88

**Opening moves**

{BODY}

**The middle game**

{BODY}

**Endgame**

{BODY}
"""
    split = split_into_chapters(document)

    assert split.method == "toc"
    assert _titles(split)[-3:] == ["Opening moves", "The middle game", "Endgame"]


def test_a_body_heading_may_carry_a_subtitle_the_contents_page_omits():
    document = f"""# Casebook

## Contents

Opening moves .......... 5
The middle game ........ 40
Endgame ................ 88

## Opening moves: the first ten

{BODY}

## The middle game - trading down

{BODY}

## Endgame

{BODY}
"""
    split = split_into_chapters(document)

    assert split.method == "toc"
    assert len(split.chapters) == 4
    assert "trading down" in split.chapters[2].markdown


def test_titles_sharing_a_prefix_are_not_confused():
    document = f"""# Guide

## Contents

Introduction .......... 1
Introduction to Widgets .......... 4
Advanced Widgets .......... 20

## Introduction

{BODY}

## Introduction to Widgets

{BODY}

## Advanced Widgets

{BODY}
"""
    split = split_into_chapters(document)

    assert _titles(split)[1:] == ["Introduction", "Introduction to Widgets", "Advanced Widgets"]
    assert "Advanced" not in split.chapters[1].markdown


def test_a_back_of_book_index_does_not_drive_the_split():
    # An index names pages, not sections that follow it in order, so its entries
    # never match forward — the coverage gate is what rejects it.
    document = f"""# Field Guide

## Introduction

{BODY}

## Method

{BODY}

## Index

alpha particles .......... 14
beta decay ............... 22
chromatography ........... 9
diffraction .............. 31
"""
    split = split_into_chapters(document)

    assert split.method == "headings"
    assert _titles(split) == ["Introduction", "Method", "Index"]


# --- The fallbacks ----------------------------------------------------------
def test_headings_split_the_document_when_there_is_no_contents_page():
    document = f"""# Handbook

## Getting started

{BODY}

## Configuration

{BODY}

## Troubleshooting

{BODY}
"""
    split = split_into_chapters(document)

    assert split.method == "headings"
    assert _titles(split) == ["Getting started", "Configuration", "Troubleshooting"]


def test_a_lone_title_heading_opens_the_first_chapter_rather_than_its_own_file():
    split = split_into_chapters(
        f"# Handbook\n\n## First\n\n{BODY}\n\n## Second\n\n{BODY}\n"
    )
    assert len(split.chapters) == 2
    assert split.chapters[0].markdown.startswith("# Handbook")


def test_the_chapter_level_wins_over_the_shallower_title_level():
    # H1s here are "Part" dividers with nothing under them but chapters; the
    # chapters are the H2s, and that is where a reader expects the cut.
    document = f"""# Volume One

## Chapter 1

{BODY}

## Chapter 2

{BODY}

## Chapter 3

{BODY}
"""
    split = split_into_chapters(document)

    assert split.method == "headings"
    assert _titles(split) == ["Chapter 1", "Chapter 2", "Chapter 3"]


def test_plain_text_chapter_markers_are_the_last_resort():
    document = f"""THE LONG WALK

CHAPTER ONE

{BODY}

CHAPTER TWO

{BODY}

CHAPTER THREE

{BODY}
"""
    split = split_into_chapters(document)

    assert split.method == "markers"
    assert _titles(split) == ["CHAPTER ONE", "CHAPTER TWO", "CHAPTER THREE"]


# --- When not to split ------------------------------------------------------
def test_an_article_is_left_whole():
    split = split_into_chapters(f"# An Article\n\n{BODY * 4}")

    assert split.method == "none"
    assert split.chapters == ()
    assert not split


def test_empty_input_is_left_whole():
    assert split_into_chapters("").method == "none"
    assert split_into_chapters("   \n\n").method == "none"


def test_headings_inside_a_code_fence_are_not_chapters():
    document = f"""# Readme

{BODY}

```markdown
## Chapter 1
## Chapter 2
## Chapter 3
```

{BODY}
"""
    assert split_into_chapters(document).method == "none"


def test_a_document_of_fragments_is_left_whole():
    # Twenty one-line sections are a glossary, not twenty chapters.
    document = "# Glossary\n\n" + "\n".join(f"## Term {n}\n\nA short definition.\n" for n in range(20))
    assert split_into_chapters(document).method == "none"


def test_a_single_section_is_not_a_split():
    assert split_into_chapters(f"# Only\n\n## One section\n\n{BODY * 3}").method == "none"


# --- Filenames --------------------------------------------------------------
def test_filenames_are_numbered_in_reading_order():
    names = [chapter.filename for chapter in split_into_chapters(DOT_LEADER_BOOK).chapters]

    assert names[0].startswith("00-")
    assert [name[:2] for name in names] == ["00", "01", "02", "03", "04"]
    assert all(name.endswith(".md") for name in names)


def test_filenames_are_slugs_of_the_titles():
    names = [chapter.filename for chapter in split_into_chapters(DOT_LEADER_BOOK).chapters]
    assert names[1] == "01-chapter-1-the-arrival.md"


def test_filenames_stay_sortable_past_ninety_nine_chapters():
    document = "# Big Book\n\n" + "\n".join(
        f"## Chapter {n}\n\n{BODY}\n" for n in range(1, 121)
    )
    names = [chapter.filename for chapter in split_into_chapters(document).chapters]

    assert len(names) == 120
    # Zero-padded to the width of the largest number, so a file manager's
    # alphabetical order is still reading order.
    assert names[0].startswith("001-")
    assert names == sorted(names)


@pytest.mark.parametrize(
    "title, expected",
    [
        ("Chapter 1: The Arrival", "chapter-1-the-arrival"),
        ("  Spaces   everywhere  ", "spaces-everywhere"),
        ("Punctuation!?*/\\<>|", "punctuation"),
        ("!!!", "section"),
        ("Grüße aus Köln", "grüße-aus-köln"),
    ],
)
def test_titles_become_filesystem_safe_stems(title, expected):
    from parsers.chapters import _slug

    assert _slug(title) == expected


def test_every_chapter_is_normalized_markdown():
    # Invariant #3: nothing leaves a parser without going through the cleaner.
    for chapter in split_into_chapters(DOT_LEADER_BOOK).chapters:
        assert chapter.markdown.endswith("\n")
        assert not chapter.markdown.startswith("\n")
        assert "\n\n\n" not in chapter.markdown
        assert chapter.length == len(chapter.markdown)
