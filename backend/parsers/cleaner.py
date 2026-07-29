"""Deterministic Markdown post-processing.

Strips residual noise, normalizes Unicode and quote characters, collapses
excess whitespace, and trims trailing spaces so that identical input reliably
yields byte-identical output.

It also drops **base64 data-URI payloads** (ADR-024). Several extractors inline
images directly into the Markdown — a single screenshot then becomes tens of
thousands of characters of unreadable base64 that dwarfs the document it
illustrates. The payload is machine data, not Markdown, so the shared normalizer
removes it while keeping the surrounding structure (and the media type) intact.
"""

from __future__ import annotations

import re
import unicodedata

# Common "smart" characters mapped to plain ASCII equivalents.
_CHAR_REPLACEMENTS = {
    "‘": "'",
    "’": "'",
    "“": '"',
    "”": '"',
    "–": "-",
    "—": "--",
    "…": "...",
    " ": " ",  # non-breaking space
    "​": "",   # zero-width space
    "﻿": "",   # byte-order mark
}

_MULTI_BLANK_LINE = re.compile(r"\n{3,}")
_TRAILING_WS = re.compile(r"[ \t]+$", re.MULTILINE)

# A base64 data URI, captured as (prefix, payload). The prefix keeps the media
# type — `data:image/png;base64,` — because *that an image was here* is real
# information about the document; the payload after it is not. Matches wherever
# the URI appears: a Markdown image, an HTML `src=`/`href=`, or bare text.
_DATA_URI_PAYLOAD = re.compile(r"(data:[A-Za-z0-9!#$&^_.+-]*/?[A-Za-z0-9!#$&^_.+-]*;base64,)[A-Za-z0-9+/=]+")

# What replaces the payload. Short, obviously elided, and stable across runs.
_ELIDED_PAYLOAD = "..."


def strip_data_uri_payloads(text: str) -> str:
    """Elide the base64 payload of every data URI, keeping its media type.

    `![Figure 1](data:image/png;base64,iVBORw0KGgo...<40 000 more chars>)`
    becomes `![Figure 1](data:image/png;base64,...)`. The document still says an
    image was there — and what kind — without carrying an unreadable blob that
    can be orders of magnitude larger than the text around it (ADR-024).

    Deliberately payload-only: no image, link, or paragraph is removed, so this
    stays a normalization rather than a content edit (invariant #3), and it is
    exact (no length heuristics), so it is deterministic.
    """
    return _DATA_URI_PAYLOAD.sub(lambda m: m.group(1) + _ELIDED_PAYLOAD, text)


def clean_markdown(text: str) -> str:
    """Normalize extracted Markdown into a uniform, deterministic form."""
    if not text:
        return ""

    # Drop inlined base64 blobs before anything else touches them: they are the
    # bulk of the string in an image-heavy document, and no other rule here has
    # any business rewriting machine payload.
    text = strip_data_uri_payloads(text)

    # Normalize Unicode composition and line endings.
    text = unicodedata.normalize("NFC", text)
    text = text.replace("\r\n", "\n").replace("\r", "\n")

    # Replace typographic characters with plain equivalents.
    for src, dst in _CHAR_REPLACEMENTS.items():
        text = text.replace(src, dst)

    # Collapse runs of blank lines and strip trailing whitespace.
    text = _TRAILING_WS.sub("", text)
    text = _MULTI_BLANK_LINE.sub("\n\n", text)

    # Guarantee a single trailing newline.
    return text.strip() + "\n"
