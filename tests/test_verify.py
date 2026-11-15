import pytest

from lease_clause_checker.statute import Section
from lease_clause_checker.verify import (
    Difference,
    check_quote,
    check_quote_with_conventions,
    dropped_negation,
    ending,
    first_difference,
    quote_in_text,
    subsection_of,
    subsections,
)

# Sample Act section 2.1 (see tests/fixtures/elaws_sample.html):
#   (1) A landlord may require a gadget deposit if,
#   (a) the tenant agrees in writing; and
#   (b) the deposit is returned at the end of the tenancy.
#   (2) The deposit shall not exceed one month’s rent.     <- curly apostrophe
# Its heading is "Gadget deposits"; subsection (2) has the headnote "Same, amount".


@pytest.mark.parametrize(
    "quote",
    [
        "The deposit shall not exceed one month’s rent.",
        "The deposit shall not exceed one month's rent.",  # straight apostrophe
        '"The deposit shall not exceed one month’s rent."',  # in quotation marks
        "“The deposit shall not exceed one month’s rent.”",
        "the tenant agrees in writing; and\n(b) the deposit is returned",  # across paragraphs
        "  A landlord   may require a gadget deposit if,  ",
        "(2) The deposit shall not exceed",  # with the subsection number
        "The deposit shall not exceed",  # exactly five words
    ],
)
def test_a_verbatim_quote_verifies(sections: dict[str, Section], quote: str) -> None:
    assert check_quote(quote, "2.1", sections) == "verified"


@pytest.mark.parametrize(
    "quote",
    [
        "The deposit must not exceed one month’s rent.",  # one word changed
        "The deposit shall not exceed month’s rent.",  # one word dropped
        "The deposit shall not exceed one full month’s rent.",  # one word added
        "the deposit shall not exceed one month’s rent.",  # letter case changed
        "A landlord may require … deposit if,",  # ellipsis
        "eposit shall not exceed one month’s rent.",  # starts mid-word
        "The deposit shall not exceed one mont",  # ends mid-word
        # Continuous only because the parser dropped a repealed subsection in between:
        "returned at the end of the tenancy. (2) The deposit shall not exceed",
    ],
)
def test_an_almost_right_quote_does_not_verify(sections: dict[str, Section], quote: str) -> None:
    assert check_quote(quote, "2.1", sections) == "not_found"


def test_a_quote_from_another_section_does_not_verify(sections: dict[str, Section]) -> None:
    quote = "A provision in a tenancy agreement prohibiting widgets is void."

    assert check_quote(quote, "1", sections) == "verified"
    assert check_quote(quote, "2.1", sections) == "not_found"


@pytest.mark.parametrize(
    "quote",
    ["The deposit shall not", "(2) The deposit shall not", "if, (a) the tenant agrees"],
)
def test_a_found_quote_under_five_words_is_too_short(
    sections: dict[str, Section], quote: str
) -> None:
    assert check_quote(quote, "2.1", sections) == "too_short"  # "(2)" and "(a)" aren't words


@pytest.mark.parametrize(
    ("quote", "section"),
    [
        ("Gadget deposits", "2.1"),  # the section's heading
        ("Same, amount", "2.1"),  # a subsection's headnote
        ('"No widget" provisions void', "1"),  # straight quotes for the heading's curly ones
    ],
)
def test_a_heading_quote_is_reported_as_one(
    sections: dict[str, Section], quote: str, section: str
) -> None:
    assert check_quote(quote, section, sections) == "heading"


@pytest.mark.parametrize("quote", ["", "   ", '""'])
def test_an_empty_quote_is_no_quote(sections: dict[str, Section], quote: str) -> None:
    assert check_quote(quote, "2.1", sections) == "no_quote"


def test_a_section_not_in_the_act_is_reported(sections: dict[str, Section]) -> None:
    assert check_quote("The deposit shall not exceed", "99", sections) == "no_such_section"


@pytest.mark.parametrize(
    ("quote", "difference"),
    [
        # A changed word: the Act's word is named.
        ("A landlord may require a deposit if,", Difference(5, "deposit", "gadget", "(1)")),
        # Running past the end of subsection (1) into (2).
        (
            "the deposit is returned at the end of the tenancy. (2) The deposit",
            Difference(10, "(2)", None, "(1)"),
        ),
        # Found in subsection (2), which is named.
        ("The deposit shall not exceed two months", Difference(5, "two", "one", "(2)")),
        # Not even the first word.
        ("Landlords may require a gadget deposit", Difference(0, "Landlords", None)),
    ],
)
def test_first_difference_says_where_a_quote_leaves_the_act(
    sections: dict[str, Section], quote: str, difference: Difference
) -> None:
    assert first_difference(quote, sections["2.1"].text) == difference


def test_a_verbatim_quote_has_no_first_difference(sections: dict[str, Section]) -> None:
    quote = "“The deposit shall not exceed one month's rent.”"  # straight apostrophe, curly marks

    assert first_difference(quote, sections["2.1"].text) is None


@pytest.mark.parametrize(
    ("quote", "marker"),
    [
        ("The deposit shall not exceed", "(2)"),
        ("the tenant agrees in writing", "(1)"),
        ("not in this section at all", None),
    ],
)
def test_subsection_of_names_the_subsection_holding_a_quote(
    sections: dict[str, Section], quote: str, marker: str | None
) -> None:
    assert subsection_of(quote, sections["2.1"].text) == marker


def test_a_section_without_numbered_subsections_has_an_empty_marker(
    sections: dict[str, Section],
) -> None:
    assert subsection_of("prohibiting widgets is void", sections["1"].text) == ""


def test_subsections_split_before_each_marker_with_no_empty_part() -> None:
    assert subsections("(1) One.\n(2) Two.") == ["(1) One.\n", "(2) Two."]
    assert subsections("No subsections.") == ["No subsections."]


CONVENTIONS = {
    "27": Section(
        "27",
        "Entry with notice",
        "(1) A landlord may enter a rental unit in accordance with written notice given to the "
        "tenant at least 24 hours before the time of entry.\n"
        "(2) The written notice under subsection (1) shall specify the reasons for entry.",
        "BK33",
    )
}


@pytest.mark.parametrize(
    "quote",
    [
        "a landlord may enter a rental unit",  # first letter's case changed
        "A landlord may enter … at least 24 hours before the time of entry",
        "A landlord may enter ... written notice given to the tenant",
        "A landlord may enter [...] written notice given to the tenant",
        "[T]he written notice … shall specify the reasons for entry",
        "[The] written notice under subsection (1) shall specify",
        "landlord may enter [the] rental unit in accordance",  # an article changed
        "landlord may enter [A Rental] unit in accordance",  # two words for two, case only
        "The written notice[s] under subsection (1) shall specify",  # a plural ending
        "… a landlord may enter a rental unit …",  # ellipses at both ends
    ],
)
def test_marked_changes_pass_with_conventions_but_not_strictly(quote: str) -> None:
    assert check_quote(quote, "27", CONVENTIONS) == "not_found"
    assert check_quote_with_conventions(quote, "27", CONVENTIONS) == "verified"


@pytest.mark.parametrize(
    "quote",
    [
        "The written notice shall specify the reasons",  # unmarked omission
        "A landlord may enter … The written notice",  # pieces in two subsections
        "the written notice … A landlord may enter",  # pieces out of order
        "at least 24 hours … A landlord may enter",  # out of order within one subsection
        "landlord may enter a [dwelling] unit in accordance",  # a bracket can't change a word
        "A landlord [does not] enter a rental unit",  # nor add a negation
        "A landlord [can] enter a rental unit",  # nor stand for a different word
        "[un]written notice given to the tenant at least",  # nor add letters inside a word
        "[] landlord may enter a rental unit",  # empty brackets
        "…",  # a bare ellipsis is no quote of anything
        "A landlord may enter a rental unit with notice",  # unmarked change
        "may enter [the] unit in accordance",  # a one-word bracket for two Act words
        "a Landlord may enter a rental unit",  # only the first letter's case may change
    ],
)
def test_unmarked_changes_fail_with_conventions(quote: str) -> None:
    assert check_quote_with_conventions(quote, "27", CONVENTIONS) == "not_found"


def test_conventions_count_words_across_pieces() -> None:
    assert check_quote_with_conventions("A landlord … the tenant", "27", CONVENTIONS) == "too_short"
    assert check_quote_with_conventions("", "27", CONVENTIONS) == "no_quote"
    assert check_quote_with_conventions("A landlord may", "99", CONVENTIONS) == "no_such_section"


NEGATED = {
    "40": Section(
        "40",
        "Distress abolished",
        "No landlord shall, without legal process, seize a tenant’s property for default in "
        "the payment of rent.",
        "BK50",
    ),
    "90": Section(
        "90",
        "Made up for the test",
        "Neither a landlord nor a tenancy agreement shall require a tenant to provide "
        "post-dated cheques.\nA tenancy may not be terminated without notice to the tenant.\n"
        "A landlord shall not enter the unit and shall not change the locks of the unit.\n"
        "This applies whether or not the tenant occupies the unit for a residential purpose.\n"
        "No landlord shall charge a fee for keys. A landlord shall charge a fee for keys only "
        "if the tenant agrees.",
        "BK99",
    ),
}


def _section(quote: str) -> str:
    return "40" if "seize" in quote else "90"


@pytest.mark.parametrize(
    "quote",
    [
        "landlord shall, without legal process, seize a tenant’s property",  # after "No"
        "a tenancy agreement shall require a tenant to provide",  # after "nor"
        "a landlord nor a tenancy agreement shall require",  # after "Neither"
        "be terminated without notice to the tenant",  # after "not"
    ],
)
def test_a_quote_that_leaves_out_the_negation_before_it_fails(quote: str) -> None:
    assert quote_in_text(quote, NEGATED[_section(quote)].text)
    assert check_quote(quote, _section(quote), NEGATED) == "drops_negation"
    assert dropped_negation(quote, NEGATED[_section(quote)].text) in ("No", "nor", "Neither", "not")


@pytest.mark.parametrize(
    "quote",
    [
        "No landlord shall, without legal process, seize",  # includes the negation
        "without legal process, seize a tenant’s property",  # after a comma, a new phrase
        "Neither a landlord nor a tenancy agreement shall require",
        "the tenant occupies the unit for a residential purpose",  # after "whether or not"
        "landlord shall charge a fee for keys",  # once after "No", once not
    ],
)
def test_a_quote_that_keeps_or_does_not_follow_a_negation_passes(quote: str) -> None:
    assert check_quote(quote, _section(quote), NEGATED) == "verified"


def test_conventions_do_not_let_an_ellipsis_piece_drop_a_negation() -> None:
    dropped = "A tenancy … be terminated without notice to the tenant"
    kept = "A tenancy may not … without notice to the tenant"
    carried = "A landlord shall not … change the locks of the unit"  # "not" ends the piece

    assert check_quote_with_conventions(dropped, "90", NEGATED) == "not_found"
    assert check_quote_with_conventions(kept, "90", NEGATED) == "verified"
    assert check_quote_with_conventions(carried, "90", NEGATED) == "verified"


ENTRY = {
    "9": Section(
        "9",
        "Entry",
        "(1) A landlord may enter without notice:\n(a) in an emergency.\n"
        "(2) A landlord may enter under the following circumstances:\n1. To repair the unit.\n"
        "(3) A landlord may enter, with notice, at any time.\n"
        "(4) A landlord may enter only if,",  # as if its clauses were repealed
        "BK9",
    )
}


@pytest.mark.parametrize(
    ("quote", "completeness", "goes_on"),
    [
        (
            "A landlord may require a gadget deposit if,",
            "stops_early",
            "(a) the tenant agrees in writing; and (b) the deposit is returned at the end of the "
            "tenancy.",
        ),
        (
            "may require a gadget deposit if, (a) the tenant agrees in writing; and",
            "stops_early",  # ends on "and": the next item is left out
            "(b) the deposit is returned at the end of the tenancy.",
        ),
        (  # 21 words follow, so the last is cut
            "A landlord may require a",
            "stops_early",
            "gadget deposit if, (a) the tenant agrees in writing; and (b) the deposit is returned "
            "at the end of the …",
        ),
        ("The deposit shall not exceed one month’s rent.", "ends_cleanly", ""),
        ("the deposit is returned at the end of the tenancy.", "ends_cleanly", ""),  # (1) ends
        (  # before "(a)", though not right before it
            "A landlord may require a gadget deposit",
            "stops_early",
            "if, (a) the tenant agrees in writing; and (b) the deposit is returned at the end of "
            "the tenancy.",
        ),
        (  # the comma after the quote is not shown
            "A landlord may require a gadget deposit if",
            "stops_early",
            "(a) the tenant agrees in writing; and (b) the deposit is returned at the end of the "
            "tenancy.",
        ),
        # Stopping mid-phrase, with no list item after it, is not flagged.
        ("The deposit shall not exceed", "ends_cleanly", "one month's rent."),
        ("the deposit is returned at the end", "ends_cleanly", "of the tenancy."),  # in (b)
    ],
)
def test_ending_flags_a_quote_that_stops_before_what_completes_it(
    sections: dict[str, Section], quote: str, completeness: str, goes_on: str
) -> None:
    found = ending(quote, "2.1", sections)

    assert found is not None
    assert (found.completeness, found.goes_on) == (completeness, goes_on)


@pytest.mark.parametrize(
    ("quote", "completeness"),
    [
        ("A landlord may enter without notice", "stops_early"),  # before "(a)"
        ("may enter under the following circumstances", "stops_early"),  # before "1."
        ("A landlord may enter, with notice,", "stops_early"),  # on a comma
        ("A landlord may enter only if,", "ends_cleanly"),  # nothing follows in (4)
    ],
)
def test_ending_flags_a_quote_that_stops_before_a_list_or_on_a_comma(
    quote: str, completeness: str
) -> None:
    found = ending(quote, "9", ENTRY)

    assert found is not None
    assert found.completeness == completeness


TWICE = {
    "7": Section(
        "7",
        "Twice",
        "(1) No landlord may enter the unit,\n(a) at night.\n"
        "(2) A landlord may enter the unit with notice.\n"
        "(3) A tenant may pay the rent,\n(a) by cheque.\n"
        "(4) A tenant may pay the rent in cash.",
        "BK7",
    )
}


def test_ending_reads_the_quote_where_it_passes_the_check() -> None:
    # In (1) the quote follows "No", so it passes on (2), which ends cleanly.
    assert check_quote("landlord may enter the unit", "7", TWICE) == "verified"
    found = ending("landlord may enter the unit", "7", TWICE)
    assert found is not None
    assert found.completeness == "ends_cleanly"


@pytest.mark.parametrize(
    ("subsection", "completeness"), [(None, "stops_early"), ("(4)", "ends_cleanly")]
)
def test_ending_prefers_the_cited_subsection(subsection: str | None, completeness: str) -> None:
    found = ending("A tenant may pay the rent", "7", TWICE, subsection)

    assert found is not None
    assert found.completeness == completeness


def test_ending_is_none_for_a_quote_not_in_the_section(sections: dict[str, Section]) -> None:
    assert ending("A landlord may require a widget deposit", "2.1", sections) is None
    assert ending("A landlord may require a gadget deposit", "99", sections) is None
