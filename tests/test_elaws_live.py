"""Opt-in check against the real e-Laws API: `uv run pytest -m live`. One request; free.

Checks properties of the real Act that the small synthetic fixture can't show.
"""

from pathlib import Path

import httpx
import pytest

from lease_clause_checker import elaws
from lease_clause_checker.clauses import check_clauses, load_clauses
from lease_clause_checker.rules import check_rules, load_rules
from lease_clause_checker.statute import Section, parse_sections

pytestmark = pytest.mark.live

COMMITTED_RULES = Path(__file__).parents[1] / "data" / "rules.yaml"
DATA = Path(__file__).parents[1] / "data"


@pytest.fixture(scope="module")
def sections() -> dict[str, Section]:
    with httpx.Client() as client:
        act = elaws.fetch_act(client)
    assert "Residential Tenancies Act, 2006" in act.title
    return parse_sections(act.content_html)


def test_committed_rules_match_the_current_act(sections: dict[str, Section]) -> None:
    rules = load_rules(COMMITTED_RULES)

    assert check_rules(rules, sections) == []
    assert all(sections[rule.section].text for rule in rules)


def test_no_citation_or_repeal_debris_left_in_text(sections: dict[str, Section]) -> None:
    lines = [line for section in sections.values() for line in section.text.splitlines()]

    # Legitimate list items end in ";" or ":"; debris is a separator after a removed citation.
    assert [line for line in lines if line.endswith(" ;")] == []
    assert [line for line in lines if line.endswith("Repealed:")] == []


@pytest.mark.parametrize("clauses_file", ["clauses.jsonl", "stress.jsonl"])
def test_committed_clauses_quote_the_current_act(
    clauses_file: str, sections: dict[str, Section]
) -> None:
    clauses = load_clauses(DATA / clauses_file)

    assert check_clauses(clauses, load_rules(COMMITTED_RULES), sections) == []
