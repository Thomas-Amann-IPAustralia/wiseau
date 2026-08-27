"""Tests for keyword extraction and its fusion (ADR-032).

Keyword extraction has no ground truth, so these tests pin the things that are
*checkable* rather than asserting a particular ranking of a particular document:

* the **contract** — a method this build cannot run is skipped and reported, not
  raised; a document too short to characterise returns nothing and says why;
* the **fusion** — agreement across methods beats one method's confidence, and
  the four methods' phrasings of one idea merge instead of competing;
* **determinism** (invariant #1) — the same Markdown always yields the same
  keywords, weights and order;
* the **annotation** — prepending the table twice replaces it rather than
  stacking it, and extracting from an annotated document analyses the document.

The optional methods (`spacy`, `keybert`) are skipped when not installed, which
is also the degradation the endpoint promises for a lean deployment.
"""

from __future__ import annotations

import pytest

from parsers import keywords as kw
from parsers.keywords import (
    KEYWORD_METHODS,
    canonical,
    extract_keywords,
    keyword_table,
    prepend_keyword_table,
    resolve_methods,
    strip_keyword_block,
)

# A document with a subject, repeated terms, a title, headings and a few named
# entities — enough for every method to have an opinion about it.
REPORT = """# Coastal Inundation and Adaptation Funding in the Pacific

## Executive summary

Climate change is accelerating coastal inundation across the Pacific island
states. Adaptation funding has not kept pace with the observed rate of sea level
rise, and the Green Climate Fund disbursed less than a third of its committed
adaptation funding in the last reporting period.

## Findings

The review of the National Adaptation Plan found that coastal inundation now
affects settlements that were previously considered safe. Adaptation funding
decisions are made without reference to the inundation modelling produced by the
Bureau of Meteorology.

Climate resilience investment is concentrated in urban centres. Rural
settlements, where coastal inundation is most severe, receive a small share of
adaptation funding.

## Recommendations

Adaptation funding should be allocated against inundation modelling, and the
Green Climate Fund should report disbursement against commitment.
"""


def _terms(result) -> list[str]:
    return [keyword.term.lower() for keyword in result.keywords]


def _available(method: str) -> bool:
    return method in kw.available_methods()


requires_spacy = pytest.mark.skipif(not _available("spacy"), reason="spaCy is not installed")
requires_keybert = pytest.mark.skipif(not _available("keybert"), reason="KeyBERT is not installed")
requires_yake = pytest.mark.skipif(not _available("yake"), reason="YAKE is not installed")


# --- The always-available floor ---------------------------------------------
def test_the_builtin_method_needs_nothing_and_always_answers():
    """`frequency` is the guarantee that a lean deployment still extracts."""
    result = extract_keywords(REPORT, methods=["frequency"], top_k=10)

    assert result.methods_used == ("frequency",)
    assert result.methods_skipped == {}
    assert "adaptation funding" in _terms(result)


def test_a_term_in_the_title_outranks_an_equally_common_one_that_is_not():
    """Structure is a signal no bag-of-words count can recover afterwards."""
    document = (
        "# Ledger Reconciliation\n\n"
        + "The ledger reconciliation was late. " * 4
        + "The tuesday meeting was late. " * 4
        + "Nobody in the office admitted to having touched the ledger that quarter.\n"
    )
    result = extract_keywords(document, methods=["frequency"], top_k=10)
    terms = _terms(result)

    assert terms.index("ledger reconciliation") < terms.index("tuesday meeting")


def test_a_term_seen_once_and_never_in_a_heading_is_not_a_keyword():
    result = extract_keywords(REPORT, methods=["frequency"], top_k=40)

    assert "urban centres" not in _terms(result)


def test_candidate_phrases_never_span_a_stopword():
    """A phrase that runs across "and"/"in" is a line of the document, not a term."""
    result = extract_keywords(REPORT, methods=["frequency"], top_k=40)

    for term in _terms(result):
        assert " and " not in term
        assert " in the " not in term


def test_a_code_fence_is_not_prose():
    document = (
        "# Deployment Notes\n\n"
        + "The deployment notes describe the rollout procedure. " * 4
        + "\n\n```\nkubectl kubectl kubectl kubectl kubectl kubectl\n```\n"
    )
    result = extract_keywords(document, methods=["frequency"], top_k=10)

    assert "kubectl" not in _terms(result)


# --- Refusing rather than guessing ------------------------------------------
def test_a_document_too_short_to_characterise_returns_nothing_and_says_why():
    result = extract_keywords("# Notice\n\nThe office is closed on Friday.\n")

    assert result.keywords == ()
    assert "too short" in result.note.lower()


def test_an_empty_document_is_not_an_error():
    result = extract_keywords("")

    assert result.keywords == ()
    assert result.note


# --- Method selection --------------------------------------------------------
def test_an_unknown_method_is_a_caller_error():
    with pytest.raises(ValueError) as excinfo:
        resolve_methods(["yake", "tf-idf"])

    assert "tf-idf" in str(excinfo.value)


@pytest.mark.parametrize("requested", [None, [], ["auto"], ["default"]])
def test_deferring_to_the_deployment_default_is_distinct_from_naming_methods(requested):
    assert resolve_methods(requested) is None


def test_methods_come_back_in_canonical_order_however_they_were_asked_for():
    assert resolve_methods(["keybert", "frequency", "yake"]) == ["frequency", "yake", "keybert"]
    assert resolve_methods(["all"]) == list(KEYWORD_METHODS)


def test_a_method_this_build_cannot_run_is_reported_not_raised(monkeypatch):
    """ADR-014's rule: degrade to a working answer rather than refusing one."""
    kw._method_availability.cache_clear()
    monkeypatch.setattr(
        kw,
        "_method_availability",
        lambda: {"frequency": "", "yake": "", "spacy": "spacy is not installed", "keybert": ""},
    )
    result = extract_keywords(REPORT, methods=["frequency", "spacy"], top_k=5)

    assert "spacy" in result.methods_skipped
    assert result.methods_used == ("frequency",)
    assert result.keywords, "the methods that could run still answered"


def test_a_method_that_raises_costs_only_itself(monkeypatch):
    def explode(*args, **kwargs):
        raise RuntimeError("model file is corrupt")

    monkeypatch.setattr(kw, "_run_yake", explode)
    result = extract_keywords(REPORT, methods=["frequency", "yake"], top_k=5)

    assert result.methods_used == ("frequency",)
    assert "model file is corrupt" in result.methods_skipped["yake"]
    assert result.keywords


def test_a_misconfigured_deployment_still_has_the_builtin_method(monkeypatch):
    monkeypatch.setenv("WISEAU_KEYWORD_METHODS", "nonsense")

    assert kw.default_methods() == ["frequency"]


# --- Fusion ------------------------------------------------------------------
@requires_yake
def test_agreement_between_methods_beats_one_methods_confidence():
    """The whole point of running several: a term two methods found wins."""
    result = extract_keywords(REPORT, methods=["frequency", "yake"], top_k=10)
    top = result.keywords[0]

    assert top.agreement == 2
    assert set(top.methods) == {"frequency", "yake"}
    assert top.score == 1.0


@requires_yake
def test_every_keyword_carries_the_rank_and_native_score_each_method_gave_it():
    result = extract_keywords(REPORT, methods=["frequency", "yake"], top_k=10)

    for keyword in result.keywords:
        assert keyword.agreement == len(keyword.methods)
        for name, evidence in keyword.methods.items():
            assert name in KEYWORD_METHODS
            assert evidence.rank >= 1
            assert isinstance(evidence.score, float)


@requires_yake
def test_weights_descend_from_the_top_ranked_term():
    result = extract_keywords(REPORT, methods=["frequency", "yake"], top_k=10)
    scores = [keyword.score for keyword in result.keywords]

    assert scores[0] == 1.0
    assert scores == sorted(scores, reverse=True)
    assert all(0.0 < score <= 1.0 for score in scores)
    assert [keyword.rank for keyword in result.keywords] == list(range(1, len(scores) + 1))


def test_two_phrasings_of_one_idea_are_one_keyword():
    """"Climate Change" and "climate changes" must not compete for two rows."""
    assert canonical("The Climate Changes,") == canonical("climate change")
    assert canonical("Adaptation Funding") == canonical("adaptation funding")


def test_a_unigram_that_only_ever_occurs_inside_a_phrase_is_dropped():
    document = (
        "# Inundation Modelling\n\n"
        + "The inundation modelling was revised. " * 5
        + "Nobody in the office admitted to having touched the ledger that quarter.\n"
    )
    terms = _terms(extract_keywords(document, methods=["frequency"], top_k=20))

    assert "inundation modelling" in terms
    assert "inundation" not in terms


def test_a_word_that_also_stands_alone_keeps_its_own_row():
    document = (
        "# Adaptation\n\n"
        + "Adaptation funding was cut. " * 3
        + "Adaptation is a policy, adaptation is a process, and adaptation is slow. "
        + "Nobody in the office admitted to having touched the ledger that quarter.\n"
    )
    terms = _terms(extract_keywords(document, methods=["frequency"], top_k=20))

    assert "adaptation" in terms


def test_occurrences_are_counted_and_do_not_span_punctuation():
    document = (
        "# Ledger Audit\n\n"
        + "The ledger audit found nothing. " * 3
        + "The auditors closed the ledger. Audit findings were filed. "
        + "Nobody in the office admitted to having touched the ledger that quarter.\n"
    )
    result = extract_keywords(document, methods=["frequency"], top_k=20)
    found = {keyword.term.lower(): keyword.occurrences for keyword in result.keywords}

    # "the ledger. Audit findings" must not read as an occurrence of "ledger audit".
    assert found["ledger audit"] == 4


def test_a_reports_own_section_names_do_not_ride_on_being_headings():
    """"Executive summary" is true of every report and says nothing about one.

    Structural terms get no heading prominence, so they need genuine repetition
    in the body to rank — which a document actually *about* its recommendations
    would supply.
    """
    document = (
        "# Adaptation Funding Review\n\n"
        "## Executive summary\n\n"
        + "Adaptation funding has not kept pace with coastal inundation. " * 4
        + "\n\n## Findings\n\n"
        + "Coastal inundation affects settlements considered safe. " * 4
        + "\n"
    )
    terms = _terms(extract_keywords(document, methods=["frequency"], top_k=15))

    assert "executive summary" not in terms
    assert "findings" not in terms
    assert "adaptation funding" in terms


def test_a_structural_term_the_document_really_is_about_still_ranks():
    document = (
        "# Audit\n\n"
        + "The recommendations were ignored. Each recommendation was costed. "
        "Recommendations from the previous audit remain open, and the "
        "recommendations register was never maintained. "
        + "The auditors closed the ledger without comment. " * 2
        + "\n"
    )
    terms = _terms(extract_keywords(document, methods=["frequency"], top_k=15))

    assert "recommendations" in terms


def test_a_phrase_that_spans_a_line_wrap_is_still_one_phrase():
    """Extracted PDF text is hard-wrapped; a wrap is not a phrase boundary.

    Found by driving the UI against a real PDF: "the Green\\nClimate Fund"
    counted zero occurrences, so `frequency` never proposed the one named
    entity the document was about.
    """
    document = (
        "Coastal Adaptation Report\n\n"
        + (
            "The Green\nClimate Fund disbursed a third of the money. "
            "The Green Climate Fund reported late. "
        )
        * 3
        + "\n\nNobody in the office admitted to having touched the ledger that quarter.\n"
    )
    result = extract_keywords(document, methods=["frequency"], top_k=20)
    found = {keyword.term.lower(): keyword.occurrences for keyword in result.keywords}

    assert "green climate fund" in found
    assert found["green climate fund"] == 6


def test_an_unpunctuated_block_does_not_run_into_the_one_below_it():
    """A heading-shaped line is a fragment, not the first clause of the paragraph.

    Also found by driving the UI: a PDF that extracted with no heading markup
    produced "Pacific Executive summary" as a top keyword, because a blank line
    is not a sentence boundary to the tokenizers these methods use.
    """
    document = (
        "Adaptation Funding in the Pacific\n\n"
        "Executive summary\n\n"
        + "Climate change is accelerating across the region. " * 4
        + "\n\nNobody in the office admitted to having touched the ledger that quarter.\n"
    )
    terms = _terms(extract_keywords(document, methods=["frequency"], top_k=25))

    assert not any("pacific executive" in term for term in terms)
    assert not any("summary climate" in term for term in terms)


# --- Determinism (invariant #1) ---------------------------------------------
@requires_yake
def test_the_same_document_always_yields_the_same_keywords():
    first = extract_keywords(REPORT, methods=["frequency", "yake"], top_k=15)
    second = extract_keywords(REPORT, methods=["frequency", "yake"], top_k=15)

    assert [(k.term, k.score, k.rank, k.occurrences) for k in first.keywords] == [
        (k.term, k.score, k.rank, k.occurrences) for k in second.keywords
    ]
    assert first.methods_used == second.methods_used


def test_a_term_is_displayed_as_the_document_writes_it_not_as_its_key():
    """Stemming is for matching; a reader must not be shown "analytic"."""
    document = (
        "# Analytics Review\n\n"
        + "The analytics review covered every dashboard. " * 4
        + "Nobody in the office admitted to having touched the ledger that quarter.\n"
    )
    terms = [keyword.term for keyword in extract_keywords(document, methods=["frequency"], top_k=10).keywords]

    assert any(term.lower().startswith("analytics") for term in terms)


# --- The optional methods ----------------------------------------------------
@requires_spacy
def test_spacy_contributes_named_entities():
    result = extract_keywords(REPORT, methods=["spacy"], top_k=25)
    entities = [keyword.term for keyword in result.keywords if keyword.kind == "entity"]

    assert entities, "spaCy found no named entities in a document full of them"
    assert result.methods_used == ("spacy",)


@requires_keybert
def test_keybert_scores_the_multi_word_candidates_the_others_pooled():
    """A regression guard: KeyBERT's default vectorizer drops these silently."""
    result = extract_keywords(REPORT, methods=["frequency", "keybert"], top_k=15)
    ranked_by_keybert = [k.term.lower() for k in result.keywords if "keybert" in k.methods]

    assert result.methods_used == ("frequency", "keybert")
    assert any(" " in term for term in ranked_by_keybert), "keybert ranked only single words"


@requires_keybert
def test_keybert_is_deterministic():
    first = extract_keywords(REPORT, methods=["keybert"], top_k=8)
    second = extract_keywords(REPORT, methods=["keybert"], top_k=8)

    assert [(k.term, k.score) for k in first.keywords] == [(k.term, k.score) for k in second.keywords]


# --- The Markdown table ------------------------------------------------------
def test_the_table_names_its_columns_its_methods_and_its_weighting():
    table = keyword_table(extract_keywords(REPORT, methods=["frequency"], top_k=5))

    assert "## Keywords" in table
    assert "| Keyword | Weight | Found by |" in table
    assert "frequency" in table
    assert "relative to the top-ranked term" in table


def test_an_empty_result_renders_its_reason_rather_than_an_empty_table():
    table = keyword_table(extract_keywords("# Notice\n\nClosed Friday.\n"))

    assert "|" not in table
    assert "too short" in table.lower()


def test_a_pipe_in_a_term_cannot_break_the_table(monkeypatch):
    document = (
        "# Cost | Benefit\n\n"
        + "The cost | benefit split was contested. " * 4
        + "Nobody in the office admitted to having touched the ledger that quarter.\n"
    )
    table = keyword_table(extract_keywords(document, methods=["frequency"], top_k=5))

    for row in [line for line in table.split("\n") if line.startswith("|")]:
        assert row.replace("\\|", "").count("|") == 4


# --- Annotating the document -------------------------------------------------
def test_the_table_goes_under_the_documents_own_title():
    annotated = prepend_keyword_table(REPORT, extract_keywords(REPORT, methods=["frequency"], top_k=3))
    lines = [line for line in annotated.split("\n") if line.strip()]

    assert lines[0] == "# Coastal Inundation and Adaptation Funding in the Pacific"
    assert lines[1] == "<!-- wiseau:keywords -->"
    assert "## Keywords" in annotated


def test_a_document_that_opens_with_prose_gets_the_table_at_the_very_top():
    document = "Some prose that opens the document.\n\n" + REPORT
    annotated = prepend_keyword_table(document, extract_keywords(REPORT, methods=["frequency"], top_k=3))

    assert annotated.startswith("<!-- wiseau:keywords -->")


def test_annotating_twice_replaces_the_table_rather_than_stacking_it():
    result = extract_keywords(REPORT, methods=["frequency"], top_k=3)
    once = prepend_keyword_table(REPORT, result)
    twice = prepend_keyword_table(once, result)

    assert twice == once
    assert twice.count("## Keywords") == 1


def test_extracting_from_an_annotated_document_analyses_the_document():
    plain = extract_keywords(REPORT, methods=["frequency"], top_k=8)
    annotated = prepend_keyword_table(REPORT, plain)
    again = extract_keywords(annotated, methods=["frequency"], top_k=8)

    assert [k.term for k in again.keywords] == [k.term for k in plain.keywords]


def test_the_block_is_well_formed_markdown_around_its_markers():
    """A renderer that joins adjacent lines must not absorb the closing marker.

    Found by driving the UI: with the caption and `<!-- /wiseau:keywords -->` on
    consecutive lines, the preview rendered the marker as the caption's last
    words — the one thing a comment must never do.
    """
    annotated = prepend_keyword_table(REPORT, extract_keywords(REPORT, methods=["frequency"], top_k=3))
    lines = annotated.split("\n")

    opened = lines.index("<!-- wiseau:keywords -->")
    closed = lines.index("<!-- /wiseau:keywords -->")
    assert lines[opened + 1] == ""
    assert lines[closed - 1] == ""


def test_the_document_survives_annotation_intact():
    annotated = prepend_keyword_table(REPORT, extract_keywords(REPORT, methods=["frequency"], top_k=3))

    assert strip_keyword_block(annotated).strip() == REPORT.strip()
