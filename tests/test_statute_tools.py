from pathlib import Path

import pytest

from lease_clause_checker import elaws, quotediff
from lease_clause_checker.statute import Section
from lease_clause_checker.statute_tools import (
    SCOPE,
    Statutes,
    UnknownActError,
    UnknownSectionError,
    split_citation,
    summary,
)


@pytest.fixture
def statutes(cache_dir: Path) -> Statutes:
    return Statutes.load(cache_dir)


def test_a_section_comes_with_its_link_consolidation_and_notice(statutes: Statutes) -> None:
    result = statutes.get_section("RTA", "2.1")

    assert (result.section, result.heading) == ("2.1", "Gadget deposits")
    assert result.text.endswith("(2) The deposit shall not exceed one month’s rent.")
    assert result.url.startswith(elaws.PUBLIC_URL + "#")
    assert (result.subsection, result.subsection_text) == (None, None)
    assert result.source.act == "Sample Act"
    assert result.source.consolidation == "Consolidation Period: From September 21, 2026."
    assert (result.source.retrieved, result.source.sha256) == ("2026-11-08", "0" * 64)
    assert "King's Printer for Ontario" in result.source.notice


def test_a_cited_subsection_is_also_returned_on_its_own(statutes: Statutes) -> None:
    result = statutes.get_section("Residential Tenancies Act, 2006", "s. 2.1(2)")

    assert (result.subsection, result.subsection_text) == (
        "(2)",
        "(2) The deposit shall not exceed one month’s rent.",
    )


@pytest.mark.parametrize(
    ("section", "error"),
    [
        ("9", "no in-force section 9"),
        ("s. 2.1(5)", "Section 2.1 has no in-force subsection"),
        ("", "doesn't name a section"),
        ("ss. 27-28", "doesn't name a section"),
    ],
)
def test_an_unknown_section_or_subsection_is_an_error(
    statutes: Statutes, section: str, error: str
) -> None:
    with pytest.raises(UnknownSectionError, match=error):
        statutes.get_section("RTA", section)


@pytest.mark.parametrize("act", ["Condominium Act, 1998", ""])
def test_only_the_residential_tenancies_act_is_served(statutes: Statutes, act: str) -> None:
    with pytest.raises(UnknownActError, match='call it "RTA"'):
        statutes.get_section(act, "1")
    with pytest.raises(UnknownActError):
        statutes.verify_quote(act, "1", "A provision in a tenancy agreement")


@pytest.mark.parametrize(
    ("section", "quote", "category", "summary"),
    [
        (
            "1",
            "A provision in a tenancy agreement prohibiting widgets is void.",
            "verbatim",
            "Word for word in s. 1.",
        ),
        (
            "1",
            "a provision in a tenancy agreement prohibiting widgets",
            "conventions",
            "In s. 1 if its ellipses, brackets or changed first capital are read as marking",
        ),
        (
            "s. 2.1(2)",
            "exceed one month’s rent.",
            "drops_negation",
            "Word for word in s. 2.1, but it leaves out the “not” right before it.",
        ),
        (
            "2.1",
            "A landlord may require a gadget deposit if the tenant agrees in person",
            "content_words_differ",
            "Not word for word in s. 2.1: its wording is closest to s. 2.1(1), but content words "
            "differ.",
        ),
        (
            "4",
            "A provision in a tenancy agreement prohibiting widgets is void.",
            "wrong_cite",
            "Not in s. 4; the words are from s. 1.",
        ),
        (
            "9",
            "A landlord may hold the tenant's keys forever and ever.",
            "not_in_act",
            "s. 9 is not an in-force section of the Act, and the quote is close to no other.",
        ),
        ("1", "", "no_quote", "There is no quote to check."),
        ("1", "“No widget” provisions void", "heading", "Found only in a heading of s. 1,"),
        ("4", "binds the Crown", "too_short", "Too short to check: under 5 words."),
        (
            "1",
            "A provision in the tenancy agreement prohibiting widgets is void.",
            "cosmetic",
            "Not word for word in s. 1, but it differs only in articles, punctuation,",
        ),
        (
            "4",
            "A provision in a tenancy agreement prohibiting gizmos is void.",
            "wrong_cite",
            "Not in s. 4; it is closest to s. 1 instead, but content words differ.",
        ),
        (
            "1",
            "The landlord may keep any pet that the tenant chooses.",
            "not_in_act",
            "Not in s. 1, and close to no subsection of the Act.",
        ),
        (
            "1",
            "A provision in a tenancy agreement prohibiting gizmos is void.",
            "content_words_differ",
            "Not word for word in s. 1: content words differ.",  # no subsections to name
        ),
    ],
)
def test_verify_quote_sorts_and_explains_the_quote(
    statutes: Statutes, section: str, quote: str, category: str, summary: str
) -> None:
    result = statutes.verify_quote("rta", section, quote)

    assert result.category == category
    assert result.summary.startswith(summary)
    assert result.verbatim == (category == "verbatim")
    assert result.scope == SCOPE


def test_verify_quote_gives_the_word_level_diff(statutes: Statutes) -> None:
    result = statutes.verify_quote(
        "RTA", "2.1", "A landlord may require a gadget deposit if the tenant agrees in person"
    )

    assert result.diff == (
        "A landlord may require a gadget deposit if [-, (a)-] the tenant agrees in [-writing-] "
        "{+person+}"
    )
    assert (result.aligned_with, result.matched_words, result.quote_words) == ("s. 2.1(1)", 12, 13)
    assert (result.strict, result.with_conventions) == ("not_found", "not_found")


def test_a_dropped_negation_is_named(statutes: Statutes) -> None:
    result = statutes.verify_quote("RTA", "2.1", "exceed one month’s rent.")

    assert (result.dropped_negation, result.strict) == ("not", "drops_negation")


@pytest.mark.parametrize(
    ("citation", "parts"),
    [
        ("14", ("14", None)),
        ("s. 27(2)(a)", ("27", "(2)")),
        ("section 27(2)", ("27", "(2)")),
        ("RTA s. 14", ("14", None)),
        ("s. 3(1.1)", ("3", "(1.1)")),
    ],
)
def test_split_citation(citation: str, parts: tuple[str, str | None]) -> None:
    assert split_citation(citation) == parts


def test_the_acts_own_title_names_it(statutes: Statutes) -> None:
    assert statutes.get_section(statutes.act.title, "1").section == "1"


@pytest.mark.parametrize(
    ("section", "quote", "note"),
    [
        (
            "s. 2.1(2)",
            "A landlord may require a gadget deposit if",
            " (The quote is in s. 2.1(1), not the cited s. 2.1(2).)",
        ),
        (
            "s. 2.1(2)",
            "A landlord may require a gizmo deposit if",
            " (The quote is closest to s. 2.1(1), not the cited s. 2.1(2).)",
        ),
        (
            "s. 2.1(9)",
            "A landlord may require a gadget deposit if",
            " (s. 2.1 has no subsection (9); the whole section was searched.)",
        ),
        ("s. 2.1(1)", "A landlord may require a gadget deposit if", "Word for word in s. 2.1."),
    ],
)
def test_the_summary_notes_a_quote_outside_the_cited_subsection(
    statutes: Statutes, section: str, quote: str, note: str
) -> None:
    result = statutes.verify_quote("RTA", section, quote)

    assert result.summary.endswith(note)
    assert result.cited_subsection == split_citation(section)[1]


def test_a_dropped_negation_behind_a_cosmetic_change_is_named() -> None:
    sections = {
        "40": Section(
            "40",
            "Distress abolished",
            "No landlord shall, without legal process, seize a tenant’s property.",
            "BK50",
        )
    }
    quote = "Landlord shall, without legal process, seize the tenant’s property"

    result = quotediff.classify(quote, "40", sections)

    assert summary(result, "40") == (
        "Matches s. 40 but for cosmetic differences, but it leaves out the “No” right before it."
    )
