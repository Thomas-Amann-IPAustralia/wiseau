"""Boxes for text-layer blocks, by aligning their words with the page's (ADR-044).

PyMuPDF4LLM writes good Markdown for a born-digital page but says nothing about
where each block sat, so the IR's text-layer elements had no `bbox` (ADR-043).
The page itself knows where every word is (`page.get_text("words")`). This module
puts the two together:

1. Cut both sides into the same tokens: runs of letters and digits, lowercased.
   Markdown syntax (`**`, `#`, `|`, escapes), link targets, HTML tags and
   punctuation drop out, so the tokens of a block are just its printed words.
2. Align the block tokens, in the Markdown's order, with the page's word tokens,
   in the page's order, with `difflib.SequenceMatcher` (longest common runs,
   order-preserving, no heuristics about layout). Blocks the Markdown puts in a
   different order from the page cannot all align in one pass, so up to
   `MAX_ROUNDS` passes align the tokens left over, keeping runs of two or more.
   A one-word element left over is placed only on the single unclaimed page
   token that can be it, never on one of several.
3. Each element gets the union box of the page words its tokens aligned to, and
   the page's lines those words sit on (with their boxes), as `lines`.

**It says how sure it is, and refuses rather than guesses (ADR-030).**
`bbox_coverage` is the share of the element's tokens that aligned; below
`MIN_COVERAGE` the element keeps `bbox = None` (and no lines), because a box
built from a few stray matches would point at the wrong part of the page.
`bbox_signal` names this method, so a box from alignment is never mistaken for
one an engine reported.

Determinism: PyMuPDF's word list is fixed for a fixed page, and SequenceMatcher
is deterministic (``autojunk=False``, so no frequency-based pruning either). The
Markdown is never touched: this only adds fields beside it.
"""

from __future__ import annotations

import re
from dataclasses import replace
from difflib import SequenceMatcher
from typing import Sequence

import pymupdf

from . import document as ir
from .reading_order import Line

# The `bbox_signal` on a box found by this alignment.
SIGNAL = "text-layer-alignment"
# An element whose aligned share of tokens is below this keeps no box.
MIN_COVERAGE = 0.5
# Alignment passes, at most. PyMuPDF4LLM sometimes emits blocks in a different
# order from the page's word list, and one order-preserving pass cannot match two
# crossed blocks; each further pass aligns what is left over, and passes stop as
# soon as one aligns nothing. A page with many crossed blocks needs several.
MAX_ROUNDS = 32

_TOKEN = re.compile(r"[^\W_]+")
# Markdown that is not printed on the page: a link's or image's target, and HTML
# tags such as PyMuPDF4LLM's <u> for underlined text.
_LINK_TARGET = re.compile(r"\]\([^)]*\)")
_HTML_TAG = re.compile(r"</?[A-Za-z][^>]*>")


def _tokens(text: str) -> list[str]:
    return [t.lower() for t in _TOKEN.findall(text)]


def _printed_tokens(markdown: str) -> list[str]:
    """The tokens of an element's Markdown that the page prints."""
    return _tokens(_HTML_TAG.sub(" ", _LINK_TARGET.sub("] ", markdown)))


def _page_tokens(words: Sequence[tuple]) -> tuple[list[str], list[int]]:
    """The page's tokens and, for each, the index of the word it came from."""
    tokens: list[str] = []
    owner: list[int] = []
    for i, w in enumerate(words):
        for t in _tokens(w[4]):
            tokens.append(t)
            owner.append(i)
    return tokens, owner


def align(elements: Sequence[ir.Element], words: Sequence[tuple]) -> list[ir.Element]:
    """`elements` with boxes, lines and coverage from the page's `words`.

    ``words`` is ``page.get_text("words")``: (x0, y0, x1, y1, text, block, line,
    word) tuples. Elements whose text has no tokens are returned unchanged.
    """
    page_tokens, owner = _page_tokens(words)
    el_tokens: list[str] = []
    el_index: list[int] = []
    for n, el in enumerate(elements):
        for t in _printed_tokens(el.text):
            el_tokens.append(t)
            el_index.append(n)
    matched: list[list[int]] = [[] for _ in elements]  # page word indices per element
    hits = [0] * len(elements)
    free_el = list(range(len(el_tokens)))      # element tokens not yet aligned
    free_page = list(range(len(page_tokens)))  # page tokens not yet claimed
    for round_no in range(MAX_ROUNDS):
        matcher = SequenceMatcher(None, [el_tokens[i] for i in free_el],
                                  [page_tokens[j] for j in free_page], autojunk=False)
        taken_el: set[int] = set()
        taken_page: set[int] = set()
        for a, b, size in matcher.get_matching_blocks():
            # After the first pass only runs of two or more tokens count: a lone
            # common word matched out of order is more likely wrong than right.
            if size == 0 or (round_no and size < 2):
                continue
            for k in range(size):
                i, j = free_el[a + k], free_page[b + k]
                n = el_index[i]
                hits[n] += 1
                matched[n].append(owner[j])
                taken_el.add(i)
                taken_page.add(j)
        if not taken_el:
            break
        free_el = [i for i in free_el if i not in taken_el]
        free_page = [j for j in free_page if j not in taken_page]
    totals = [0] * len(elements)
    for n in el_index:
        totals[n] += 1
    # A one-word element (a page number, a lone label) that no run could place is
    # placed only when exactly one unclaimed page token can be it: two
    # candidates is a guess, so it stays unplaced.
    for i in free_el:
        n = el_index[i]
        if totals[n] != 1:
            continue
        candidates = [j for j in free_page if page_tokens[j] == el_tokens[i]]
        if len(candidates) == 1:
            hits[n] += 1
            matched[n].append(owner[candidates[0]])
            free_page.remove(candidates[0])

    out = []
    for n, el in enumerate(elements):
        if not totals[n]:
            out.append(el)
            continue
        coverage = hits[n] / totals[n]
        if coverage < MIN_COVERAGE:
            out.append(replace(el, bbox_signal=SIGNAL, bbox_coverage=coverage))
            continue
        hit_words = sorted(set(matched[n]))
        out.append(replace(el, bbox=ir.BBox.around(_box(words[i]) for i in hit_words),
                           lines=_lines(words, hit_words), bbox_signal=SIGNAL,
                           bbox_coverage=coverage))
    return out


class _Box:
    __slots__ = ("x0", "y0", "x1", "y1")

    def __init__(self, x0: float, y0: float, x1: float, y1: float) -> None:
        self.x0, self.y0, self.x1, self.y1 = x0, y0, x1, y1


def _box(word: tuple) -> _Box:
    return _Box(word[0], word[1], word[2], word[3])


def _lines(words: Sequence[tuple], hit_words: Sequence[int]) -> tuple[Line, ...]:
    """The page lines (PyMuPDF block, line) that the matched words sit on, in page order.

    Each line is the whole printed line (all its words), not only the matched ones,
    so it reads as the page prints it.
    """
    keys: list[tuple[int, int]] = []
    for i in hit_words:
        key = (words[i][5], words[i][6])
        if key not in keys:
            keys.append(key)
    wanted = set(keys)
    members: dict[tuple[int, int], list[tuple]] = {}
    for w in words:
        key = (w[5], w[6])
        if key in wanted:
            members.setdefault(key, []).append(w)
    lines = []
    for key in keys:
        ws = members[key]
        lines.append(Line(min(w[0] for w in ws), min(w[1] for w in ws), max(w[2] for w in ws),
                          max(w[3] for w in ws), " ".join(w[4] for w in ws)))
    return tuple(lines)


def page_words(page: pymupdf.Page) -> list[tuple]:
    """The page's words in PyMuPDF's extraction order (no re-sorting)."""
    return page.get_text("words", sort=False)
