from pathlib import Path

import pytest

from lease_clause_checker.elaws import PUBLIC_URL
from lease_clause_checker.rules import Rule, RulesFileError, check_rules, load_rules
from lease_clause_checker.statute import Section, parse_sections

COMMITTED_RULES = Path(__file__).parents[1] / "data" / "rules.yaml"

VALID_YAML = """\
rules:
  - section: "1"
    heading: "“No widget” provisions void"
    paraphrase: "Widget bans are void."
    source: "https://www.ontario.ca/laws/statute/06r17#BK1"
  - section: "2.1"
    heading: "Gadget deposits"
    paraphrase: "Gadget deposits are limited."
    source: "https://www.ontario.ca/laws/statute/06r17#BK2"
"""


def write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "rules.yaml"
    path.write_text(text, encoding="utf-8")
    return path


@pytest.fixture
def sections(sample_html: str) -> dict[str, Section]:
    return parse_sections(sample_html)


def test_load_reads_rules_in_file_order(tmp_path: Path) -> None:
    rules = load_rules(write(tmp_path, VALID_YAML))

    assert [rule.section for rule in rules] == ["1", "2.1"]
    assert rules[0].paraphrase == "Widget bans are void."


def test_load_rejects_duplicate_sections(tmp_path: Path) -> None:
    duplicated = VALID_YAML.replace('section: "2.1"', 'section: "1"')

    with pytest.raises(RulesFileError, match="more than once: 1"):
        load_rules(write(tmp_path, duplicated))


def test_load_rejects_a_misspelt_field(tmp_path: Path) -> None:
    misspelt = VALID_YAML.replace("paraphrase:", "paraphrse:", 1)

    with pytest.raises(RulesFileError, match="paraphrse"):
        load_rules(write(tmp_path, misspelt))


def test_load_rejects_a_missing_field(tmp_path: Path) -> None:
    missing = VALID_YAML.replace(f'    source: "{PUBLIC_URL}#BK1"\n', "")

    with pytest.raises(RulesFileError, match="source"):
        load_rules(write(tmp_path, missing))


def test_load_rejects_a_blank_paraphrase(tmp_path: Path) -> None:
    blank = VALID_YAML.replace('"Widget bans are void."', '"  "')

    with pytest.raises(RulesFileError, match="paraphrase"):
        load_rules(write(tmp_path, blank))


def test_load_asks_for_quoted_section_numbers(tmp_path: Path) -> None:
    unquoted = VALID_YAML.replace('section: "2.1"', "section: 2.10")

    with pytest.raises(RulesFileError, match="must be quoted"):
        load_rules(write(tmp_path, unquoted))


@pytest.mark.parametrize("text", ["- section: '1'\n", "rules: []\n", ""])
def test_load_rejects_a_file_without_rules(tmp_path: Path, text: str) -> None:
    with pytest.raises(RulesFileError, match="non-empty top-level `rules:` list"):
        load_rules(write(tmp_path, text))


def test_check_passes_when_rules_match_the_act(
    tmp_path: Path, sections: dict[str, Section]
) -> None:
    assert check_rules(load_rules(write(tmp_path, VALID_YAML)), sections) == []


def test_check_reports_missing_section_wrong_heading_and_wrong_link(
    sections: dict[str, Section],
) -> None:
    rules = [
        Rule(section="99", heading="x", paraphrase="x", source=PUBLIC_URL),
        Rule(
            section="1",
            heading="No widget provisions void",
            paraphrase="x",
            source=f"{PUBLIC_URL}#BK1",
        ),
        Rule(section="2.1", heading="Gadget deposits", paraphrase="x", source=f"{PUBLIC_URL}#BK9"),
    ]

    problems = check_rules(rules, sections)

    assert len(problems) == 3
    assert problems[0] == "s. 99: not in the Act"
    assert "e-Laws has '“No widget” provisions void'" in problems[1]
    assert problems[2].endswith(f"expected {PUBLIC_URL}#BK2")


def test_committed_rules_file_is_well_formed() -> None:
    """Offline check of the committed file's shape only. Whether its headings and links match
    the real Act is checked by `lcc statute check-rules` and the live tests."""
    rules = load_rules(COMMITTED_RULES)

    assert rules
    assert all(rule.source.startswith(f"{PUBLIC_URL}#BK") for rule in rules)
