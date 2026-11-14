import pytest

from lease_clause_checker.statute import (
    Section,
    SectionNotFoundError,
    StatuteParseError,
    parse_sections,
    select_sections,
)

SECTION_ONE = '<p class="section"><a name="BK1"></a><b>1 </b>One.</p>'


@pytest.fixture
def parsed(sample_html: str) -> dict[str, Section]:
    return parse_sections(sample_html)


def test_finds_in_force_sections_in_order(parsed: dict[str, Section]) -> None:
    assert list(parsed) == ["1", "2.1", "4"]


def test_heading_is_the_headnote_just_before_the_section(parsed: dict[str, Section]) -> None:
    assert parsed["1"].heading == "“No widget” provisions void"
    assert parsed["2.1"].heading == "Gadget deposits"


def test_section_after_a_part_heading_has_no_heading(parsed: dict[str, Section]) -> None:
    assert parsed["4"].heading == ""


def test_text_drops_number_and_citation_and_collapses_whitespace(
    parsed: dict[str, Section],
) -> None:
    assert parsed["1"].text == "A provision in a tenancy agreement prohibiting widgets is void."
    assert parsed["1"].anchor == "BK1"


def test_text_joins_provisions_and_omits_subsection_headnotes(
    parsed: dict[str, Section],
) -> None:
    assert parsed["2.1"].text.splitlines() == [
        "(1) A landlord may require a gadget deposit if,",
        "(a) the tenant agrees in writing; and",
        "(b) the deposit is returned at the end of the tenancy.",
        "(2) The deposit shall not exceed one month’s rent.",
    ]


def test_headnotes_of_in_force_subsections_are_kept_apart(parsed: dict[str, Section]) -> None:
    # Not "Same, repealed" (a repealed subsection) or "Same, interest" (not yet in force).
    assert parsed["2.1"].headnotes == ("Same, amount",)
    assert parsed["1"].headnotes == ()


def test_provisions_not_yet_in_force_are_excluded(parsed: dict[str, Section]) -> None:
    assert "3.1" not in parsed
    assert all("gizmo" not in section.text.lower() for section in parsed.values())
    assert "Interest on the deposit" not in parsed["2.1"].text


def test_repealed_provisions_and_sections_are_excluded(parsed: dict[str, Section]) -> None:
    assert "3" not in parsed
    assert all("Repealed" not in section.text for section in parsed.values())


def test_omitted_placeholder_sections_are_excluded(parsed: dict[str, Section]) -> None:
    assert "5-9" not in parsed


def test_toc_entry_outside_the_toc_table_fails_loudly() -> None:
    html = SECTION_ONE + '<p class="table">Stray TOC title</p>'

    with pytest.raises(StatuteParseError, match="Stray TOC title"):
        parse_sections(html)


def test_spacer_paragraph_with_text_fails_loudly(sample_html: str) -> None:
    html = sample_html.replace('<p class="MsoNormal"></p>', '<p class="MsoNormal">Hidden text</p>')

    with pytest.raises(StatuteParseError, match="Hidden text"):
        parse_sections(html)


def test_unrecognised_paragraph_class_fails_loudly(sample_html: str) -> None:
    html = sample_html.replace('<p class="line">', '<p class="newKind">')

    with pytest.raises(StatuteParseError, match="newKind"):
        parse_sections(html)


def test_provision_outside_a_section_fails_loudly() -> None:
    html = '<p class="partnum">PART I</p><p class="subsection">(1) Orphan text.</p>'

    with pytest.raises(StatuteParseError, match="Orphan"):
        parse_sections(html)


def test_not_yet_in_force_section_closes_the_previous_section() -> None:
    html = (
        SECTION_ONE + '<p class="Ysection">1.1 Future.</p>'
        '<p class="subsection">(2) Belongs to no in-force section.</p>'
    )

    with pytest.raises(StatuteParseError, match="Belongs to no"):
        parse_sections(html)


def test_section_without_an_anchor_fails_loudly() -> None:
    with pytest.raises(StatuteParseError, match="Section 1 has no anchor"):
        parse_sections('<p class="section"><b>1 </b>One.</p>')


def test_duplicate_section_number_fails_loudly() -> None:
    html = SECTION_ONE + SECTION_ONE

    with pytest.raises(StatuteParseError, match="twice"):
        parse_sections(html)


def test_select_returns_requested_order(parsed: dict[str, Section]) -> None:
    assert [section.number for section in select_sections(parsed, ["4", "1"])] == ["4", "1"]


def test_select_names_every_missing_section(parsed: dict[str, Section]) -> None:
    with pytest.raises(SectionNotFoundError, match=r"3\.1, 99"):
        select_sections(parsed, ["1", "3.1", "99"])
