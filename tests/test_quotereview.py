from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest

from lease_clause_checker import quotereview
from lease_clause_checker.clauses import Clause
from lease_clause_checker.closedbook import ClosedBookResult
from lease_clause_checker.models import MODELS, ModelSpec
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


def run_with_quote(quote: str, spec: ModelSpec = HAIKU) -> ModelRun:
    answer = PipelineAnswer(verdict="void", section="14", quote=quote, reason="r")
    record = RunRecord(
        clause_id="c01",
        model_key=spec.key,
        model_id=spec.model_id,
        served_model=spec.model_id,
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
    return ModelRun(spec, [record], [result.score({"14"})], closed_book=[result])


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
        "model,clause,category,hand_check,meaning_changed,misleads_here,note\n"
        "claude-haiku-5-5,c01,not_in_act,,,,\n"
    )
    checks_file.write_text(
        "model,clause,category,hand_check,meaning_changed,misleads_here,note\n"
        "claude-haiku-5-5,c01,cosmetic,agree,yes,unsure,invented wording\n"
    )

    checks = quotereview.write_review([run], tmp_path, "NOTICE", "cb-v1")

    assert checks_file.read_text() == (
        "model,clause,category,hand_check,meaning_changed,misleads_here,note\n"
        "claude-haiku-5-5,c01,not_in_act,agree,yes,unsure,invented wording\n"
    )
    assert quotereview.hand_check_line([run], checks) == (
        "1 of the 1 categories have been checked by hand; the hand check agreed with 1 of them. "
        "Read by hand for meaning, 1 of 1 quotes change what the Act says. Whether each would "
        "mislead on the clause being checked, in the author's reading (not legal advice): yes 0, "
        "unsure 1, no 0."
    )
    page = (tmp_path / quotereview.REVIEW_FILE).read_text()
    assert "| agree; meaning changed: yes; misleads here: unsure; invented wording |" in page


@pytest.mark.parametrize(
    ("hand_check", "meaning", "line"),
    [
        (
            "",
            "",
            "The categories have not yet been checked by hand. No quote has yet been read by "
            "hand for meaning.",
        ),
        ("cosmetic", "", "the hand check agreed with 0 of them. No quote has yet been read"),
        ("Not_In_Act", "", "the hand check agreed with 1 of them. No quote has yet been read"),
        ("agree", "no", "Read by hand for meaning, 0 of 1 quotes change what the Act says."),
        (
            "",
            "yes",
            "not yet been checked by hand. Read by hand for meaning, 1 of 1 quotes change what "
            "the Act says. Whether each would mislead on the clause being checked has not yet been "
            "judged.",
        ),
    ],
)
def test_the_hand_check_line_counts_agreement_and_meaning_apart(
    tmp_path: Path, hand_check: str, meaning: str, line: str
) -> None:
    path = tmp_path / quotereview.CHECKS_FILE
    path.write_text(
        "model,clause,category,hand_check,meaning_changed,note\n"
        f"claude-haiku-5-5,c01,not_in_act,{hand_check},{meaning},\n"
    )

    checks = quotereview.load_checks(path)

    assert line in quotereview.hand_check_line([run_with_quote("x y z w v")], checks)


def test_a_hand_check_must_be_agree_or_a_category(tmp_path: Path) -> None:
    path = tmp_path / quotereview.CHECKS_FILE
    path.write_text("model,clause,category,hand_check,note\nclaude-haiku-5-5,c01,x,yes,\n")

    with pytest.raises(quotereview.HandCheckError, match="'yes' is not 'agree'"):
        quotereview.load_checks(path)


def test_meaning_changed_must_be_yes_or_no(tmp_path: Path) -> None:
    path = tmp_path / quotereview.CHECKS_FILE
    path.write_text(
        "model,clause,category,hand_check,meaning_changed,note\n"
        "claude-haiku-5-5,c01,x,agree,maybe,\n"
    )

    with pytest.raises(quotereview.HandCheckError, match="'maybe' is not 'yes' or 'no'"):
        quotereview.load_checks(path)


def test_a_file_from_before_the_meaning_column_still_loads(tmp_path: Path) -> None:
    path = tmp_path / quotereview.CHECKS_FILE
    path.write_text("model,clause,category,hand_check,note\nclaude-haiku-5-5,c01,x,agree,n\n")

    [check] = quotereview.load_checks(path).values()

    assert check == quotereview.HandCheck("agree", "", "", "n")


def test_meaning_changes_are_also_counted_among_content_words_differ(tmp_path: Path) -> None:
    run = run_with_quote("x y z w v")
    [result] = run.closed_book
    assert result.quote is not None
    content = replace(result, quote=replace(result.quote, category="content_words_differ"))
    run = replace(run, closed_book=[content])
    path = tmp_path / quotereview.CHECKS_FILE
    path.write_text(
        "model,clause,category,hand_check,meaning_changed,note\n"
        "claude-haiku-5-5,c01,content_words_differ,,no,\n"
    )

    line = quotereview.hand_check_line([run], quotereview.load_checks(path))

    assert line.endswith(
        "0 of 1 quotes change what the Act says (0 of the 1 that the code sorted as content "
        "words differ)."
    )


def test_a_partial_hand_check_says_so_far(tmp_path: Path) -> None:
    runs = [run_with_quote("x y z w v"), run_with_quote("x y z w v", MODELS[1])]
    path = tmp_path / quotereview.CHECKS_FILE
    path.write_text(
        "model,clause,category,hand_check,meaning_changed,note\n"
        "claude-haiku-5-5,c01,not_in_act,agree,,\n"
    )

    line = quotereview.hand_check_line(runs, quotereview.load_checks(path))

    assert line.startswith("1 of the 2 categories have been checked by hand so far; the hand")


@pytest.mark.parametrize("meaning", ["", "no"])
def test_misleads_here_is_only_for_a_quote_that_changes_the_meaning(
    tmp_path: Path, meaning: str
) -> None:
    path = tmp_path / quotereview.CHECKS_FILE
    path.write_text(
        "model,clause,category,hand_check,meaning_changed,misleads_here,note\n"
        f"claude-haiku-5-5,c01,x,agree,{meaning},no,\n"
    )

    with pytest.raises(quotereview.HandCheckError, match="only for a quote whose meaning_changed"):
        quotereview.load_checks(path)


def test_misleads_here_must_be_yes_no_or_unsure(tmp_path: Path) -> None:
    path = tmp_path / quotereview.CHECKS_FILE
    path.write_text(
        "model,clause,category,hand_check,meaning_changed,misleads_here,note\n"
        "claude-haiku-5-5,c01,x,agree,yes,maybe,\n"
    )

    with pytest.raises(quotereview.HandCheckError, match="'maybe' is not 'yes', 'no' or 'unsure'"):
        quotereview.load_checks(path)


@pytest.mark.parametrize(
    ("misleads", "line"),
    [
        (
            ("yes", "unsure", "no"),
            "3 of 3 quotes change what the Act says. Whether each would mislead on the clause "
            "being checked, in the author's reading (not legal advice): yes 1, unsure 1, no 1.",
        ),
        (
            ("yes", "Yes", ""),
            "(not legal advice): yes 2, unsure 0, no 0, not yet judged 1.",
        ),
    ],
)
def test_the_hand_check_line_counts_the_quotes_that_mislead_on_the_clause(
    tmp_path: Path, misleads: tuple[str, ...], line: str
) -> None:
    specs = MODELS[:3]
    path = tmp_path / quotereview.CHECKS_FILE
    path.write_text(
        "model,clause,category,hand_check,meaning_changed,misleads_here,note\n"
        + "".join(
            f"{spec.key},c01,not_in_act,,yes,{misled},\n"
            for spec, misled in zip(specs, misleads, strict=True)
        )
    )

    runs = [run_with_quote("x y z w v", spec) for spec in specs]

    assert quotereview.hand_check_line(runs, quotereview.load_checks(path)).endswith(line)
