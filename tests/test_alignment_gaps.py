from pathlib import Path

import pytest

from lease_clause_checker import alignment_gaps, quotereview
from lease_clause_checker.alignment_gaps import AlignmentGapError
from lease_clause_checker.models import MODELS

CHECKS = (
    Path(__file__).parents[1] / "results" / "closedbook-v1+conventions-v1" / quotereview.CHECKS_FILE
)


def test_each_listed_gap_is_a_reviewed_quote_whose_note_names_the_provision() -> None:
    gaps = alignment_gaps.load()
    checks = quotereview.load_checks(CHECKS)

    assert [(g.model, g.clause, g.review) for g in gaps.quotes] == [
        ("claude-haiku-5-5", "c25", "108"),
        ("gemini-3.8-flash", "c18", "37(4)"),
        ("gemini-4-argon", "c24", "106(3)"),
    ]
    for gap in gaps.quotes:
        assert gap.model in {spec.key for spec in MODELS}
        assert f"s. {gap.review}" in checks[gap.model, gap.clause].note


def test_a_quote_listed_twice_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "gaps.yaml"
    row = "  - {model: m, clause: c01, review: '14'}\n"
    path.write_text("issue: 2\nquotes:\n" + row + row, encoding="utf-8")

    with pytest.raises(AlignmentGapError, match="listed twice"):
        alignment_gaps.load(path)


def test_a_gap_without_the_reviews_provision_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "gaps.yaml"
    path.write_text("issue: 2\nquotes:\n  - {model: m, clause: c01}\n", encoding="utf-8")

    with pytest.raises(AlignmentGapError, match=r"quotes\.0\.review"):
        alignment_gaps.load(path)
