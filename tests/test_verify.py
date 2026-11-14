import pytest

from lease_clause_checker.statute import Section
from lease_clause_checker.verify import check_quote

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
