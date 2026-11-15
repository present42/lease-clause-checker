from dataclasses import replace
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

VERBATIM = "A provision in a tenancy agreement prohibiting widgets is void."


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


MESSY_MEMO = """Memo: the no-widget clause

The clause reads "Tenant shall keep no widgets on the premises." Section 1 voids it. Under s. 1
a no-widget term is void, and s. 2.1(1) lets the landlord ask for a deposit, but "The deposit
shall exceed one month’s rent" applies too.

The Act puts it plainly: "A provision in a tenancy agreement prohibiting widgets is void."

A deposit is allowed where "[t]he tenant agrees in writing … the deposit is returned" (RTA,
s. 2.1(1)). Compare the Legislation Act, 2006, s. 67 and ss. 2.1(3) and 9 of the Act.
"""


def test_check_text_finds_and_checks_every_quote_and_citation(statutes: Statutes) -> None:
    found = statutes.check_text(MESSY_MEMO).findings

    assert [(f.line, f.status, f.citation, f.statute) for f in found] == [
        (3, "uncited", None, None),  # a quote of the lease, not the Act
        (3, "citation_only", "s. 1", None),
        (3, "citation_only", "s. 1", None),
        (4, "checked", "s. 2.1(1)", None),  # two citations before it in a sentence: the later
        (7, "uncited", None, None),
        (9, "checked", "s. 2.1(1)", "RTA"),  # an ellipsis and a bracket
        (10, "other_statute", "s. 67", "Legislation Act, 2006"),
        (10, "citation_only", "s. 2.1(3)", "Act"),
        (10, "no_such_section", "s. 9", "Act"),
    ]
    lease, _, _, deposit, plainly, writing, other, subsection, section = found
    assert lease.result is None
    assert "close to no subsection of the Act, so it may not quote the Act" in lease.summary
    assert deposit.result is not None
    assert deposit.result.category == "content_words_differ"
    assert deposit.result.critical_words == ["not"]
    assert deposit.summary.endswith("(The quote is closest to s. 2.1(2), not the cited s. 2.1(1).)")
    assert plainly.result is not None
    assert (plainly.result.section, plainly.result.category) == ("1", "verbatim")
    assert plainly.summary == (
        "No citation goes with it; s. 1 has the closest wording. Word for word in s. 1."
    )
    assert writing.result is not None
    assert (writing.result.strict, writing.result.category) == ("not_found", "conventions")
    assert (
        other.summary == "Cites Legislation Act, 2006, which this server doesn't have; not checked."
    )
    assert subsection.summary == (
        "s. 2.1 (e-Laws heading: Gadget deposits) is in force; no quote goes with this citation, "
        "so there is nothing to check. It has no subsection (3); the citation was read as s. 2.1."
    )
    assert section.summary == "The Act has no in-force section 9."


def test_a_checked_quote_is_what_verify_quote_gives(statutes: Statutes) -> None:
    text = 'Under s. 2.1(2), the deposit may "exceed one month’s rent."'

    [finding] = statutes.check_text(text).findings

    assert finding.result == statutes.verify_quote("RTA", "s. 2.1(2)", "exceed one month’s rent.")
    assert finding.result.category == "drops_negation"


def test_check_text_counts_what_it_found_and_gives_no_verdict(statutes: Statutes) -> None:
    assert statutes.check_text(MESSY_MEMO).summary == (
        "Quotes: 4 (content words differ 1, verbatim 1, passes with quoting conventions 1). "
        "Quotes with no citation: 2. Citations with no quote: 3. Citations of no in-force "
        "section: 1. Citations of another statute, not checked: 1. Wording only: no verdict "
        "on any clause."
    )
    assert statutes.check_text("No quotes here.").summary == (
        "Quotes: 0. Wording only: no verdict on any clause."
    )


@pytest.mark.parametrize(
    "named",
    [
        "RTA, s. 1",
        "s. 1 of the Act",
        "Residential Tenancies Act, 2006, S.O. 2006, c. 17, s. 1",
        "Ontario's Residential Tenancies Act, s. 1",
    ],
)
def test_the_act_by_any_of_its_names_is_checked(statutes: Statutes, named: str) -> None:
    [finding] = statutes.check_text(f"See {named}.").findings

    assert finding.status == "citation_only"


def test_a_quote_cited_to_another_statute_is_not_checked(statutes: Statutes) -> None:
    text = '"Words in the singular include the plural" (Legislation Act, 2006, s. 67).'

    [finding] = statutes.check_text(text).findings

    assert (finding.status, finding.result) == ("other_statute", None)
    assert finding.quote == "Words in the singular include the plural"


def test_a_short_quote_with_no_citation_is_not_looked_up(statutes: Statutes) -> None:
    [finding] = statutes.check_text('A "no widget" term.').findings

    assert finding.summary == (
        "No citation goes with it, and it is too short to look up (under 5 words)."
    )


@pytest.mark.parametrize(
    ("copies", "also"),
    [
        (1, "also s. 10."),
        (3, "also s. 10, s. 11, s. 12."),
        (5, "also s. 10, s. 11, s. 12 and 2 more."),
    ],
)
def test_a_quote_with_no_citation_names_every_section_that_has_its_words(
    statutes: Statutes, copies: int, also: str
) -> None:
    repeated = {
        str(10 + i): replace(statutes.sections["1"], number=str(10 + i)) for i in range(copies)
    }
    more = Statutes(statutes.act, statutes.sections | repeated)

    [finding] = more.check_text(f'"{VERBATIM}"').findings

    assert finding.summary == (
        f"No citation goes with it; its words are in s. 1 (checked here) and {also} Word for word "
        "in s. 1."
    )


@pytest.mark.parametrize(
    "text",
    [
        "Human Rights Code, R.S.O. 1990, c. H.19, s. 2 applies.",
        "Commercial Tenancies Act, R.S.O. 1990, c. L.7, s. 1",
        "Under section 1 of the HRC, a person has rights.",
    ],
)
def test_a_citation_of_another_statute_by_its_full_cite_or_acronym_is_not_checked(
    statutes: Statutes, text: str
) -> None:
    [finding] = statutes.check_text(text).findings

    assert finding.status == "other_statute"


def test_an_unpaired_quotation_mark_hides_no_quote_in_the_next_paragraph(
    statutes: Statutes,
) -> None:
    text = f'The tenant wrote "no. Then he left.\n\nSection 1 says "{VERBATIM}"'

    stray, quote = statutes.check_text(text).findings

    assert (stray.line, stray.status) == (1, "unpaired_mark")
    assert stray.summary.startswith('A quotation mark (") with no partner in its paragraph')
    assert quote.result is not None
    assert (quote.line, quote.citation, quote.result.category) == (3, "s. 1", "verbatim")


def test_a_verbatim_quote_that_stops_early_gets_a_warning(statutes: Statutes) -> None:
    result = statutes.verify_quote("RTA", "2.1", "A landlord may require a gadget deposit if,")

    assert (result.verbatim, result.completeness) == (True, "stops_early")
    assert result.warning == (
        "Warning: word for word, but it may stop before the words that decide it. The Act goes "
        "on: \u201c(a) the tenant agrees in writing; and (b) the deposit is returned at the end of "
        "the tenancy.\u201d"
    )
    whole = statutes.verify_quote("RTA", "1", VERBATIM)
    assert (whole.completeness, whole.warning) == ("ends_cleanly", None)
    changed = statutes.verify_quote("RTA", "2.1", "A landlord may require a widget deposit if,")
    assert (changed.completeness, changed.warning) == (None, None)
    negated = statutes.verify_quote("RTA", "2.1", "exceed one month’s rent.")  # found, not verbatim
    assert (negated.strict, negated.completeness) == ("drops_negation", None)
    short = statutes.verify_quote("RTA", "2.1", "A landlord may")
    assert (short.strict, short.completeness) == ("too_short", None)


def test_verify_quote_reads_the_ending_in_the_cited_subsection(statutes: Statutes) -> None:
    text = "(1) A tenant may pay the rent,\n(a) by cheque.\n(2) A tenant may pay the rent in cash."
    more = replace(statutes, sections={**statutes.sections, "7": Section("7", "Pay", text, "BK7")})

    assert more.verify_quote("RTA", "s. 7", "A tenant may pay the rent").warning is not None
    assert more.verify_quote("RTA", "s. 7(2)", "A tenant may pay the rent").warning is None


def test_check_text_shows_the_warning_and_counts_it(statutes: Statutes) -> None:
    checked = statutes.check_text('Section 2.1 says "A landlord may require a gadget deposit if,".')

    [finding] = checked.findings
    assert finding.result is not None
    assert finding.result.warning is not None
    assert finding.summary.endswith(finding.result.warning)
    assert "Verbatim quotes that may stop before the deciding words: 1." in checked.summary
    [uncited] = statutes.check_text(
        'The rule: "A landlord may require a gadget deposit if,"'
    ).findings
    assert uncited.status == "uncited"
    assert uncited.result is not None
    assert uncited.result.warning is not None
    assert uncited.summary.endswith(uncited.result.warning)
