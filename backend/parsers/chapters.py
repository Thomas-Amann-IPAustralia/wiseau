"""Split a long document's Markdown into its chapters (ADR-030).

A 300-page PDF converts to one 300-page Markdown string, which is the wrong
shape for the reader who wanted *one file per chapter*. This module finds the
chapter boundaries in already-extracted Markdown and returns the document as an
ordered list of `Chapter` slices, each with a filename a caller can save it
under. It is the "save each chapter separately" half of the feature; the API
turns the slices into JSON and the UI turns them into files.

**How chapters are found**, in order of how much the document tells us:

1. **The contents page** (`method="toc"`). A book that has chapters almost always
   prints them in a table of contents, and that page survives extraction as a run
   of "title ..... 12" lines. Those entries are the *author's own* list of
   chapters — better than any guess this module could make about which headings
   matter — so they are tried first: parse the entries, then find each one again
   in the body below, in order. The block is only believed once at least half of
   its top-level entries are found in document order (see `_MIN_TOC_MATCH_RATIO`),
   which is what stops a back-of-book index or a page of prose from splitting a
   document into nonsense.
2. **Heading structure** (`method="headings"`). No usable contents page: split on
   the shallowest heading level that occurs more than once, preferring a level
   whose headings actually look like chapters ("Chapter 4", "Part II",
   "Appendix A", "3. Findings") over one that merely sits higher in the tree.
3. **Chapter markers in plain text** (`method="markers"`). Scanned and
   single-font PDFs often arrive with no heading markup at all, so a chapter
   opens as a bare line reading "CHAPTER FOUR". Those lines are the last signal
   worth using.
4. **Nothing** (`method="none"`). A document with no chapter structure is left
   whole rather than being cut at arbitrary points — an article is not a book.

**Guarantees.** The chapters *partition* the document: concatenating them in
order reproduces the input, modulo the whitespace normalization every chapter is
put through (`clean_markdown`, invariant #3). Nothing is dropped and nothing is
duplicated — content before the first chapter (title page, the contents page
itself) is returned as its own leading section rather than being discarded. And
like every other default path, the split is **deterministic**: the same Markdown
always yields the same chapters, titles, and filenames. There is no model here,
only text.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from difflib import SequenceMatcher

from .cleaner import clean_markdown
from .naming import slug

# --- Tunables ---------------------------------------------------------------
# A contents block has to list at least this many entries before it is treated as
# a table of contents at all. Two lines that happen to end in a number are a
# coincidence; four are a contents page.
_MIN_TOC_ENTRIES = 3

# ...and at least this fraction of its top-level entries must then be found, in
# order, in the body below it. This is the gate that rejects a back-of-book index
# (whose entries point at pages, not at sections that follow it in order).
_MIN_TOC_MATCH_RATIO = 0.5

# Two normalized titles at least this similar (difflib ratio) are the same
# heading. Extraction routinely drops a subtitle or mangles a dash, so exact
# equality alone misses real matches; below this, titles that merely share
# vocabulary would start colliding.
_FUZZY_MATCH_RATIO = 0.86

# A "chapter" this short is a page header or a stray line, not a chapter. Only
# applied to the *median* section, so one genuinely brief chapter is fine.
_MIN_MEDIAN_CHAPTER_CHARS = 200

# Material before the first chapter shorter than this is a bare title line, not
# front matter, so it opens the first chapter instead of becoming its own file.
# (A title page plus a contents page is comfortably longer.)
_MIN_FRONT_MATTER_CHARS = 100

# Splitting a document into more pieces than this means the split found
# paragraphs, not chapters; the result is discarded and the document stays whole.
_MAX_CHAPTERS = 500

# The longest a line can be and still plausibly be a chapter title.
_MAX_TITLE_CHARS = 120

# How far into the document to look for a contents page. Front matter, always —
# a heading named "Contents" three-quarters of the way through a report is a
# section about contents, not the document's index.
_TOC_SEARCH_FRACTION = 0.6

# Sub-section entries indent under their chapter. Extractors are inconsistent
# about how much, so indentation is bucketed rather than trusted exactly.
_INDENT_PER_DEPTH = 2

# --- Structure --------------------------------------------------------------
_FENCE = re.compile(r"^\s*(?:```|~~~)")
_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")
_LIST_MARKER = re.compile(r"^[-*+•]\s+")
_TABLE_DELIMITER = re.compile(r"^\s*\|?[\s:|-]+\|[\s:|-]*$")
_TABLE_ROW = re.compile(r"^\s*\|(.+)\|\s*$")
_BOLD_LINE = re.compile(r"^\*\*(.+)\*\*$")
_PAGE_NUMBER = re.compile(r"^(?:\d{1,4}|[ivxlcdm]{1,8})$", re.I)

# The heading that introduces a contents page. Deliberately narrow: these are
# the words a document uses for *its own* index, and each must be the whole line.
_CONTENTS_TITLE = re.compile(
    r"^(?:table\s+of\s+)?contents$"
    r"|^index$"
    r"|^table\s+of\s+contents$"
    r"|^summary\s+of\s+contents$"
    r"|^in\s+this\s+(?:issue|report|volume|edition)$",
    re.I,
)

# "Chapter 4", "Part II", "Appendix A", "Lesson 3" — the words a document uses to
# announce a chapter. The number (or the end of the line) is required: without it
# "Part of the problem" and "Book of Revelation" would read as chapter openings.
_CHAPTER_WORDS = (
    r"chapter|chap\.?|part|section|appendix|annex|book|unit|lesson|module|volume|article"
)
_NUMBER_WORDS = (
    r"one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|thirteen|fourteen"
    r"|fifteen|sixteen|seventeen|eighteen|nineteen|twenty"
)
_CHAPTER_NUMBER = rf"\d+|[ivxlcdm]+|[a-z]|{_NUMBER_WORDS}"
_CHAPTER_MARKER = re.compile(
    rf"^(?:{_CHAPTER_WORDS})\b(?:[\s.:\-]*(?:{_CHAPTER_NUMBER})\b|[\s.:\-]*$)",
    re.I,
)

# A leading number the *document* uses for ordering ("3.", "1.2", "IV.", "b)").
# Stripped only for comparison, so a contents entry reading "Chapter 3: Blades"
# can still match a body heading that reads just "Blades".
_LABEL_PREFIX = re.compile(
    rf"^(?:{_CHAPTER_WORDS})\b[\s.:\-]*(?:{_CHAPTER_NUMBER})?\b[\s.:\-]*",
    re.I,
)
_NUMERIC_PREFIX = re.compile(r"^\d+(?:[.\-]\d+)*\s*[.):\-]?\s+")
_ROMAN_PREFIX = re.compile(r"^[ivxlcdm]+\s*[.)]\s+", re.I)
_DOTTED_NUMBER = re.compile(r"^(\d+(?:\.\d+)+)")

# A contents entry with a page number: "The Long Con .......... 118". The
# separator must be at least two characters wide (dot leaders, a rule of spaces,
# a run of underscores), which is what distinguishes an entry from a sentence
# that happens to end in a number.
_ENTRY_WITH_PAGE = re.compile(
    r"^(?P<title>\S.*?)[\s.·•_–—\-]{2,}(?P<page>\d{1,4}|[ivxlcdm]{1,8})\s*$",
    re.I,
)
# The same thing after an extractor has collapsed the leaders to one space.
# Only trusted *inside* an already-identified contents block.
_ENTRY_WITH_PAGE_LOOSE = re.compile(r"^(?P<title>\S.*?[^\d\s])\s+(?P<page>\d{1,4})\s*$")
# A linked entry, which is how a TOC survives an HTML or DOCX conversion.
_ENTRY_LINK = re.compile(
    r"^\[(?P<label>[^\]]+)\]\([^)]*\)[\s.·_\-]*(?P<page>\d{1,4})?\s*$"
)

_EMPHASIS = re.compile(r"[*_`~]+")
_MD_LINK = re.compile(r"!?\[([^\]]*)\]\([^)]*\)")
_NON_WORD = re.compile(r"[^\w\s]+", re.UNICODE)


# --- Public shapes ----------------------------------------------------------
@dataclass(frozen=True)
class Chapter:
    """One chapter-sized slice of a document, ready to be written to a file."""

    title: str
    level: int
    filename: str
    markdown: str

    @property
    def length(self) -> int:
        return len(self.markdown)


@dataclass(frozen=True)
class ChapterSplit:
    """The result of trying to split a document.

    `method` records which signal actually fired (`toc`, `headings`, `markers`,
    or `none`) so the answer is auditable: a caller — and an operator reading
    `GET /metrics` — can tell "split by the document's own contents page" from
    "split by guessing at headings", which are very different levels of
    confidence. `chapters` is empty exactly when `method` is `none`.
    """

    method: str
    chapters: tuple[Chapter, ...]

    def __bool__(self) -> bool:
        return bool(self.chapters)


# --- Text normalization -----------------------------------------------------
def _normalize(text: str) -> str:
    """Reduce a title to comparable words: no markup, no case, no punctuation."""
    stripped = _MD_LINK.sub(r"\1", text.strip())
    stripped = re.sub(r"^#{1,6}\s+", "", stripped)
    stripped = _EMPHASIS.sub("", stripped)
    stripped = _NON_WORD.sub(" ", stripped)
    return re.sub(r"\s+", " ", stripped).strip().lower()


def _strip_numbering(text: str) -> str:
    """Drop a leading chapter label or number: `3. Blades` -> `blades`.

    Roman numerals and single letters are only removed when punctuation confirms
    them (`iv.`, `b)`), because "Vivid" and "A" are also words a title can start
    with, and a normalization that eats them would match the wrong sections.
    """
    for pattern in (_LABEL_PREFIX, _NUMERIC_PREFIX, _ROMAN_PREFIX):
        stripped = pattern.sub("", text, count=1)
        if stripped != text:
            return stripped.strip()
    return text


def _keys(title: str) -> tuple[str, str]:
    """Comparison keys for a title: as written, and without its numbering.

    Both are kept because extraction is inconsistent about which side carries the
    number — a contents page saying "4 The Ledger" against a heading saying
    "Chapter 4 - The Ledger" only matches once both are reduced. When stripping
    leaves nothing (an entry that is *only* "Chapter 4"), the full form is reused
    so that "Chapter 4" and "Chapter 5" stay distinguishable.
    """
    base = _normalize(title)
    stripped = _strip_numbering(base)
    return base, (stripped or base)


def _similar(left: str, right: str) -> bool:
    """Fuzzy title equality, with difflib's own cheap pre-filters in front."""
    if len(left) < 6 or len(right) < 6:
        return False
    matcher = SequenceMatcher(None, left, right)
    return (
        matcher.real_quick_ratio() >= _FUZZY_MATCH_RATIO
        and matcher.quick_ratio() >= _FUZZY_MATCH_RATIO
        and matcher.ratio() >= _FUZZY_MATCH_RATIO
    )


def _same_title(left: tuple[str, str], right: tuple[str, str]) -> bool:
    """Whether two titles name the same section, under either comparison key."""
    for one, other in ((left[0], right[0]), (left[1], right[1]), (left[0], right[1]), (left[1], right[0])):
        if not one or not other:
            continue
        if one == other:
            return True
        # A heading often carries a subtitle the contents page omits (or the
        # reverse), so one being the opening of the other is a match — but only
        # when the shorter side is long enough to be distinctive.
        shorter, longer = sorted((one, other), key=len)
        if len(shorter) >= 8 and longer.startswith(shorter):
            return True
    return _similar(left[1], right[1])


def _repeats(text: str, seen: list[tuple[str, str]]) -> bool:
    """Whether a line restates a title already listed on the contents page.

    Exact equality only — deliberately stricter than `_same_title`. A contents
    page that lists "Chapter 1" and "Chapter 2" would fail a fuzzy test on its
    own second line (those two strings are 89% similar), and truncating a
    contents page at its second entry is a worse error than reading one line of
    the body as an entry.
    """
    keys = _keys(text)
    return any(keys[0] == key[0] or keys[1] == key[1] for key in seen)


# --- Document scanning ------------------------------------------------------
@dataclass(frozen=True)
class _Anchor:
    """A line that could open a chapter: a heading, or a heading-like line."""

    line: int
    level: int  # 1-6 for an ATX heading; 0 for an unmarked line
    text: str
    keys: tuple[str, str]
    marker: bool  # reads like "Chapter 4" / "Part II" / "Appendix A"
    heading_like: bool  # a heading, a bold line, capitals, or a chapter marker


def _fence_mask(lines: list[str]) -> list[bool]:
    """Which lines sit inside a fenced code block (and so are never structure)."""
    inside = False
    mask = []
    for line in lines:
        if _FENCE.match(line):
            mask.append(True)  # the fence itself is code, not a heading
            inside = not inside
            continue
        mask.append(inside)
    return mask


def _looks_like_title(text: str) -> bool:
    """A short, title-shaped line — the shape an unmarked chapter opening has."""
    if not text or len(text) > _MAX_TITLE_CHARS:
        return False
    if _BOLD_LINE.match(text) or _CHAPTER_MARKER.match(text):
        return True
    letters = [c for c in text if c.isalpha()]
    # Fully capitalized lines ("THE SECOND WITNESS") are the other common way a
    # single-font PDF signals a chapter opening.
    return len(letters) >= 3 and all(c.isupper() for c in letters)


def _anchors(lines: list[str], fenced: list[bool], *, wide: bool = False) -> list[_Anchor]:
    """Plausible chapter openings, in document order.

    The narrow set (the default) is headings and lines that *look* like headings
    — what the heading and marker strategies split on. The `wide` set adds every
    other short line, and exists for one job: a contents page tells us the exact
    titles to look for, and in a PDF whose chapter openings kept no markup at all
    ("Foreword" on its own line, in the same font as the body) those titles can
    only be found by matching the text. It is built lazily, since it costs a
    normalization per line and only the contents-page path needs it.
    """
    found: list[_Anchor] = []
    for index, raw in enumerate(lines):
        if fenced[index]:
            continue
        stripped = raw.strip()
        if not stripped:
            continue
        heading = _HEADING.match(stripped)
        heading_like = True
        if heading:
            text = heading.group(2).strip()
            level = len(heading.group(1))
        else:
            level = 0
            heading_like = _looks_like_title(stripped)
            if not (heading_like or wide):
                continue
            bold = _BOLD_LINE.match(stripped)
            text = (bold.group(1) if bold else stripped).strip()
        text = _normalize_title_text(text)
        # An over-long line is prose, not a title — but a heading stays an anchor
        # whatever its length, because the heading strategy counts levels.
        if not text or (level == 0 and len(text) > _MAX_TITLE_CHARS):
            continue
        found.append(
            _Anchor(
                line=index,
                level=level,
                text=text,
                keys=_keys(text),
                marker=bool(_CHAPTER_MARKER.match(text)),
                heading_like=heading_like,
            )
        )
    return found


def _normalize_title_text(text: str) -> str:
    """A title as it should read in output: markup off, whitespace collapsed."""
    plain = _MD_LINK.sub(r"\1", text)
    plain = _EMPHASIS.sub("", plain)
    return re.sub(r"\s+", " ", plain).strip(" .·-–—\t")


# --- The contents page ------------------------------------------------------
@dataclass(frozen=True)
class _TocEntry:
    line: int
    title: str
    depth: int
    page: str | None
    from_heading: bool
    # Whether the line carried contents-page *structure* — a page number, a link,
    # a list marker, a table row — as opposed to being a bare line that only
    # counts as an entry because it sits under a "Contents" heading.
    structured: bool


def _entry_depth(indent: int, title: str) -> int:
    """How deep an entry sits: from its indentation, or from its own numbering."""
    depth = indent // _INDENT_PER_DEPTH
    dotted = _DOTTED_NUMBER.match(title)
    if dotted:
        depth = max(depth, dotted.group(1).count("."))
    return depth


def _parse_entry(raw: str, index: int, *, loose: bool) -> _TocEntry | None:
    """Read one line as a contents entry, or return None if it is not one.

    `loose` widens what counts to entries a strict scan would miss — a
    single-space page separator, or a bare title with no page number at all. It
    is only set once a "Contents" heading has already established that this run
    of lines *is* a contents page; a document-wide scan uses the strict forms so
    that ordinary prose is never mistaken for an index.
    """
    if not raw.strip() or _TABLE_DELIMITER.match(raw):
        return None
    indent = len(raw) - len(raw.lstrip(" \t"))
    text = raw.strip()
    page: str | None = None
    from_heading = False
    structured = True

    row = _TABLE_ROW.match(text)
    if row:
        cells = [cell.strip() for cell in row.group(1).split("|") if cell.strip()]
        if not cells:
            return None
        title = cells[0]
        if len(cells) > 1 and _PAGE_NUMBER.match(cells[-1]):
            page = cells[-1]
        elif not loose:
            return None
    else:
        heading = _HEADING.match(text)
        if heading:
            text = heading.group(2).strip()
            from_heading = True
        listed = bool(_LIST_MARKER.match(text))
        text = _LIST_MARKER.sub("", text)
        text = _EMPHASIS.sub("", text).strip()
        link = _ENTRY_LINK.match(text)
        matched = _ENTRY_WITH_PAGE.match(text)
        if matched is None and loose:
            matched = _ENTRY_WITH_PAGE_LOOSE.match(text)
        if link:
            title, page = link.group("label"), link.group("page")
        elif matched:
            title, page = matched.group("title"), matched.group("page")
        elif loose and len(text) <= _MAX_TITLE_CHARS:
            title, structured = text, listed
        else:
            return None

    title = _normalize_title_text(title)
    # An entry has to name something; a row of page numbers or a rule does not.
    if not title or len(title) > _MAX_TITLE_CHARS or not re.search(r"[^\W\d_]", title):
        return None
    return _TocEntry(index, title, _entry_depth(indent, title), page, from_heading, structured)


def _find_contents_heading(lines: list[str], fenced: list[bool]) -> tuple[int, int] | None:
    """Locate the document's own "Contents" heading: (line index, level)."""
    limit = max(1, int(len(lines) * _TOC_SEARCH_FRACTION))
    for index, raw in enumerate(lines[:limit]):
        if fenced[index]:
            continue
        text = raw.strip()
        if not text:
            continue
        heading = _HEADING.match(text)
        level = len(heading.group(1)) if heading else 0
        candidate = _normalize_title_text(heading.group(2) if heading else text)
        if _CONTENTS_TITLE.match(candidate.strip()):
            return index, level
    return None


def _collect_entries(
    lines: list[str], fenced: list[bool], start: int, contents_level: int
) -> list[_TocEntry]:
    """Read the run of contents entries that follows a "Contents" heading.

    Stops at the first sign the body has begun: a heading at or above the
    contents heading's own level, a bare line once the page has shown it lists
    entries with structure, a line repeating an entry already collected (the
    first chapter, reached), or three non-blank lines in a row that are not
    entries at all.

    Those middle two rules are what keep the block from running away. A chapter
    opening ("**Opening moves**") has exactly the shape of a bare contents entry,
    so without them the body would be read as more of the contents page and the
    whole document would be swallowed. A contents page that numbers its pages
    numbers all of them, and it lists each title once.
    """
    entries: list[_TocEntry] = []
    seen: list[tuple[str, str]] = []
    structured = 0
    strikes = 0
    for index in range(start, len(lines)):
        raw = lines[index]
        if fenced[index]:
            break
        if not raw.strip():
            continue
        heading = _HEADING.match(raw.strip())
        if heading and contents_level and len(heading.group(1)) <= contents_level:
            break
        if _repeats(raw, seen):
            break
        entry = _parse_entry(raw, index, loose=True)
        if entry is None:
            strikes += 1
            if strikes >= 3:
                break
            continue
        if not entry.structured and structured >= 2:
            break
        strikes = 0
        structured += 1 if entry.structured else 0
        entries.append(entry)
        seen.append(_keys(entry.title))

    # A trailing entry that was itself a heading and carried no page number is
    # far more likely to be the first chapter than the last contents line, so
    # hand it back to the body rather than swallowing it.
    while entries and entries[-1].from_heading and entries[-1].page is None:
        entries.pop()
    return entries


def _unlabelled_contents(lines: list[str], fenced: list[bool]) -> list[_TocEntry]:
    """Find a contents page that lost its heading, by its run of page numbers.

    Extraction drops the word "Contents" often enough — it is set as art, or in a
    header — that the entries themselves have to be the signal. Only the strict
    entry form counts here, and only a run of them near the front.
    """
    limit = max(1, int(len(lines) * _TOC_SEARCH_FRACTION))
    run: list[_TocEntry] = []
    gap = 0
    for index in range(min(limit, len(lines))):
        if fenced[index]:
            continue
        if not lines[index].strip():
            continue
        entry = _parse_entry(lines[index], index, loose=False)
        if entry is not None and entry.page is not None:
            run.append(entry)
            gap = 0
            continue
        gap += 1
        if gap > 2:
            if len(run) >= _MIN_TOC_ENTRIES + 1:
                return run
            run = []
            gap = 0
    return run if len(run) >= _MIN_TOC_ENTRIES + 1 else []


def _top_level(entries: list[_TocEntry]) -> list[_TocEntry]:
    """The chapter-level entries: the shallowest depth the contents page uses.

    A contents page lists sub-sections too; splitting on those would return forty
    fragments where the reader asked for six chapters.
    """
    if not entries:
        return []
    shallowest = min(entry.depth for entry in entries)
    return [entry for entry in entries if entry.depth == shallowest]


def _exact_index(candidates: list[_Anchor]) -> dict[str, list[_Anchor]]:
    """Group candidate lines by their comparison keys, for O(1) title lookup."""
    index: dict[str, list[_Anchor]] = {}
    for anchor in candidates:
        for key in {anchor.keys[0], anchor.keys[1]}:
            if key:
                index.setdefault(key, []).append(anchor)
    return index


def _match_in_body(
    entries: list[_TocEntry], candidates: list[_Anchor], after: int
) -> list[tuple[int, str, int]]:
    """Find each contents entry again in the body, in order.

    Matching is strictly forward: each entry is looked for *after* the previous
    match. That is what makes the whole approach safe — a list of titles that
    does not reappear in order below is not this document's table of contents,
    and the caller rejects it on coverage.

    Exact title matches are looked up in an index over every candidate line, so a
    chapter opening that kept no markup is still found. Only entries that fail
    that lookup fall through to the fuzzy scan, which is restricted to
    heading-like lines — comparing every entry against every line of a book would
    be both slow and far too eager to find a match.
    """
    index = _exact_index(candidates)
    heading_like = [anchor for anchor in candidates if anchor.heading_like]
    matched: list[tuple[int, str, int]] = []
    position = after
    for entry in entries:
        keys = _keys(entry.title)
        hits = [
            anchor
            for key in {keys[0], keys[1]}
            for anchor in index.get(key, ())
            if anchor.line >= position
        ]
        found = min(hits, key=lambda anchor: anchor.line) if hits else None
        if found is None:
            found = next(
                (
                    anchor
                    for anchor in heading_like
                    if anchor.line >= position and _same_title(keys, anchor.keys)
                ),
                None,
            )
        if found is not None:
            matched.append((found.line, entry.title, max(found.level, 1)))
            position = found.line + 1
    return matched


def _toc_split(lines: list[str], fenced: list[bool]) -> list[tuple[int, str, int]]:
    """Chapter starts taken from the document's contents page, or []."""
    entries: list[_TocEntry] = []
    heading = _find_contents_heading(lines, fenced)
    if heading is not None:
        entries = _collect_entries(lines, fenced, heading[0] + 1, heading[1])
    if len(entries) < _MIN_TOC_ENTRIES:
        entries = _unlabelled_contents(lines, fenced)
    if len(entries) < _MIN_TOC_ENTRIES:
        return []

    chapters = _top_level(entries)
    if len(chapters) < 2:
        return []
    candidates = _anchors(lines, fenced, wide=True)
    matched = _match_in_body(chapters, candidates, after=entries[-1].line + 1)
    if len(matched) < 2 or len(matched) < _MIN_TOC_MATCH_RATIO * len(chapters):
        return []
    return matched


# --- The fallbacks ----------------------------------------------------------
def _heading_split(anchors: list[_Anchor]) -> list[tuple[int, str, int]]:
    """Chapter starts taken from heading structure, or [].

    Picks the shallowest level that occurs more than once, unless a deeper level
    is visibly the chapter level — a document whose H2s are "Chapter 1", "Chapter
    2" and whose H1 is its title should split at the chapters, not at the title.
    """
    headings = [anchor for anchor in anchors if anchor.level > 0]
    counts = Counter(anchor.level for anchor in headings)
    levels = sorted(level for level, count in counts.items() if count >= 2)
    if not levels:
        return []
    chapterish = [
        level
        for level in levels
        if sum(1 for a in headings if a.level == level and a.marker) >= 0.6 * counts[level]
    ]
    level = chapterish[0] if chapterish else levels[0]
    return [(a.line, a.text, a.level) for a in headings if a.level == level]


def _marker_split(anchors: list[_Anchor]) -> list[tuple[int, str, int]]:
    """Chapter starts taken from unmarked "Chapter N" lines, or []."""
    marked = [a for a in anchors if a.level == 0 and a.marker]
    if len(marked) < 2:
        return []
    return [(a.line, a.text, 1) for a in marked]


# --- Assembly ---------------------------------------------------------------
def _front_matter_title(lines: list[str], fenced: list[bool], end: int) -> str:
    """Name the material before the first chapter after the document itself."""
    for index in range(min(end, len(lines))):
        if fenced[index]:
            continue
        heading = _HEADING.match(lines[index].strip())
        if heading and len(heading.group(1)) == 1:
            return _normalize_title_text(heading.group(2))
    return "Front matter"


def _build(lines: list[str], fenced: list[bool], starts: list[tuple[int, str, int]]) -> list[Chapter]:
    """Turn chapter start lines into the chapters themselves.

    Every line of the document lands in exactly one chapter, in order, so the
    split is lossless: anything above the first chapter start becomes a leading
    section rather than being thrown away.
    """
    sections: list[tuple[str, int, str]] = []
    first = starts[0][0]
    preamble = "\n".join(lines[:first])
    # A lone "# Handbook" above the first chapter is the document's title, not a
    # section of it; it opens chapter one instead of becoming an 11-byte file.
    has_front_matter = len(preamble.strip()) >= _MIN_FRONT_MATTER_CHARS
    if has_front_matter:
        sections.append((_front_matter_title(lines, fenced, first), 0, preamble))

    for position, (line, title, level) in enumerate(starts):
        end = starts[position + 1][0] if position + 1 < len(starts) else len(lines)
        body = "\n".join(lines[line:end])
        if position == 0 and not has_front_matter and preamble.strip():
            body = f"{preamble}\n{body}"
        sections.append((title, level, body))

    # Number the files so they sort in reading order in any file manager, with a
    # leading 0 for front matter so it stays ahead of chapter 1.
    width = max(2, len(str(len(sections))))
    chapters: list[Chapter] = []
    for position, (title, level, body) in enumerate(sections):
        number = position if has_front_matter else position + 1
        chapters.append(
            Chapter(
                title=title or f"Section {number}",
                level=level,
                filename=f"{number:0{width}d}-{slug(title)}.md",
                markdown=clean_markdown(body),
            )
        )
    return chapters


def _plausible(chapters: list[Chapter]) -> bool:
    """Reject a split that produced fragments rather than chapters.

    Judged on the *median* chapter, and on the chapters only — one genuinely
    short chapter is normal, and front matter is often just a title page, so
    neither should sink an otherwise good split.
    """
    lengths = sorted(len(chapter.markdown) for chapter in chapters if chapter.level > 0)
    if not 2 <= len(lengths) <= _MAX_CHAPTERS:
        return False
    return lengths[len(lengths) // 2] >= _MIN_MEDIAN_CHAPTER_CHARS


def split_into_chapters(markdown: str) -> ChapterSplit:
    """Split extracted Markdown into chapters.

    Args:
        markdown: A converted document, already normalized by `clean_markdown`.

    Returns:
        A `ChapterSplit`. When no chapter structure is found — the ordinary case
        for an article or a one-page form — `method` is `"none"` and `chapters`
        is empty; the caller keeps the document whole rather than inventing
        boundaries for it.
    """
    if not markdown or not markdown.strip():
        return ChapterSplit("none", ())

    lines = markdown.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    fenced = _fence_mask(lines)
    anchors = _anchors(lines, fenced)

    for method, starts in (
        ("toc", _toc_split(lines, fenced)),
        ("headings", _heading_split(anchors)),
        ("markers", _marker_split(anchors)),
    ):
        if len(starts) < 2:
            continue
        chapters = _build(lines, fenced, starts)
        if _plausible(chapters):
            return ChapterSplit(method, tuple(chapters))
    return ChapterSplit("none", ())
