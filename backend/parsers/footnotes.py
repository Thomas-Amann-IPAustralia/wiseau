"""Footnotes and their markers (roadmap 3.5, ADR-053).

A footnote is a note at the foot of a page (or column) tied to a marker in the
text. Engines give two unrelated blocks: running text with `<sup>2</sup>` in it,
and a run of numbered lines somewhere below. This pass ties them together:

1. **Markers.** On text-layer pages PyMuPDF4LLM writes a superscript as
   `<sup>2</sup>` (or `<sup>2,4</sup>`): an explicit marker. A scan has no such
   signal (OCR reads a marker as a digit glued to its word, `Argentina2`, which
   cannot be told from a number in the text), so on OCR'd pages the pass does
   nothing. It refuses rather than guesses.
2. **Notes.** A block on the same page whose lines start with marker numbers,
   in increasing order, each number one the page's markers use, at least
   `MIN_NOTES` of them, and every one of its lines belonging to a note (the
   first line starts with a number). Such a block is split at the lines that
   start a note, into one FOOTNOTE element per note. Splitting moves whole
   lines: each note's text is the block's lines, unchanged, and the notes
   together are exactly the block.
3. **Links.** Each note gets a `footnote_for` relation to every block on the
   page whose markers name it (`signal="footnote-marker"`).

The serializer writes a linked note as `[^n]: ...` and its markers as `[^n]`
(the `<sup>` markup the engine wrote is replaced by Markdown's footnote syntax;
the number itself is unchanged). A page whose notes and markers do not agree is
left alone.
"""

from __future__ import annotations

import re
from dataclasses import replace

from . import document as ir
from . import recover

SIGNAL = "footnote-marker"
MIN_NOTES = 2

MARKER = re.compile(r"<sup>\s*(\d{1,2}(?:\s*,\s*\d{1,2})*)\s*</sup>")
_NOTE_START = re.compile(r"^\s*(\d{1,2})[.)]?\s+\S")


def markers(text: str) -> list[int]:
    out = []
    for m in MARKER.finditer(text):
        out.extend(int(n) for n in re.split(r"\s*,\s*", m.group(1)))
    return out


def _split_notes(el: ir.Element, known: set[int]) -> list[tuple[int, str]] | None:
    """The block as [(number, text)] notes, or None when it is not a run of notes."""
    lines = el.text.split("\n")
    notes: list[tuple[int, list[str]]] = []
    last = 0
    for line in lines:
        m = _NOTE_START.match(line)
        n = int(m.group(1)) if m else None
        if n is not None and n in known and n > last:
            notes.append((n, [line]))
            last = n
        elif notes:
            notes[-1][1].append(line)
        else:
            return None  # text before the first note: not a notes block
    if len(notes) < MIN_NOTES:
        return None
    return [(n, "\n".join(part)) for n, part in notes]


def apply(doc: ir.Document) -> None:
    for page, indices in recover.page_groups(doc):
        info = next((p for p in doc.pages if p.number == page), None)
        if info is None or info.method != ir.TEXT_LAYER:
            continue
        cited: dict[int, list[str]] = {}  # note number -> ids of blocks citing it
        for i in indices:
            for n in markers(doc.elements[i].text):
                cited.setdefault(n, [])
                if doc.elements[i].id not in cited[n]:
                    cited[n].append(doc.elements[i].id)
        if not cited:
            continue
        for i in reversed(indices):  # reversed: splitting shifts later indices
            el = doc.elements[i]
            if el.type not in recover.BODY_TYPES and el.type != ir.FOOTNOTE:
                continue
            if markers(el.text):
                continue  # a block that cites notes is not the notes
            notes = _split_notes(el, set(cited))
            if notes is None:
                continue
            parts = [replace(el, type=ir.FOOTNOTE, text=text, role_signal=SIGNAL, id=f"{el.id}.{k}")
                     for k, (_, text) in enumerate(notes, 1)]
            doc.elements[i:i + 1] = parts
            for (n, _), part in zip(notes, parts):
                for target in cited.get(n, []):
                    doc.relations.append(ir.Relation("footnote_for", part.id, target, SIGNAL))
            break  # one notes block per page
