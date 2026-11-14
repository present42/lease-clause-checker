import pytest

from lease_clause_checker.statute import Section
from lease_clause_checker.verify import (
    Difference,
    check_quote,
    check_quote_with_conventions,
    first_difference,
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
        ("A landlord may require a deposit if,", Difference(5, "deposit", "gadget")),
        # Running past the end of subsection (1) into (2).
        (
            "the deposit is returned at the end of the tenancy. (2) The deposit",
            Difference(10, "(2)", None),
        ),
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
