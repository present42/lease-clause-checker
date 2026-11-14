from datetime import UTC, datetime
from pathlib import Path

import pytest

from lease_clause_checker import quotereview
from lease_clause_checker.clauses import Clause
from lease_clause_checker.closedbook import ClosedBookResult
from lease_clause_checker.models import MODELS
from lease_clause_checker.pipeline import Attempt, PipelineAnswer
from lease_clause_checker.quotediff import Classified
from lease_clause_checker.report import ModelRun
from lease_clause_checker.runs import RunRecord

HAIKU = MODELS[0]
CLAUSE = Clause(
    id="c01",
    text="Clause.",
    label="void",
    kind="clear",
    section="14",
    also_accept=[],
    rationale="Why.",
    quote="decisive words",
    source="test",
    reviewed=True,
)


def run_with_quote(quote: str) -> ModelRun:
    answer = PipelineAnswer(verdict="void", section="14", quote=quote, reason="r")
    record = RunRecord(
        clause_id="c01",
        model_key=HAIKU.key,
        model_id=HAIKU.model_id,
        served_model=HAIKU.model_id,
        prompt_version="cb-v1",
        prompt_sha256="0" * 64,
        clauses_sha256="0" * 64,
        request={},
        requested_at=datetime(2026, 11, 14, tzinfo=UTC),
        text=answer.model_dump_json(),
        finish_reason="end_turn",
        input_tokens=1,
        output_tokens=1,
        raw={},
    )
    attempt = Attempt(record, answer, "not_found")
    classified = Classified("not_in_act", "not_found", "not_found", None)
    result = ClosedBookResult(CLAUSE, attempt, classified)
    return ModelRun(HAIKU, [record], [result.score({"14"})], closed_book=[result])


def test_a_quote_with_a_pipe_stays_in_its_cell() -> None:
    page = quotereview.review_page([run_with_quote("pets | animals")], {}, "NOTICE", "cb-v1")

    [row] = [line for line in page.splitlines() if line.startswith("| c01")]
    assert "“pets \\| animals”" in row
    assert row.count(" | ") == 8  # nine cells


def test_regenerating_keeps_the_hand_checks_and_refreshes_the_category(tmp_path: Path) -> None:
    run = run_with_quote("pets are banned")
    quotereview.write_review([run], tmp_path, "NOTICE", "cb-v1")
    checks_file = tmp_path / quotereview.CHECKS_FILE
    assert checks_file.read_text() == (
        "model,clause,category,hand_check,note\nclaude-haiku-5-5,c01,not_in_act,,\n"
    )
    checks_file.write_text(
        "model,clause,category,hand_check,note\n"
        "claude-haiku-5-5,c01,cosmetic,agree,invented wording\n"
    )

    checks = quotereview.write_review([run], tmp_path, "NOTICE", "cb-v1")

    assert checks_file.read_text() == (
        "model,clause,category,hand_check,note\n"
        "claude-haiku-5-5,c01,not_in_act,agree,invented wording\n"
    )
    assert quotereview.hand_check_line([run], checks) == (
        "Every one of the 1 quotes was checked by hand; the hand check agreed with the category "
        "on 1."
    )
    page = (tmp_path / quotereview.REVIEW_FILE).read_text()
    assert "| agree: invented wording |" in page


@pytest.mark.parametrize(
    ("hand_check", "line"),
    [
        ("", "The categories have not yet been checked by hand."),
        ("cosmetic", "the hand check agreed with the category on 0."),
        ("not_in_act", "the hand check agreed with the category on 1."),
    ],
)
def test_the_hand_check_line_counts_agreement(tmp_path: Path, hand_check: str, line: str) -> None:
    path = tmp_path / quotereview.CHECKS_FILE
    path.write_text(
        f"model,clause,category,hand_check,note\nclaude-haiku-5-5,c01,not_in_act,{hand_check},\n"
    )

    checks = quotereview.load_checks(path)

    assert quotereview.hand_check_line([run_with_quote("x y z w v")], checks).endswith(line)


def test_a_hand_check_must_be_agree_or_a_category(tmp_path: Path) -> None:
    path = tmp_path / quotereview.CHECKS_FILE
    path.write_text("model,clause,category,hand_check,note\nclaude-haiku-5-5,c01,x,yes,\n")

    with pytest.raises(quotereview.HandCheckError, match="'yes' is not 'agree'"):
        quotereview.load_checks(path)
