"""Tests for the shared filename rule (`parsers/naming.py`).

Filenames are the part of a batch (ADR-031) a user actually keeps: they land in
a folder, sorted next to files the user already had. Three things have to hold —
they must be recognizably the document that was uploaded, they must be unique
within one archive, and they must never carry a path.
"""

from __future__ import annotations

from parsers.naming import markdown_filename, slug, unique_filenames


# --- Slugging ---------------------------------------------------------------
def test_a_name_survives_recognizably():
    assert markdown_filename("Annual Report 2025.pdf") == "annual-report-2025.md"


def test_the_extension_is_replaced_not_appended():
    assert markdown_filename("minutes.docx") == "minutes.md"
    assert markdown_filename("scan.PDF") == "scan.md"


def test_a_document_with_no_extension_still_gets_one():
    assert markdown_filename("README") == "readme.md"


def test_non_latin_titles_keep_their_own_characters():
    # `\w` is Unicode-aware, so a document named in another script stays named in
    # it rather than collapsing to the fallback.
    assert markdown_filename("Отчёт 2025.pdf") == "отчёт-2025.md"


def test_a_name_with_no_word_characters_falls_back():
    assert markdown_filename("???.pdf") == "document.md"
    assert slug("***") == "section"
    assert slug("***", fallback="document") == "document"


def test_a_very_long_name_is_bounded():
    name = markdown_filename(f"{'x' * 400}.pdf")
    assert len(name) <= 63  # 60-character stem + ".md"
    assert name.endswith(".md")


# --- The path-traversal property --------------------------------------------
# The uploader chooses this string and the client writes the result under it, so
# it must be a bare filename whatever arrives.
def test_a_path_in_the_uploaded_name_cannot_escape():
    assert markdown_filename("../../etc/passwd") == "passwd.md"
    assert markdown_filename("/absolute/secrets.pdf") == "secrets.md"
    assert markdown_filename("C:\\Windows\\System32\\hosts") == "hosts.md"


def test_a_name_that_is_only_traversal_still_yields_a_filename():
    assert markdown_filename("../../..") == "document.md"


def test_no_suggested_name_contains_a_separator():
    for awkward in ("a/b.pdf", "a\\b.pdf", "..", "a:b.pdf", "tab\tname.pdf"):
        name = markdown_filename(awkward)
        assert "/" not in name and "\\" not in name and ":" not in name
        assert not name.startswith(".")


# --- Uniqueness within a batch ----------------------------------------------
def test_duplicate_uploads_get_distinct_names():
    assert unique_filenames(["report.pdf", "report.pdf", "report.pdf"]) == [
        "report.md",
        "report-2.md",
        "report-3.md",
    ]


def test_names_that_only_differ_by_case_or_punctuation_still_collide():
    # They slug identically, so without this one would overwrite the other in the
    # user's folder — losing a conversion silently, which is the failure mode
    # worth a test.
    assert unique_filenames(["Report.pdf", "REPORT.docx", "report!.pdf"]) == [
        "report.md",
        "report-2.md",
        "report-3.md",
    ]


def test_a_suffix_that_collides_with_a_real_document_moves_on():
    assert unique_filenames(["report.pdf", "report-2.pdf", "report.pdf"]) == [
        "report.md",
        "report-2.md",
        "report-3.md",
    ]


def test_naming_is_deterministic():
    sources = ["b.pdf", "a.pdf", "b.pdf", "Ünïcode ☃.pdf"]
    assert unique_filenames(sources) == unique_filenames(sources)


def test_distinct_documents_keep_their_own_names():
    assert unique_filenames(["a.pdf", "b.docx"]) == ["a.md", "b.md"]
