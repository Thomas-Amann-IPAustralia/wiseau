"""Deterministic Markdown post-processing.

Strips residual noise, normalizes Unicode and quote characters, collapses
excess whitespace, and trims trailing spaces so that identical input reliably
yields byte-identical output.
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


def clean_markdown(text: str) -> str:
    """Normalize extracted Markdown into a uniform, deterministic form."""
    if not text:
        return ""

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
