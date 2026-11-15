import pytest

from lease_clause_checker import verify
from lease_clause_checker.quotediff import Category, LeftOut, align, classify, tokens
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
        ("landlord shall, without legal process, seize a tenant", "40", "drops_negation"),
        # A cosmetic change doesn't hide the dropped "No".
        (
            "Landlord shall without legal process seize the tenant’s property",
            "40", "drops_negation",
        ),
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
            "27", "content_words_differ",
        ),
        (
            "A landlord may enter a rental unit in accordance with oral notice given",
            "27", "content_words_differ",
        ),
        ("the presence of animals in or about the residential complex is void", "27", "wrong_cite"),
        ("No landlord shall, without legal process, seize a tenant", "27", "wrong_cite"),
        ("A landlord shall seize the property of a tenant at any time", "40", "not_in_act"),
        # A paragraph marker starting a line of the Act is layout; one inside a line is a word.
        ("A landlord shall not seize property for arrears under", "60", "cosmetic"),
        ("seize property for arrears under subsection 59; or", "60", "content_words_differ"),
        ("under the following circumstances: To carry out a repair", "80", "cosmetic"),
        ("under the following circumstances: carry out a repair", "80", "content_words_differ"),
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
        ("end a tenancy unless its is terminated under this Act", "60", "content_words_differ"),
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

    assert (result.category, result.critical) == ("content_words_differ", ("written",))


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

    assert (result.category, result.critical) == ("content_words_differ", ("no",))


def test_a_dropped_negation_elsewhere_is_not_the_same_words() -> None:
    # s. 40's words without its "No", cited to s. 14: a wrong cite, but with words changed.
    result = classify("landlord shall, without legal process, seize a tenant", "14", SECTIONS)

    assert (result.category, result.found_in, result.words_changed) == ("wrong_cite", ("40",), True)


def test_only_cosmetic_opening_words_are_looked_behind_for_a_negation() -> None:
    section = Section("71", "Test", "This shall not ever apply to any landlord.", "BK81")
    changed = align("never apply to any landlord", section)  # "never" for "ever": a change
    cosmetic = align("Ever apply to any landlord", section)  # case only

    assert changed is not None
    assert cosmetic is not None
    assert changed.dropped_negation is None
    assert cosmetic.dropped_negation == "not"


@pytest.mark.parametrize(
    ("quote", "section", "whole"),
    [
        (
            "A landlord may enter a rental unit in accordance with oral notice given",
            "27",  # no "(1)" marker before it, and the rest of (1) after it
            "A landlord may enter a rental unit in accordance with [-written-] {+oral+} notice "
            "given [-to the tenant at least 24 hours before the time of entry.-]",
        ),
        (
            "landlord shall, without legal process, seize",
            "40",
            "[-No-] landlord shall, without legal process, seize [-a tenant’s property for "
            "default in the payment of rent.-]",
        ),
        (  # "Every" already stands against "No", so "No" is not dropped twice
            "Every landlord shall, without legal process, seize a tenant’s property",
            "40",
            "[-No-] {+Every+} landlord shall, without legal process, seize a tenant’s property "
            "[-for default in the payment of rent.-]",
        ),
        (  # "now" already stands against the full stop
            "No landlord shall, without legal process, seize a tenant’s property for default in "
            "the payment of rent now",
            "40",
            "No landlord shall, without legal process, seize a tenant’s property for default in "
            "the payment of rent [-.-] {+now+}",
        ),
    ],
)
def test_a_whole_wdiff_shows_what_the_quote_leaves_out_at_its_ends(
    quote: str, section: str, whole: str
) -> None:
    alignment = align(quote, SECTIONS[section])

    assert alignment is not None
    assert alignment.wdiff(whole=True) == whole


LEFT_OUT = {
    "acceleration": Section(
        "15",
        "Acceleration clause void",
        "A landlord may charge rent. A provision in a tenancy agreement providing that the rent "
        "becomes due upon a default of the tenant is void. Another rule follows.",
        "BK20",
    ),
    "list": Section(
        "27",
        "Entry with notice",
        "(1) A landlord may enter a rental unit under the following circumstances:\n"
        "1. To carry out a repair in the rental unit.\n"
        "2. To show the rental unit to a buyer.\n"
        "3. To let an insurer view the rental unit.\n"
        "(2) Subject to subsection 12.1 (11), the landlord shall give written notice.\n"
        "(3) The notice shall give a time of entry between 8 a.m. and 8 p.m.",
        "BK33",
    ),
    "letters": Section(
        "7",
        "Letters",
        "The following apply to a unit described below:\nA. a unit owned by a co-operative, "
        "if its members live in it.\nB. a unit owned by a charity.",
        "BK8",
    ),
    "definitions": Section(
        "2",
        "Definitions",
        "In this Act,\n“Board” means the Landlord and Tenant Board; (“Commission”)\n"
        "“vital service” means hot or cold water, fuel or electricity; (“service essentiel”)",
        "BK2",
    ),
    "long": Section(
        "80",
        "Long",
        "If the landlord and the tenant agree in writing on the amount and the day and the place, "
        "(a) the landlord may keep the deposit; or (b) the tenant may ask for it back from the "
        "landlord at the end of the term in writing, as long as the tenant is not in arrears.",
        "BK90",
    ),
    "cut": Section(
        "81",
        "Cut",
        "If the landlord and tenant agree on (a) the amount of the deposit and the day it is paid "
        "and the place it is kept, as set out in (b) the form that the tenant has signed, the "
        "landlord may keep the deposit.",
        "BK91",
    ),
    "sixteen": Section(
        "82",
        "Sixteen",
        "One two three four five six seven eight nine ten eleven twelve thirteen fourteen "
        "fifteen sixteen the landlord may keep the deposit.",
        "BK92",
    ),
}


@pytest.mark.parametrize(
    ("section", "quote", "left_out"),
    [
        (  # the sentence's head and tail, not the sentences around it, nor its full stop
            "acceleration",
            "the rent becomes due upon a default by the tenant",
            LeftOut("A provision in a tenancy agreement providing that", "is void"),
        ),
        (  # words the quote adds at its ends don't hide what it leaves out
            "acceleration",
            "Under the law, a provision in a tenancy agreement providing that the rent becomes due "
            "upon a default of the tenant in any way at all",
            LeftOut("", "is void"),
        ),
        (  # a list is one sentence with the words that lead into it
            "list",
            "A landlord may enter a rental unit under the following circumstances:",
            LeftOut("", "1. To carry out a repair in the … To let an insurer view the rental unit"),
        ),
        (  # in a list item: the lead-in, not the other items or the item's own "2."
            "list",
            "To show the rental unit to a buyer.",
            LeftOut("A landlord may enter a rental unit under the following circumstances", ""),
        ),
        (  # in a list item: the lead-in and the item's own words, not the next item
            "list",
            "the rental unit to a buyer",
            LeftOut(
                "A landlord may enter a rental unit under the following circumstances … To show", ""
            ),
        ),
        (  # "12.1" is a number, not a full stop
            "list",
            "the landlord shall give written notice.",
            LeftOut("Subject to subsection 12.1 (11)", ""),
        ),
        (  # "a.m." before a lower-case word ends no sentence, and "p.m." keeps its full stop
            "list",
            "The notice shall give a time of entry",
            LeftOut("", "between 8 a.m. and 8 p.m."),
        ),
        (
            "list",
            "The notice shall give a time of entry between 8 a.m. and 8",
            LeftOut("", "p.m."),
        ),
        (  # "A." starts a list item too
            "letters",
            "a unit owned by a co-operative, if its members",
            LeftOut("The following apply to a unit described below", "live in it"),
        ),
        (  # each definition is its own sentence, and its quotation marks stay
            "definitions",
            "means hot or cold water, fuel or electricity",
            LeftOut("“vital service”", "(“service essentiel”)"),
        ),
        ("acceleration", "A landlord may charge rent.", LeftOut("", "")),
        (  # a lone article is not worth naming
            "acceleration",
            "provision in a tenancy agreement providing that the rent becomes due",
            LeftOut("", "upon a default of the tenant is void"),
        ),
        (  # sixteen words are shown whole
            "sixteen",
            "the landlord may keep the deposit.",
            LeftOut(
                "One two three four five six seven eight nine ten eleven twelve thirteen fourteen "
                "fifteen sixteen",
                "",
            ),
        ),
        (  # long stretches keep their ends, with "(a)" whole
            "long",
            "the landlord may keep the deposit",
            LeftOut(
                "If the landlord and the tenant agree in … amount and the day and the place, (a)",
                "or (b) the tenant may ask for it … long as the tenant is not in arrears",
            ),
        ),
        (  # a cut at either end never splits "(a)" or "(b)"
            "cut",
            "the landlord may keep the deposit.",
            LeftOut(
                "If the landlord and tenant agree on (a) … (b) the form that the tenant has signed",
                "",
            ),
        ),
    ],
)
def test_left_out_names_the_words_of_the_quote_s_sentence_it_leaves_out(
    section: str, quote: str, left_out: LeftOut
) -> None:
    alignment = align(quote, LEFT_OUT[section])

    assert alignment is not None
    assert alignment.left_out == left_out
