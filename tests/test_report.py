import json
import re
from datetime import UTC, datetime

from lease_clause_checker.clauses import Clause
from lease_clause_checker.models import model_by_key
from lease_clause_checker.pipeline import Attempt, Result, assess
from lease_clause_checker.report import ModelRun, error_lines, pipeline_table, summary_table
from lease_clause_checker.runs import RunRecord
from lease_clause_checker.scoring import score
from lease_clause_checker.statute import Section

HAIKU = model_by_key("claude-haiku-5-5")
S14 = "A provision in a tenancy agreement prohibiting pets is void."  # synthetic text
SECTION_14 = Section("14", "No pets", S14, "BK19")
CLAUSE = Clause.model_validate(
    {
        "id": "c01",
        "text": "No pets.",
        "label": "void",
        "kind": "clear",
        "section": "14",
        "also_accept": [],
        "rationale": "x",
        "quote": "x",
        "source": "x",
        "reviewed": True,
    }
)


def record(text: str, finish_reason: str) -> RunRecord:
    return RunRecord(
        clause_id="c01",
        model_key=HAIKU.key,
        model_id=HAIKU.model_id,
        served_model=HAIKU.model_id,
        prompt_version="test-v1",
        prompt_sha256="0" * 64,
        clauses_sha256="0" * 64,
        request={},
        requested_at=datetime(2026, 11, 11, tzinfo=UTC),
        text=text,
        finish_reason=finish_reason,
        input_tokens=1_000_000,
        output_tokens=1_000_000,
        raw={},
    )


def model_run(text: str, finish_reason: str = "end_turn") -> ModelRun:
    saved = record(text, finish_reason)
    return ModelRun(HAIKU, [saved], [score(CLAUSE, saved.text, {"14"})])


def test_an_answer_cut_off_by_the_token_limit_is_counted_and_listed() -> None:
    cut_off = model_run("The clause is void because", finish_reason="max_tokens")

    row = summary_table([cut_off], clause_count=1).splitlines()[1].split()

    assert row[-2:] == ["1", "0.6000"]  # one unfinished answer; 1M tokens in and out
    assert error_lines(cut_off) == [
        "c01 (clear void, s. 14): no readable verdict; cited none, no section",
        "c01: answer ended with max_tokens",
    ]


def test_a_right_answer_lists_no_errors() -> None:
    right = model_run("Verdict: not enforceable\nSection: 14")

    assert error_lines(right) == ["no wrong verdicts or sections"]


def test_a_model_without_saved_answers_says_so() -> None:
    assert error_lines(ModelRun(HAIKU, [], [])) == ["no saved answers"]


def attempt(text: str) -> Attempt:
    return assess(record(text, "end_turn"), {"14": SECTION_14})


def pipeline_answer(quote: str) -> str:
    return json.dumps({"verdict": "void", "section": "14", "quote": quote, "reason": ""})


def columns(table: str) -> list[dict[str, str]]:
    header, *rows = [re.split(r"\s{2,}", line) for line in table.splitlines()]
    return [dict(zip(header, row, strict=True)) for row in rows]


def test_pipeline_rows_count_unsure_gave_up_and_retried_and_lines_show_each_check() -> None:
    wrong, right = pipeline_answer("No pets are allowed here."), pipeline_answer(S14)
    fixed = Result(CLAUSE, (attempt(wrong), attempt(right)))
    gave_up = Result(CLAUSE, (attempt(wrong), attempt(wrong)))
    runs = [
        ModelRun(HAIKU, [a.record for a in r.attempts], [r.score({"14"})], [r])
        for r in (fixed, gave_up)
    ]

    rows = columns(pipeline_table(runs, clause_count=1))

    counts = [(row["unsure"], row["gave up"], row["retried"]) for row in rows]
    assert counts == [("0", "0", "1"), ("1", "1", "1")]
    assert error_lines(runs[0]) == [
        "c01 (clear void, s. 14): verdict right; cited s. 14, the deciding section; "
        "checks: not found, then verified"
    ]
    unreadable = Result(CLAUSE, (attempt("Void, s. 14."), attempt(right)))
    records = [a.record for a in unreadable.attempts]
    unreadable_run = ModelRun(HAIKU, records, [unreadable.score({"14"})], [unreadable])
    assert error_lines(unreadable_run)[0].endswith("checks: unreadable, then verified")
    assert error_lines(runs[1]) == [
        "c01 (clear void, s. 14): unsure; cited none, no section; checks: not found, then not found"
    ]
