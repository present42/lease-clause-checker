import pytest

from lease_clause_checker import verify
from lease_clause_checker.quotediff import Category, align, classify, tokens
from lease_clause_checker.statute import Section

SECTIONS = {
    s.number: s
    for s in (
        Section(
            "14",
            "“No pet” provisions void",
            "A provision in a tenancy agreement prohibiting the presence of animals in or about "
            "the residential complex is void.",
            "BK19",
        ),
        Section(
            "27",
            "Entry with notice",
            "(1) A landlord may enter a rental unit in accordance with written notice given to "
            "the tenant at least 24 hours before the time of entry.\n"
            "(2) The written notice under subsection (1) shall specify the reasons for entry.",
            "BK33",
        ),
        Section(
            "40",
            "Distress abolished",
            "No landlord shall, without legal process, seize a tenant’s property for default in "
            "the payment of rent.",
            "BK50",
        ),
        Section(
            "60",
            "Paragraphs and cross-references",
            "A landlord shall not,\n(i) seize property for arrears under subsection 59 (1); or\n"
            "(ii) end a tenancy unless it is terminated under this Act.",
            "BK70",
        ),
        Section(
            "80",
            "Numbered paragraphs",
            "A landlord may enter under the following circumstances:\n"
            "1. To carry out a repair or replacement.\n2. To show the unit to a buyer.",
            "BK90",
        ),
        Section(
            "108",
            "Post-dated cheques",
            "Neither a landlord nor a tenancy agreement shall require a tenant to,\n"
            "(a) provide post-dated cheques for payment of rent; or\n"
            "(b) permit automatic debiting of the tenant’s account for the payment of rent.",
            "BK119",
        ),
    )
}


@pytest.mark.parametrize(
    ("quote", "cited", "category"),
    [
        (
            "prohibiting the presence of animals in or about the residential complex",
            "14", "verbatim",
        ),
        ("is void", "14", "too_short"),
        ("", "14", "no_quote"),
        ("a provision in a tenancy agreement prohibiting the presence", "14", "conventions"),
        (
            "The landlord may enter the rental unit in accordance with written notice",
            "27", "cosmetic",
        ),
        ("the presence of animal in or about the residential complex is void", "14", "cosmetic"),
        ("The written notices under subsection (1) shall specify the reasons", "27", "cosmetic"),
        ("require a tenant to provide post-dated cheques for payment of rent.", "108", "cosmetic"),
        (
            "A landlord may enter a rental unit in accordance with notice given to",
            "27", "meaning_changed",
        ),
        (
            "A landlord may enter a rental unit in accordance with oral notice given",
            "27", "meaning_changed",
        ),
        ("the presence of animals in or about the residential complex is void", "27", "wrong_cite"),
        ("No landlord shall, without legal process, seize a tenant", "27", "wrong_cite"),
        ("A landlord shall seize the property of a tenant at any time", "40", "not_in_act"),
        # A paragraph marker starting a line of the Act is layout; one inside a line is a word.
        ("A landlord shall not seize property for arrears under", "60", "cosmetic"),
        ("seize property for arrears under subsection 59; or", "60", "meaning_changed"),
        ("under the following circumstances: To carry out a repair", "80", "cosmetic"),
        ("under the following circumstances: carry out a repair", "80", "meaning_changed"),
        # Under 5 words is too short to check, even if only an article changed.
        ("seize the tenant’s property", "40", "too_short"),
        # A cited number that isn't a section of the Act.
        ("the presence of animals in or about the residential complex", "999", "wrong_cite"),
        ("A landlord may walk in at any hour of the day", "999", "not_in_act"),
        # Grammatical number: a plural noun and its verb.
        (
            "Provisions in a tenancy agreement prohibiting the presence of animals in or about "
            "the residential complex are void",
            "14", "cosmetic",
        ),
        # Only words of 3 letters or more can differ by a plural "s" ("it", "its").
        ("end a tenancy unless its is terminated under this Act", "60", "meaning_changed"),
        # Half the quote's words must line up: 6 of 13 is not enough.
        (
            "A landlord may enter whenever the tenant is away on a long holiday",
            "27", "not_in_act",
        ),
    ],
)  # fmt: skip
def test_each_quote_gets_the_first_category_that_fits(
    quote: str, cited: str, category: Category
) -> None:
    assert classify(quote, cited, SECTIONS).category == category


def test_a_dropped_critical_word_is_never_cosmetic_and_is_named() -> None:
    result = classify("in accordance with notice given to the tenant at least", "27", SECTIONS)

    assert (result.category, result.critical) == ("meaning_changed", ("written",))


def test_a_quote_that_lines_up_only_with_another_section_is_a_wrong_cite_with_changed_words() -> (
    None
):
    result = classify("No landlord shall without process seize a tenant’s property", "27", SECTIONS)

    assert (result.category, result.found_in, result.words_changed) == ("wrong_cite", ("40",), True)
    assert result.alignment is not None
    assert result.alignment.section == "40"


def test_a_wrong_cite_names_every_section_that_holds_the_words() -> None:
    result = classify("the presence of animals in or about the residential complex", "27", SECTIONS)

    assert result.found_in == ("14",)


def test_words_scattered_over_a_long_subsection_are_not_a_version_of_it() -> None:
    long = Section("70", "Long", " ".join(f"w{i}" for i in range(40)), "BK80")
    # All 8 words are in the section in this order, but spread over all 40 of its words.
    scattered = align("w0 w1 w2 w3 w12 w21 w30 w39", long)
    close = align("w0 w1 w2 w3 w5 w7 w9 w11", long)

    assert scattered is not None
    assert close is not None
    assert (scattered.matched_words, scattered.quote_words, scattered.span_words) == (8, 8, 40)
    assert not scattered.lined_up
    assert close.lined_up


def test_a_short_match_far_from_the_rest_is_dropped() -> None:
    # A lone "entry", 18 tokens past "unit", would stretch the match over all of s. 27(1).
    alignment = align("A landlord may enter a rental unit only on entry", SECTIONS["27"])

    assert alignment is not None
    assert alignment.blocks == ((3, 0, 7),)  # after the "(1)" marker
    assert alignment.lined_up


def test_a_quote_that_check_passes_before_an_apostrophe_lines_up_in_full() -> None:
    quote = "No landlord shall, without legal process, seize a tenant"
    assert verify.check_quote(quote, "40", SECTIONS) == "verified"

    alignment = align(quote, SECTIONS["40"])

    assert alignment is not None
    assert alignment.matched_words == alignment.quote_words == 9
    assert alignment.changes == ()


def test_a_closing_full_stop_neither_anchors_the_match_nor_counts_as_a_dropped_word() -> None:
    alignment = align(
        "require a tenant to provide post-dated cheques for payment of rent.", SECTIONS["108"]
    )

    assert alignment is not None
    assert alignment.wdiff() == (
        "require a tenant to [-, (a)-] provide post-dated cheques for payment of rent {+.+}"
    )


def test_the_act_keeps_its_own_characters_and_line_breaks() -> None:
    act = tokens(SECTIONS["108"].text)

    assert [t.text for t in act if t.key == "'"] == ["’"]
    assert [t.text for t in act if t.before == "\n"] == ["Neither", "(", "("]  # line starts


def test_wdiff_marks_dropped_and_added_words() -> None:
    alignment = align(
        "A landlord may enter a rental unit in accordance with oral notice given", SECTIONS["27"]
    )

    assert alignment is not None
    assert alignment.subsection == "(1)"
    assert alignment.wdiff() == (
        "A landlord may enter a rental unit in accordance with [-written-] {+oral+} notice given"
    )


def test_a_dropped_leading_negation_is_flagged() -> None:
    result = classify(
        "a landlord shall, without legal process, seize a tenant’s property", "40", SECTIONS
    )

    assert (result.category, result.critical) == ("meaning_changed", ("no",))
