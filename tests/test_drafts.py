import pytest

from lease_clause_checker.drafts import read_draft


def cited(text: str) -> list[str | None]:
    """The citation each quotation in `text` goes with."""
    return [q.citation.cited if q.citation else None for q in read_draft(text).quotations]


def test_a_citation_with_no_quote_is_found_and_paired_with_nothing() -> None:
    draft = read_draft("Section 14 voids a no-pet term.")

    assert [c.cited for c in draft.citations] == ["s. 14"]
    assert draft.quotations == ()


def test_a_quote_with_no_citation_in_its_sentence_goes_with_none() -> None:
    assert cited('The clause reads "Tenant shall keep no pets." Section 14 voids it.') == [None]


@pytest.mark.parametrize(
    ("text", "citation"),
    [
        ('Under s. 14 a no-pet term is void, and s. 15 says "A provision is void."', "s. 15"),
        ('Under s. 14, "A provision is void" (s. 15).', "s. 15"),
        ('Under s. 14, "A provision is void" (RTA, s. 15(1)(a)).', "s. 15(1)(a)"),
        ('Section 14 says "A provision is void", unlike s. 15.', "s. 14"),
    ],
)
def test_with_two_citations_in_a_sentence_the_one_right_after_wins_else_the_one_before(
    text: str, citation: str
) -> None:
    assert cited(text) == [citation]


@pytest.mark.parametrize(
    ("text", "citation"),
    [
        ('See s. 14\n\n"A provision is void."', None),  # a blank line, with no full stop
        ('Void (see s. 14.) "A provision is void."', None),  # a full stop inside a bracket
        ('- Pets: s. 14\n- Deposit: the lease says "pay a deposit"', None),
        ('## Section 14\nThe lease says "pay a deposit"', None),
        ('1) Pets: s. 14\n2) The lease says "pay a deposit"', None),
        ('Under s. 14,\nthe lease says "pay a deposit"', "s. 14"),  # a wrapped line
        ('Under s. 14, the rule says "a deposit" (O. Reg. 516/06, s. 3).', "s. 3"),
        ('Under s. 14, "a right" (Human Rights Code, R.S.O. 1990, c. H.19, s. 2).', "s. 2"),
    ],
)
def test_where_a_sentence_ends(text: str, citation: str | None) -> None:
    assert cited(text) == [citation]


def test_a_full_stop_inside_a_quote_ends_no_sentence() -> None:
    assert cited('"Widgets are fine. Gizmos too." (s. 1).') == ["s. 1"]
    assert cited('"Widgets are fine." Section 1 says so.') == [None]


def test_a_citation_inside_a_quote_is_part_of_it() -> None:
    draft = read_draft('Section 3 says "despite section 83, the Act applies" here.')

    assert [c.cited for c in draft.citations] == ["s. 3"]
    assert cited(draft.text) == ["s. 3"]


@pytest.mark.parametrize(
    ("text", "citations"),
    [
        ("ss. 2.1(1) and 4 apply", ["s. 2.1(1)", "s. 4"]),
        ("sections 14, 15 or 16 apply", ["s. 14", "s. 15", "s. 16"]),
        ("s. 14 and 15 days later", ["s. 14"]),
        ("ss. 14-16 apply", ["s. 14"]),
        ("subsection 27 (2) and subsection (3)", ["s. 27(2)"]),
        ("the tenant's 14 days, U.S. 14, 3 s. 1.1", ["s. 1.1"]),
        ("§§ 3(1.1) and 4", ["s. 3(1.1)", "s. 4"]),
    ],
)
def test_citations_and_lists_of_them(text: str, citations: list[str]) -> None:
    assert [c.cited for c in read_draft(text).citations] == citations


@pytest.mark.parametrize(
    ("text", "statute"),
    [
        ("the Legislation Act, 2006, s. 67", "Legislation Act, 2006"),
        ("s. 67 of the Legislation Act, 2006", "Legislation Act, 2006"),
        ("ss. 14 and 15 of the Act", "Act"),
        ("RTA, s. 14", "RTA"),
        (
            "Residential Tenancies Act, 2006, S.O. 2006, c. 17, s. 14",
            "Residential Tenancies Act, 2006, S.O. 2006, c. 17",
        ),
        ("O. Reg. 516/06, s. 3", "O. Reg. 516/06"),
        ("See Legislation Act, 2006, s. 67", "Legislation Act, 2006"),
        ("Under The Human Rights Code, s. 2", "Human Rights Code"),
        (
            "Human Rights Code, R.S.O. 1990, c. H.19, s. 2",
            "Human Rights Code, R.S.O. 1990, c. H.19",
        ),
        ("section 2 of the HRC", "HRC"),
        ("the Act. Section 14 voids it", None),
        ("Section 14 voids it", None),
    ],
)
def test_the_statute_named_beside_a_citation(text: str, statute: str | None) -> None:
    assert {c.statute for c in read_draft(text).citations} == {statute}


def test_a_quote_reads_curly_and_straight_marks_within_a_paragraph() -> None:
    draft = read_draft('“A provision\nis void” and "this one"\n\nbut "not across\n\na blank line"')

    assert [q.text for q in draft.quotations] == ["A provision is void", "this one"]
    assert [draft.line(q.start) for q in draft.quotations] == [1, 2]


def test_a_quotation_mark_with_no_partner_is_not_paired_across_a_paragraph() -> None:
    draft = read_draft('He wrote "no. Then left.\n\nSection 1 says “A provision is void."')

    assert [(q.text, q.citation.cited if q.citation else None) for q in draft.quotations] == [
        ("A provision is void.", "s. 1")  # a curly mark opens and a straight one closes
    ]
    assert draft.unpaired_marks == (draft.text.index('"'),)
