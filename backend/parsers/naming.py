"""Filenames for extracted Markdown.

One slug rule, shared by everything in the engine that suggests a `.md` file:
the chapters of a split document (ADR-030) and the documents of a batch
conversion (ADR-031). A caller saving files should see the same naming wherever
they came from, and there should be one place to change it.

Two properties matter beyond looking tidy:

* **Deterministic** (invariant #1) — the same title always slugs to the same
  stem, and a batch's duplicate names always disambiguate the same way, so two
  identical requests produce byte-identical archives.
* **Path-free** — a suggested filename is written to disk (or into a ZIP entry)
  by whatever client asked for it, and for a batch the input is the *uploader's*
  filename. Directory components are dropped and every non-word character
  becomes a hyphen, so nothing here can name a path outside the caller's target
  directory.
"""

from __future__ import annotations

import re
from typing import Iterable

_SLUG_SEPARATOR = re.compile(r"[^\w]+", re.UNICODE)
_EXTENSION = re.compile(r"\.[A-Za-z0-9]{1,5}$")

# Long enough to stay recognizable, short enough that a numbered prefix and a
# suffix still fit inside every filesystem's per-name limit.
_MAX_STEM_CHARS = 60


def slug(title: str, fallback: str = "section") -> str:
    """A filename stem from a title: lowercase, hyphenated, bounded.

    Args:
        title: Any human-facing title — a chapter heading, an uploaded filename.
        fallback: Stem to use when `title` has no word characters at all (an
            emoji, punctuation, or the empty string).
    """
    stem = _SLUG_SEPARATOR.sub("-", title.strip().lower()).strip("-")
    return stem[:_MAX_STEM_CHARS].strip("-") or fallback


def markdown_filename(source: str) -> str:
    """The `.md` file an uploaded document should be saved as.

    `Reports/Annual Report 2025.pdf` becomes `annual-report-2025.md`: the
    directory part is dropped, the original extension is replaced, and the rest
    goes through `slug`, so the name is recognizably the document the user
    uploaded rather than an opaque index.
    """
    base = source.replace("\\", "/").rsplit("/", 1)[-1]
    return f"{slug(_EXTENSION.sub('', base), fallback='document')}.md"


def unique_filenames(sources: Iterable[str]) -> list[str]:
    """Names for a set of documents, deduplicated in order.

    Two uploads called `report.pdf` — or `report.pdf` and `Report.PDF`, which
    slug identically — would otherwise be one entry in the archive, silently
    losing a conversion the user paid for. The first keeps the plain name and
    each repeat is suffixed in upload order (`report.md`, `report-2.md`), which
    depends only on that order and so stays deterministic.
    """
    names: list[str] = []
    taken: set[str] = set()
    for source in sources:
        candidate = markdown_filename(source)
        stem = candidate[: -len(".md")]
        # Counting up past names already taken also covers the awkward case
        # where the suffix collides with a document genuinely called
        # `report-2.pdf`: it simply moves on to `report-3.md`.
        suffix = 1
        while candidate in taken:
            suffix += 1
            candidate = f"{stem}-{suffix}.md"
        taken.add(candidate)
        names.append(candidate)
    return names
