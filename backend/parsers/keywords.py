"""Keyword extraction over already-converted Markdown (ADR-032).

A conversion answers "what does this document say"; a reader or an agent then
wants "what is it *about*". This module takes the Markdown the engine already
produced and returns a ranked, weighted keyword list — with the per-method
evidence behind each term, so the ranking can be judged rather than trusted.

It runs **after** extraction and after `clean_markdown`, on text alone, so it is
engine-independent: docling, PyMuPDF, Mammoth, OCR and Trafilatura output all
arrive here the same way.

**Four methods, deliberately different in kind.** Keyword extraction has no
ground truth, and each family of extractor is wrong in its own way, so the point
is not to pick a winner but to see where independent signals agree:

1. **`frequency`** — built-in, no dependency, always available. Candidate phrases
   are the runs between stopwords and punctuation; they score on how often they
   occur *and* where — a term in the title or a heading counts for more than the
   same term buried in a paragraph. This is the only method that reads the
   document's *structure*, and it is the floor the endpoint can always stand on
   (ADR-014's rule applied here: never hard-depend on an optional package).
2. **`yake`** — YAKE!'s statistical features (casing, position, dispersion). Ships
   in the base requirements: pure Python, no model download, and stopword lists
   for many languages. Its score is a *cost* — lower is better.
3. **`spacy`** — not a keyword extractor at all, which is the point: it
   contributes linguistically valid noun chunks and **named entities** (people,
   organisations, statutes, places), the terms a statistical method under-ranks
   because they are rare. Opt-in; needs a model.
4. **`keybert`** — semantic relevance: how close a candidate phrase is to the
   document's own embedding. It is the only method that can rank a term the
   document barely repeats but is entirely about. Opt-in and heavy (PyTorch).

**Fusion is by rank, not by score.** The four scores are mutually incomparable —
a YAKE cost of 0.04, a cosine similarity of 0.61 and an occurrence count of 17
cannot be averaged, and normalizing them against each other invents a
relationship that is not there. So each method's output is used only for the
*order* it puts terms in, and the orders are combined with Reciprocal Rank
Fusion: a term scores `sum(1 / (RRF_K + rank))` over the methods that ranked it.
RRF is scale-free, needs no tuning, and degrades exactly the way this project
wants — a method that is unavailable contributes nothing and the rest still rank.

**It says how sure it is, and refuses rather than guesses** (the ADR-030 rule).
Every keyword carries `agreement` (how many methods found it) and the rank and
native score each of them gave it, so a term all four agree on is visibly
different from one a single method liked. A document too short to characterise
returns *no* keywords and says why, rather than promoting whichever noun
happened to occur twice.

**Deterministic** (invariant #1). Every method here is deterministic for fixed
inputs — including `keybert`, whose weights are fixed and which runs CPU
inference with a pinned seed, the same bargain `ocr.py` strikes for EasyOCR.
Ties break on the term itself, never on dict order.
"""

from __future__ import annotations

import logging
import os
import re
import threading
from collections import Counter
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Iterable, Sequence

logger = logging.getLogger("markdown_engine.keywords")

# --- The method set ---------------------------------------------------------
# Canonical order: cheapest and most available first. Every list this module
# reports is sorted into this order, so two identical requests describe their
# methods identically (invariant #1).
KEYWORD_METHODS: tuple[str, ...] = ("frequency", "yake", "spacy", "keybert")

# What `methods="auto"` resolves to when the deployment says nothing: the two
# that cost nothing to have. `spacy` and `keybert` are opted into, per
# deployment (`WISEAU_KEYWORD_METHODS`) or per request — the same shape as the
# engine choice (ADR-025/027), and for the same reason: a model download and
# seconds of inference are a deliberate choice, not the price of every request.
_CHEAP_METHODS: tuple[str, ...] = ("frequency", "yake")

# --- Tunables ---------------------------------------------------------------
# The RRF constant. 60 is the value the original paper uses and everyone has
# used since; it flattens the difference between adjacent top ranks so that
# *agreement across methods* outweighs one method's confident first place.
_RRF_K = 60

# Longest candidate, in words. YAKE and the built-in method stop at 3; spaCy
# noun chunks and entities can run longer, and past four words a "keyword" is a
# sentence fragment that will never match another method's phrasing.
_MAX_TERM_WORDS = 4

# A document shorter than this is a paragraph, not a subject: there is nothing
# to characterise and any ranking would be noise, so none is returned.
_MIN_DOCUMENT_CHARS = 200

# How many candidates each method contributes to the fusion, as a multiple of
# the requested `top_k`. Deeper than the answer on purpose: a term ranked 40th
# by three methods is a better keyword than one ranked 5th by a single method,
# and it can only prove that if all three rankings go that deep.
_POOL_MULTIPLE = 4
_MIN_POOL = 40

# Hard ceiling on the candidate pool handed to the expensive methods. Bounds
# what one request can cost when the document is a 300-page report.
_MAX_POOL = 400

# Words with fewer characters than this are never a keyword on their own.
_MIN_WORD_CHARS = 3

# A single-word term is dropped when every one of its occurrences sits inside a
# higher-ranked phrase (see `_drop_subsumed`) — "climate" alongside "climate
# change", where the unigram adds a row and no information.
_SUBSUMPTION_SLACK = 0

# Where the keyword block is fenced in an annotated document. HTML comments so
# the markers carry no visible weight, and are exact so re-running extraction on
# an already-annotated document removes the old block rather than analysing it.
_BLOCK_OPEN = "<!-- wiseau:keywords -->"
_BLOCK_CLOSE = "<!-- /wiseau:keywords -->"
_BLOCK = re.compile(
    re.escape(_BLOCK_OPEN) + r".*?" + re.escape(_BLOCK_CLOSE) + r"\n*",
    re.DOTALL,
)


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, str(default)))
    except ValueError:
        return default


def max_document_chars() -> int:
    """How much of a document any method is allowed to read.

    A cap rather than a refusal: a book still yields keywords, they are just
    drawn from its first `n` characters. Bounds the cost of one request on a
    shared free-tier container (invariant #4's spirit).
    """
    return _env_int("WISEAU_KEYWORD_MAX_CHARS", 400_000)


def default_language() -> str:
    """Language code the methods assume when a request names none."""
    return (os.environ.get("WISEAU_KEYWORD_LANG", "en") or "en").strip().lower()


# --- Stopwords --------------------------------------------------------------
# Built-in so `frequency` needs no package. English only: `yake` carries the
# other languages, which is part of why it is in the base requirements. A
# non-English document still extracts, with `frequency` contributing less.
_STOPWORDS = frozenset(
    """
    a about above after again against all almost also although always am among an and another any
    anyone anything are around as at be because been before being below between both but by came
    can cannot could did do does doing done down during each either else enough etc even ever every
    few for from further had has have having he her here hers herself him himself his how however i
    if in into is it its itself just made make many may me might more most much must my myself
    neither never nevertheless no nor not now of off often on once one only onto or other others our
    ours ourselves out over own perhaps rather really same seem seen several shall she should since
    so some somewhat still such than that the their theirs them themselves then there therefore
    these they this those though through thus to too toward towards under until up upon us use used
    using very was we well were what when where whether which while who whom whose why will with
    within without would yet you your yours yourself yourselves
    """.split()
)

# Words that end a candidate phrase without being stopwords in the usual sense —
# the connective vocabulary of reports, which otherwise glues two real terms into
# one meaningless phrase ("funding including adaptation").
_PHRASE_BREAKERS = frozenset(
    "including include includes included following follows followed said says say per via".split()
)

_BOUNDARY_WORDS = _STOPWORDS | _PHRASE_BREAKERS

# The names a document gives its own *structure*. These are not stopwords — a
# report really can be about its recommendations — but they are the words that
# appear in headings regardless of subject, so they must not collect the heading
# prominence bonus that `frequency` pays for being in one. Without this, a
# report's top keywords are "Executive summary" and "Findings", which is true of
# every report and therefore says nothing about this one. Canonical keys (see
# `canonical`), so they are already lowercased and singularized.
_STRUCTURE_TERMS = frozenset(
    {
        "abstract",
        "acknowledgement",
        "annex",
        "appendix",
        "background",
        "bibliography",
        "conclusion",
        "content",
        "executive summary",
        "figure",
        "finding",
        "foreword",
        "glossary",
        "index",
        "introduction",
        "method",
        "methodology",
        "overview",
        "preface",
        "recommendation",
        "reference",
        "result",
        "scope",
        "summary",
        "table",
        "table of content",
    }
)

# --- Structure --------------------------------------------------------------
_FENCE = re.compile(r"^\s*(?:```|~~~)")
_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")
_HTML_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)
_HTML_TAG = re.compile(r"</?[A-Za-z][^>]*>")
_IMAGE = re.compile(r"!\[([^\]]*)\]\([^)]*\)")
_LINK = re.compile(r"\[([^\]]*)\]\([^)]*\)")
_INLINE_CODE = re.compile(r"`+([^`]*)`+")
_EMPHASIS_MARKS = re.compile(r"(\*{1,3}|_{1,3})(?=\S)(.+?)(?<=\S)\1", re.DOTALL)
_EMPHASIS_SPAN = re.compile(r"\*\*(.+?)\*\*|__(.+?)__", re.DOTALL)
_LIST_MARKER = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+")
_QUOTE_MARKER = re.compile(r"^\s*>+\s?")
_TABLE_DELIMITER = re.compile(r"^\s*\|?[\s:|-]+\|[\s:|-]*$")
_RULE = re.compile(r"^\s*(?:[-*_]\s*){3,}$")

# A word: letters/digits, with internal hyphens and apostrophes kept so
# "decision-making" and "don't" survive as single tokens.
_WORD = re.compile(r"[^\W\d_][\w'’-]*|\d+[\w'’-]*", re.UNICODE)
# Anything that ends a candidate phrase: sentence punctuation, brackets, pipes.
_BREAK = re.compile(r"[^\w\s'’-]+", re.UNICODE)
# A paragraph break — the only *whitespace* that ends a phrase. A single newline
# is a wrap, not a boundary (see `_Occurrences`).
_BLANK_LINE = re.compile(r"\n[ \t]*\n")


@dataclass(frozen=True)
class DocumentText:
    """The plain-text view of a document, plus where its emphasis falls.

    `frequency` is the only method that uses the zones; the others get `text`.
    """

    text: str
    title: str
    headings: str
    emphasis: str
    truncated: bool


def strip_keyword_block(markdown: str) -> str:
    """Remove a keyword block this module previously prepended.

    Makes both extraction and annotation **idempotent**: extracting keywords
    from an already-annotated document analyses the document, not the table of
    keywords sitting on top of it, and prepending twice replaces the block
    rather than stacking a second one.
    """
    if _BLOCK_OPEN not in markdown:
        return markdown
    return _BLOCK.sub("", markdown, count=1).lstrip("\n")


def document_text(markdown: str, limit: int | None = None) -> DocumentText:
    """Reduce Markdown to the prose the extractors should see.

    Markup is removed rather than rewritten: fenced code (which is not prose and
    would flood any frequency count with syntax), image payloads, link targets,
    table pipes, list bullets and emphasis marks all go, while the words they
    wrapped stay in place. The title, every heading, and every bold span are
    also returned separately, because *where* a term appears is a signal no
    bag-of-words method can recover afterwards.
    """
    ceiling = max_document_chars() if limit is None else limit
    body = strip_keyword_block(markdown)
    truncated = len(body) > ceiling
    if truncated:
        body = body[:ceiling]

    lines: list[str] = []
    headings: list[str] = []
    title = ""
    in_fence = False
    for raw in body.split("\n"):
        if _FENCE.match(raw):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        if _RULE.match(raw) or _TABLE_DELIMITER.match(raw):
            continue
        heading = _HEADING.match(raw)
        if heading:
            text = _inline_text(heading.group(2))
            headings.append(text)
            if not title:
                title = text
            lines.append(_terminate(text))
            continue
        line = _QUOTE_MARKER.sub("", raw)
        # A list item is a fragment of its own, not a clause of the item above.
        listed = bool(_LIST_MARKER.match(line))
        line = _LIST_MARKER.sub("", line)
        # A table row's cells are separate phrases, not one run-on sentence.
        line = line.replace("|", " . ")
        line = _inline_text(line)
        lines.append(_terminate(line) if listed else line)

    text = "\n".join(_close_blocks(lines))
    emphasis = " . ".join(
        _inline_text(match.group(1) or match.group(2) or "") for match in _EMPHASIS_SPAN.finditer(body)
    )
    return DocumentText(
        text=text,
        title=title,
        headings=" . ".join(headings),
        emphasis=emphasis,
        truncated=truncated,
    )


# What counts as a line already ending a sentence.
_SENTENCE_END = (".", "!", "?", ":", ";", '."', ".'", ".)", '?"', '!"')


def _terminate(line: str) -> str:
    """End a standalone fragment with a full stop, if it does not end itself."""
    stripped = line.rstrip()
    if not stripped or stripped.endswith(_SENTENCE_END):
        return line
    return f"{stripped}."


def _close_blocks(lines: list[str]) -> list[str]:
    """Terminate the last line of every block, so blocks cannot run together.

    A heading, a caption or a one-line paragraph carries no punctuation of its
    own, and a blank line is *not* a sentence boundary to the tokenizers these
    methods use. Without this, a document whose extraction produced no heading
    markup — which is most scanned and single-font PDFs — hands YAKE the string
    "…in the Pacific Executive summary Climate change is…" and gets back
    "Pacific Executive summary" as one of its best keywords.

    Only *block-final* lines are touched: a wrapped paragraph keeps its interior
    line breaks unpunctuated, so a phrase that spans a wrap ("the Pacific
    island / states") still reads as one phrase.
    """
    closed = list(lines)
    for index, line in enumerate(closed):
        if not line.strip():
            continue
        following = closed[index + 1] if index + 1 < len(closed) else ""
        if not following.strip():
            closed[index] = _terminate(line)
    return closed


def _inline_text(fragment: str) -> str:
    """Strip inline Markdown/HTML from one line, keeping the words."""
    fragment = _HTML_COMMENT.sub(" ", fragment)
    fragment = _IMAGE.sub(r" \1 ", fragment)
    fragment = _LINK.sub(r"\1", fragment)
    fragment = _INLINE_CODE.sub(r"\1", fragment)
    fragment = _HTML_TAG.sub(" ", fragment)
    fragment = _EMPHASIS_MARKS.sub(r"\2", fragment)
    return fragment.strip()


# --- Canonical form ---------------------------------------------------------
# Four methods phrase the same idea four ways — "Climate Change", "climate
# changes", "climate change" — and a fusion that treated those as three terms
# would report agreement as disagreement. Every term is therefore reduced to a
# canonical key for matching, while the *display* term stays the surface form
# the document actually used.


def _s_stem(word: str) -> str:
    """Fold a plural onto its singular, conservatively (the "S-stemmer").

    Deliberately the weakest possible stemmer: it only touches a trailing `s`,
    and only where English is unambiguous about it. A real stemmer would fold
    "policy" and "police", which is worse than leaving two rows in a table.
    """
    if len(word) < 4 or not word.isalpha():
        return word
    if word.endswith("ies") and len(word) > 4:
        return word[:-3] + "y"
    if word.endswith(("ss", "us", "is", "os")):
        return word
    if word.endswith("es") and word[:-2].endswith(("s", "x", "z", "ch", "sh")):
        return word[:-2]
    if word.endswith("s"):
        return word[:-1]
    return word


def _words(text: str) -> list[str]:
    """Lowercased word tokens, in order."""
    return [match.group(0).lower() for match in _WORD.finditer(text)]


def canonical(term: str) -> str:
    """The matching key for a term: lowercased, stemmed, boundary words trimmed.

    `"the Climate Changes,"` and `"climate change"` both key to
    `"climate change"`, so two methods that found the same idea fuse into one
    keyword rather than competing for adjacent rows.
    """
    words = [_s_stem(word) for word in _words(term)]
    while words and words[0] in _BOUNDARY_WORDS:
        words.pop(0)
    while words and words[-1] in _BOUNDARY_WORDS:
        words.pop()
    return " ".join(words)


def _is_usable(key: str) -> bool:
    """Whether a canonical key is worth ranking at all."""
    words = key.split()
    if not words or len(words) > _MAX_TERM_WORDS:
        return False
    if all(word.isdigit() for word in words):
        return False
    if len(words) == 1:
        word = words[0]
        return len(word) >= _MIN_WORD_CHARS and not word.isdigit() and word not in _BOUNDARY_WORDS
    return any(len(word) >= _MIN_WORD_CHARS and not word.isdigit() for word in words)


# --- Occurrence counting ----------------------------------------------------
class _Occurrences:
    """How often each candidate n-gram occurs, counted once for the document.

    Built from a token stream in which punctuation and *paragraph* breaks are
    `None` sentinels, so an n-gram is only counted when its words are genuinely
    adjacent — "funding. Adaptation" never becomes the phrase "funding
    adaptation".

    A single newline is deliberately **not** a boundary. Extracted PDF text is
    hard-wrapped at the page width, so "the Green\nClimate Fund" is one phrase
    that happens to cross a line; treating every line end as a break reported
    nought occurrences for it and kept `frequency` from proposing it at all.
    Fragments that really do stand alone (headings, list items, one-line
    paragraphs) are terminated with a full stop upstream, so punctuation still
    separates them.
    """

    def __init__(self, text: str) -> None:
        stream: list[str | None] = []
        # The same stream before lowercasing and stemming, so a term can be
        # displayed the way the document writes it rather than as its key —
        # "Analytics", not "analytic".
        raw: list[str | None] = []
        for block in _BLANK_LINE.split(text):
            for chunk in _BREAK.split(block.replace("\n", " ")):
                for match in _WORD.finditer(chunk):
                    surface = match.group(0)
                    raw.append(surface)
                    stream.append(_s_stem(surface.lower()))
                stream.append(None)
                raw.append(None)
            stream.append(None)
            raw.append(None)
        self._stream = stream
        self._raw = raw
        self._counts: list[Counter] = [Counter() for _ in range(_MAX_TERM_WORDS + 1)]
        for size in range(1, _MAX_TERM_WORDS + 1):
            counter = self._counts[size]
            for start in range(len(stream) - size + 1):
                window = stream[start : start + size]
                if None in window:
                    continue
                counter[" ".join(window)] += 1  # type: ignore[arg-type]

    def count(self, key: str) -> int:
        size = len(key.split())
        if 1 <= size <= _MAX_TERM_WORDS:
            return self._counts[size][key]
        return 0

    def candidates(self) -> Iterable[tuple[str, int]]:
        """Every n-gram that could be a keyword, with its count.

        A candidate is a run *between* stopwords, never across one: allowing an
        interior "and" or "in" turns a title into phrases like "inundation and
        adaptation funding", which is a line of the document rather than a term
        in it. Phrases that genuinely need a stopword ("Bureau of Meteorology")
        reach the fusion from `spacy`, and are still counted correctly here.
        """
        for size in range(1, _MAX_TERM_WORDS + 1):
            for key, count in self._counts[size].items():
                if any(word in _BOUNDARY_WORDS for word in key.split()):
                    continue
                if _is_usable(key):
                    yield key, count

    def surfaces(self, keys: set[str]) -> dict[str, str]:
        """The form the document uses for each key, by one pass over the stream.

        Deferred until the candidates are known, and bounded by them, so the
        untouched surface forms of every n-gram in a 400 000-character document
        never have to be held at once.
        """
        found: dict[str, Counter] = {}
        sizes = {len(key.split()) for key in keys}
        for size in sorted(sizes):
            if not 1 <= size <= _MAX_TERM_WORDS:
                continue
            for start in range(len(self._stream) - size + 1):
                window = self._stream[start : start + size]
                if None in window:
                    continue
                key = " ".join(window)  # type: ignore[arg-type]
                if key in keys:
                    found.setdefault(key, Counter())[" ".join(self._raw[start : start + size])] += 1  # type: ignore[arg-type]
        return {key: _preferred_surface(counter, key) for key, counter in found.items()}

    @property
    def token_count(self) -> int:
        return sum(1 for token in self._stream if token is not None)


# --- Method results ---------------------------------------------------------
@dataclass(frozen=True)
class _MethodResult:
    """One method's opinion: an ordered list of terms, best first."""

    ranking: tuple[tuple[str, float], ...]
    entities: frozenset[str] = frozenset()


@dataclass(frozen=True)
class MethodScore:
    """Where one method placed a keyword, and what it scored it natively."""

    rank: int
    score: float


@dataclass(frozen=True)
class Keyword:
    """One extracted keyword and the evidence behind it."""

    term: str
    score: float
    rank: int
    kind: str
    occurrences: int
    agreement: int
    methods: dict[str, MethodScore] = field(default_factory=dict)


@dataclass(frozen=True)
class KeywordSet:
    """The result of one extraction — the answer plus how it was reached."""

    keywords: tuple[Keyword, ...]
    methods_used: tuple[str, ...]
    methods_skipped: dict[str, str]
    language: str
    note: str = ""


# --- Availability -----------------------------------------------------------
def _import_error(module: str, extra: str) -> str:
    return f"'{module}' is not installed on this deployment (pip install -r {extra})."


@lru_cache(maxsize=None)
def _method_availability() -> dict[str, str]:
    """Why each optional method is unavailable, empty string when it is fine.

    Import-only: it never loads a model, so `/ping` stays cheap. A method whose
    package imports but whose *model* is missing reports that at extraction
    time instead, which is the only point at which it can be known.
    """
    status: dict[str, str] = {"frequency": ""}
    for method, module, extra in (
        ("yake", "yake", "requirements.txt"),
        ("spacy", "spacy", "requirements-keywords.txt"),
        ("keybert", "keybert", "requirements-keywords.txt"),
    ):
        try:
            __import__(module)
        except Exception as exc:  # noqa: BLE001 - a broken install is unavailability
            status[method] = _import_error(module, extra) if isinstance(exc, ImportError) else str(exc)
        else:
            status[method] = ""
    return status


def available_methods() -> list[str]:
    """The methods this build can actually run, in canonical order.

    Published by `GET /ping` so a client offers the choice instead of guessing —
    the same contract `engines` has (ADR-027).
    """
    status = _method_availability()
    return [method for method in KEYWORD_METHODS if not status.get(method, "unavailable")]


def default_methods() -> list[str]:
    """What `methods="auto"` runs here (`WISEAU_KEYWORD_METHODS`).

    Unset means the cheap pair. A deployment that has installed the extras opts
    into them once, here, rather than every caller naming them — and a request
    can still override it either way (ADR-025's shape).
    """
    raw = (os.environ.get("WISEAU_KEYWORD_METHODS", "") or "").strip().lower()
    available = available_methods()
    if raw in {"", "auto", "default"}:
        chosen = [method for method in _CHEAP_METHODS if method in available]
    elif raw == "all":
        chosen = list(available)
    else:
        named = {part.strip() for part in raw.replace(";", ",").split(",") if part.strip()}
        chosen = [method for method in KEYWORD_METHODS if method in named and method in available]
    # `frequency` needs nothing and cannot fail, so it is what stops a
    # misconfigured deployment from having no keyword capability at all.
    return chosen or ["frequency"]


def resolve_methods(requested: Sequence[str] | None) -> list[str] | None:
    """Validate a caller's method choice (the `resolve_engine` contract).

    Returns `None` for "this deployment's default", kept distinct so a request
    never pins a method set it did not ask for.

    Raises:
        ValueError: a name this build has never heard of — a caller mistake,
            which must read as a 400 rather than a failed extraction. A name it
            *knows* but cannot run is not an error here: it is reported as
            skipped, so a missing optional package degrades the answer instead
            of refusing it (ADR-014's rule).
    """
    if requested is None:
        return None
    names = [str(name).strip().lower() for name in requested if str(name).strip()]
    if not names or names == ["auto"] or names == ["default"]:
        return None
    if "all" in names:
        return list(KEYWORD_METHODS)
    unknown = sorted({name for name in names if name not in KEYWORD_METHODS})
    if unknown:
        supported = ", ".join((*KEYWORD_METHODS, "auto", "all"))
        raise ValueError(
            f"Unknown keyword method{'s' if len(unknown) > 1 else ''} "
            f"{', '.join(repr(name) for name in unknown)}. Supported: {supported}."
        )
    return [method for method in KEYWORD_METHODS if method in set(names)]


# --- The methods ------------------------------------------------------------
def _run_frequency(doc: DocumentText, occurrences: _Occurrences, pool: int) -> _MethodResult:
    """Structural term frequency — the built-in, always-available method.

    Two things separate it from a word count. Candidates are the runs *between*
    stopwords, so they are phrases rather than arbitrary n-grams; and a term is
    weighted by where it appears, because a document's title and headings are
    the author's own statement of what it is about. A term seen once and never
    in a heading is dropped: once is not a topic.
    """
    title_key = f" {canonical(doc.title)} "
    headings_key = f" {' . '.join(canonical(part) for part in doc.headings.split(' . '))} "
    emphasis_key = f" {' . '.join(canonical(part) for part in doc.emphasis.split(' . '))} "

    scored: list[tuple[str, float]] = []
    for key, count in occurrences.candidates():
        padded = f" {key} "
        # A structural term gets no credit for appearing in a heading: that is
        # where it always appears, in every document, whatever the document is
        # about. It can still rank on genuine repetition in the body.
        structural = key in _STRUCTURE_TERMS
        in_title = padded in title_key and not structural
        in_heading = padded in headings_key and not structural
        if count < 2 and not (in_title or in_heading):
            continue
        prominence = 1.0 + (2.0 if in_title else 0.0) + (1.0 if in_heading else 0.0)
        if padded in emphasis_key:
            prominence += 0.3
        # Multi-word terms are more specific than the words inside them, so a
        # phrase seen five times outranks a word seen five times.
        length_bonus = (1.0, 1.0, 1.4, 1.6, 1.6)[len(key.split())]
        scored.append((key, count * length_bonus * prominence))

    scored.sort(key=lambda item: (-item[1], item[0]))
    top = scored[:pool]
    surfaces = occurrences.surfaces({key for key, _ in top})
    return _MethodResult(ranking=tuple((surfaces.get(key, key), score) for key, score in top))


def _run_yake(doc: DocumentText, language: str, pool: int) -> _MethodResult:
    """YAKE! — statistical features over the raw text (lower score is better)."""
    import yake

    extractor = yake.KeywordExtractor(lan=language, n=3, dedup_lim=0.9, top=pool)
    results = extractor.extract_keywords(doc.text)
    # Sorted here rather than trusted: the tie order of an upstream sort is not
    # part of its contract, and this one has to be reproducible (invariant #1).
    ordered = sorted(((str(term), float(score)) for term, score in results), key=lambda item: (item[1], item[0]))
    return _MethodResult(ranking=tuple(ordered))


def _spacy_model_name() -> str:
    return (os.environ.get("WISEAU_SPACY_MODEL", "") or "en_core_web_sm").strip()


@lru_cache(maxsize=4)
def _spacy_pipeline(model: str):
    """Load a spaCy pipeline once per process (models are tens of megabytes)."""
    import spacy

    return spacy.load(model)


# Entity labels worth reporting as keywords. Deliberately not the whole set:
# CARDINAL, ORDINAL, PERCENT, MONEY, DATE and TIME are facts *in* a document,
# never what it is about, and they would crowd out every real term.
_ENTITY_LABELS = frozenset(
    {"PERSON", "ORG", "GPE", "LOC", "NORP", "FAC", "EVENT", "LAW", "PRODUCT", "WORK_OF_ART", "LANGUAGE"}
)


def _run_spacy(doc: DocumentText, pool: int) -> _MethodResult:
    """Noun chunks and named entities — the linguistically valid candidates.

    Contributes the terms a frequency count under-ranks: a statute cited three
    times in a hundred pages is not frequent, but it is unmistakably what a
    section is about. Entities are scored above noun chunks of the same count
    for exactly that reason.
    """
    nlp = _spacy_pipeline(_spacy_model_name())
    text = doc.text
    if len(text) > nlp.max_length:
        text = text[: nlp.max_length]
    parsed = nlp(text)

    counts: Counter = Counter()
    entities: set[str] = set()
    surfaces: dict[str, Counter] = {}

    def add(term: str, is_entity: bool) -> None:
        key = canonical(term)
        if not _is_usable(key):
            return
        counts[key] += 2 if is_entity else 1
        surfaces.setdefault(key, Counter())[term.strip()] += 1
        if is_entity:
            entities.add(key)

    for chunk in parsed.noun_chunks:
        add(chunk.text, False)
    for entity in parsed.ents:
        if entity.label_ in _ENTITY_LABELS:
            add(entity.text, True)

    ordered = sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    ranking = tuple(
        (_preferred_surface(surfaces[key], key), float(weight)) for key, weight in ordered[:pool]
    )
    return _MethodResult(ranking=ranking, entities=frozenset(entities))


def _preferred_surface(surfaces: Counter, fallback: str) -> str:
    """The surface form to display: the most common, ties broken lexically."""
    if not surfaces:
        return fallback
    return min(surfaces.items(), key=lambda item: (-item[1], item[0]))[0]


def _keybert_model_name() -> str:
    return (os.environ.get("WISEAU_KEYBERT_MODEL", "") or "all-MiniLM-L6-v2").strip()


_keybert_lock = threading.Lock()


@lru_cache(maxsize=2)
def _keybert_model(name: str):
    """Build the KeyBERT model once per process, with the seed pinned.

    Weights are fixed and inference runs on CPU, so output is reproducible for a
    given model — the same bargain `ocr.py` strikes for EasyOCR (ADR-012).
    """
    from keybert import KeyBERT

    try:
        import torch

        torch.manual_seed(0)
        torch.use_deterministic_algorithms(True, warn_only=True)
    except Exception:  # noqa: BLE001 - the seed is a belt-and-braces measure
        logger.debug("torch not seeded for keybert", exc_info=True)
    return KeyBERT(model=name)


# How much text goes into one embedding, and how many of them a document gets.
# The encoder truncates at a few hundred tokens, so embedding a report whole
# would embed its cover page; chunking and averaging represents the document,
# and the cap bounds what a 300-page PDF can cost.
_KEYBERT_CHUNK_CHARS = 1500
_KEYBERT_MAX_CHUNKS = 24


def _chunk_evenly(text: str, size: int, limit: int) -> list[str]:
    """Split text into at most `limit` chunks, sampled evenly across it.

    Taking the first `limit` chunks would characterise the introduction. Striding
    across the whole document instead keeps the sample representative, and the
    stride is computed from the length, so it is the same on every run.
    """
    chunks = [text[start : start + size] for start in range(0, len(text), size)]
    chunks = [chunk for chunk in chunks if chunk.strip()]
    if len(chunks) <= limit:
        return chunks
    stride = len(chunks) / limit
    return [chunks[min(len(chunks) - 1, int(index * stride))] for index in range(limit)]


def _run_keybert(doc: DocumentText, candidates: Sequence[str], pool: int) -> _MethodResult:
    """Semantic relevance — cosine similarity between term and document.

    Runs as a **re-ranker over the pooled candidates** the other methods
    produced, not as a fifth candidate generator. Two reasons: the candidates
    are then a shared vocabulary, so the fusion measures agreement rather than
    comparing disjoint lists; and scoring a bounded candidate set is a bounded
    amount of inference, which matters on a free CPU where this is the only
    method that can take minutes.

    The document embedding is the mean of embeddings taken across the whole
    document, not one embedding of the text handed to the encoder — a
    sentence-transformer truncates at a few hundred tokens, so the obvious call
    would silently rank every candidate against the first page alone.
    """
    if not candidates:
        return _MethodResult(ranking=())
    model = _keybert_model(_keybert_model_name())

    import numpy as np
    from sklearn.feature_extraction.text import CountVectorizer

    # KeyBERT's own vectorizer would drop most of what it is given here, in three
    # silent ways: it lowercases the document but not the vocabulary, so every
    # capitalized candidate ("Green Climate Fund") matches nothing; it defaults to
    # unigrams, so every phrase matches nothing; and it strips English stopwords
    # before forming n-grams, so "bureau of meteorology" matches nothing. None of
    # those raise — they just return a shorter list — so the vectorizer is
    # supplied explicitly rather than trusted to defaults.
    surface_by_key = {}
    for candidate in candidates:
        surface_by_key.setdefault(candidate.lower(), candidate)
    vocabulary = sorted(surface_by_key)
    vectorizer = CountVectorizer(
        vocabulary=vocabulary,
        ngram_range=(1, _MAX_TERM_WORDS),
        lowercase=True,
        stop_words=None,
        # Keep hyphens and apostrophes inside a token, so "decision-making" is
        # the one token the candidate list calls it.
        token_pattern=r"(?u)\b\w[\w'\u2019-]*\b",
    )

    # One model instance, shared across worker threads; sentence-transformers is
    # not documented as thread-safe and a torn batch would be a silent wrong
    # answer rather than an error.
    with _keybert_lock:
        chunks = _chunk_evenly(doc.text, _KEYBERT_CHUNK_CHARS, _KEYBERT_MAX_CHUNKS)
        if not chunks:
            return _MethodResult(ranking=())
        chunk_embeddings = np.asarray(model.model.embed(chunks), dtype="float32")
        pooled = chunk_embeddings.mean(axis=0).reshape(1, -1)
        results = model.extract_keywords(
            [doc.text],
            vectorizer=vectorizer,
            top_n=pool,
            doc_embeddings=pooled,
        )

    ranked = results[0] if results and isinstance(results[0], list) else results
    ordered = sorted(
        ((surface_by_key.get(str(term), str(term)), float(score)) for term, score in ranked),
        key=lambda item: (-item[1], item[0]),
    )
    return _MethodResult(ranking=tuple(ordered))


# --- Fusion -----------------------------------------------------------------
def _fuse(
    results: dict[str, _MethodResult],
    occurrences: _Occurrences,
    top_k: int,
) -> list[Keyword]:
    """Combine the per-method rankings by Reciprocal Rank Fusion.

    Each method contributes `1 / (RRF_K + rank)` to every term it ranked, keyed
    on the canonical form so the four methods' phrasings of one idea add up
    instead of competing. The reported `score` is that sum divided by the
    winner's, which turns an arbitrary small number into a relative weight a
    reader can act on: the top term is 1.0 and the rest say how far behind they
    are.
    """
    fused: dict[str, float] = {}
    per_method: dict[str, dict[str, MethodScore]] = {}
    surfaces: dict[str, Counter] = {}
    entities: set[str] = set()

    for method in KEYWORD_METHODS:
        result = results.get(method)
        if result is None:
            continue
        entities |= result.entities
        seen: set[str] = set()
        for position, (term, native) in enumerate(result.ranking, start=1):
            key = canonical(term)
            if not _is_usable(key) or key in seen:
                # A method that lists two phrasings of one term votes once: the
                # better-placed of them, not twice for the same idea.
                continue
            seen.add(key)
            fused[key] = fused.get(key, 0.0) + 1.0 / (_RRF_K + position)
            per_method.setdefault(key, {})[method] = MethodScore(rank=position, score=round(native, 6))
            surfaces.setdefault(key, Counter())[term.strip()] += 1

    if not fused:
        return []

    ordered = sorted(fused.items(), key=lambda item: (-item[1], item[0]))
    ordered = _drop_subsumed(ordered, occurrences)
    best = ordered[0][1]

    keywords: list[Keyword] = []
    for position, (key, score) in enumerate(ordered[:top_k], start=1):
        methods = per_method[key]
        keywords.append(
            Keyword(
                term=_preferred_surface(surfaces[key], key),
                # Relative to the top term, so the number means something to a
                # reader; the raw per-method scores stay in `methods`.
                score=round(score / best, 4) if best else 0.0,
                rank=position,
                kind="entity" if key in entities else "phrase",
                occurrences=occurrences.count(key),
                agreement=len(methods),
                methods={name: methods[name] for name in KEYWORD_METHODS if name in methods},
            )
        )
    return keywords


def _drop_subsumed(
    ordered: list[tuple[str, float]], occurrences: _Occurrences
) -> list[tuple[str, float]]:
    """Remove a term that only ever occurs inside a better-ranked longer one.

    "climate" next to "climate change" is a row that carries no information the
    row above it does not. It is dropped only when *every* occurrence of the
    shorter term is inside the longer one — a document that also discusses
    climate on its own keeps both.
    """
    kept: list[tuple[str, float]] = []
    for key, score in ordered:
        count = occurrences.count(key)
        subsumed = False
        if count:
            for other, _ in kept:
                if len(other) <= len(key):
                    continue
                if f" {key} " in f" {other} " and occurrences.count(other) >= count - _SUBSUMPTION_SLACK:
                    subsumed = True
                    break
        if not subsumed:
            kept.append((key, score))
    return kept


# --- The public entry point -------------------------------------------------
def extract_keywords(
    markdown: str,
    *,
    methods: Sequence[str] | None = None,
    top_k: int = 20,
    language: str | None = None,
) -> KeywordSet:
    """Rank the keywords of an already-converted document.

    Args:
        markdown: The document, as the engine returned it. Any keyword block a
            previous run prepended is removed before analysis.
        methods: Which methods to run; `None` means this deployment's default.
            A named method that is not installed is *skipped and reported*, not
            an error — the answer degrades, the request does not fail.
        top_k: How many keywords to return.
        language: Language code for the language-aware methods (`yake`).

    Returns:
        A `KeywordSet`: the ranked keywords, the methods that actually ran, why
        any named method did not, and a `note` when the answer is empty for a
        reason the caller should see.
    """
    chosen = list(methods) if methods is not None else default_methods()
    chosen = [method for method in KEYWORD_METHODS if method in set(chosen)] or ["frequency"]
    lang = (language or default_language()).strip().lower() or "en"
    top_k = max(1, int(top_k))
    pool = min(_MAX_POOL, max(_MIN_POOL, top_k * _POOL_MULTIPLE))

    doc = document_text(markdown)
    skipped: dict[str, str] = {}
    availability = _method_availability()
    for method in chosen:
        reason = availability.get(method, "")
        if reason:
            skipped[method] = reason

    runnable = [method for method in chosen if method not in skipped]
    if len(doc.text.strip()) < _MIN_DOCUMENT_CHARS:
        # The ADR-030 rule: say nothing rather than promote noise. Twelve words
        # of text cannot support a ranking, and an empty list with a reason is
        # something a caller can act on where three arbitrary nouns are not.
        return KeywordSet(
            keywords=(),
            methods_used=tuple(runnable),
            methods_skipped=skipped,
            language=lang,
            note=f"Document is too short to extract keywords from (under {_MIN_DOCUMENT_CHARS} characters of text).",
        )

    occurrences = _Occurrences(doc.text)
    results: dict[str, _MethodResult] = {}
    used: list[str] = []

    # Cheap, candidate-generating methods first: `keybert` re-ranks what they
    # pooled, so it has to run last, and it has nothing to do if they all failed.
    for method in [m for m in runnable if m != "keybert"]:
        try:
            if method == "frequency":
                results[method] = _run_frequency(doc, occurrences, pool)
            elif method == "yake":
                results[method] = _run_yake(doc, lang, pool)
            elif method == "spacy":
                results[method] = _run_spacy(doc, pool)
        except Exception as exc:  # noqa: BLE001 - one method failing is not a failed request
            logger.exception("keyword method %s failed", method)
            skipped[method] = f"{method} failed: {exc}"
            continue
        used.append(method)

    if "keybert" in runnable:
        pooled = _pooled_candidates(results, occurrences, pool)
        try:
            results["keybert"] = _run_keybert(doc, pooled, pool)
        except Exception as exc:  # noqa: BLE001
            logger.exception("keyword method keybert failed")
            skipped["keybert"] = f"keybert failed: {exc}"
        else:
            used.append("keybert")

    keywords = _fuse(results, occurrences, top_k)
    note = ""
    if doc.truncated:
        note = (
            f"Only the first {max_document_chars():,} characters of the document were analysed."
        )
    if not keywords and not note:
        note = "No terms in this document occurred often or prominently enough to rank as keywords."
    return KeywordSet(
        keywords=tuple(keywords),
        methods_used=tuple(method for method in KEYWORD_METHODS if method in set(used)),
        methods_skipped=dict(sorted(skipped.items())),
        language=lang,
        note=note,
    )


def _pooled_candidates(
    results: dict[str, _MethodResult], occurrences: _Occurrences, pool: int
) -> list[str]:
    """The shared vocabulary `keybert` re-ranks: what the other methods found.

    Ordered by how many methods proposed each term and then by the term itself,
    so the list handed to the model is the same on every run. Falls back to the
    document's own frequent n-grams if nothing else produced candidates, so
    `methods=["keybert"]` alone still answers.
    """
    votes: Counter = Counter()
    surfaces: dict[str, Counter] = {}
    for result in results.values():
        for term, _ in result.ranking:
            key = canonical(term)
            if _is_usable(key):
                votes[key] += 1
                surfaces.setdefault(key, Counter())[term.strip()] += 1
    if not votes:
        for key, count in occurrences.candidates():
            if count >= 2:
                votes[key] += 1
                surfaces.setdefault(key, Counter())[key] += 1
    ordered = sorted(votes.items(), key=lambda item: (-item[1], item[0]))
    return [_preferred_surface(surfaces[key], key) for key, _ in ordered[:pool]]


# --- Rendering --------------------------------------------------------------
def keyword_table(result: KeywordSet) -> str:
    """The keyword set as a Markdown table.

    Three columns, because the fourth is always the one that gets skimmed past:
    the term, its weight relative to the top term, and which methods found it —
    which is the honest way to present a ranking with no ground truth. The
    caption names the methods and the weighting so the table still explains
    itself once it has been copied out of here.
    """
    if not result.keywords:
        reason = result.note or "No keywords were extracted."
        return f"## Keywords\n\n*{reason}*\n"

    lines = ["## Keywords", "", "| Keyword | Weight | Found by |", "| --- | ---: | --- |"]
    for keyword in result.keywords:
        methods = ", ".join(name for name in KEYWORD_METHODS if name in keyword.methods)
        lines.append(f"| {_escape_cell(keyword.term)} | {keyword.score:.2f} | {methods} |")
    lines.append("")
    lines.append(
        f"*{len(result.keywords)} keyword{'s' if len(result.keywords) != 1 else ''} extracted by "
        f"{', '.join(result.methods_used) or 'no method'}. "
        "Weight is relative to the top-ranked term (1.00), fused across methods by rank.*"
    )
    if result.note:
        lines.append("")
        lines.append(f"*{result.note}*")
    return "\n".join(lines) + "\n"


def _escape_cell(text: str) -> str:
    """Make a term safe inside a table cell: no stray pipes, no line breaks."""
    return text.replace("|", "\\|").replace("\n", " ").strip()


def prepend_keyword_table(markdown: str, result: KeywordSet) -> str:
    """Return the document with the keyword table at the top.

    Placed **after the document's own opening heading** when it has one, and at
    the very top otherwise. A file whose first line is `## Keywords` has lost
    its title to a summary of itself — every downstream reader, from a static
    site generator to the UI's own "name this download" dialog, takes the first
    heading as what the document *is*.

    The block is fenced in HTML comments, so running this twice replaces the
    table rather than stacking a second one, and extraction run on an annotated
    document analyses the document rather than its own output.
    """
    body = strip_keyword_block(markdown)
    # Blank lines around the table so the block is well-formed Markdown in
    # its own right: a renderer that joins adjacent lines into a paragraph
    # must not be able to absorb the closing marker into the caption.
    block = f"{_BLOCK_OPEN}\n\n{keyword_table(result)}\n{_BLOCK_CLOSE}"

    lines = body.split("\n")
    insert_at = 0
    for index, line in enumerate(lines):
        if not line.strip():
            continue
        # Only a heading that *opens* the document keeps its place; a heading
        # further down is a section, and the table belongs above it.
        if _HEADING.match(line):
            insert_at = index + 1
        break

    head = "\n".join(lines[:insert_at]).rstrip("\n")
    tail = "\n".join(lines[insert_at:]).strip("\n")
    parts = [part for part in (head, block, tail) if part]
    return "\n\n".join(parts) + "\n"
