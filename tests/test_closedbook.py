import json
import re
from pathlib import Path
from typing import Any

from lease_clause_checker import closedbook, pipeline
from lease_clause_checker.baseline import run
from lease_clause_checker.clauses import Clause
from lease_clause_checker.evaluation import closedbook_run
from lease_clause_checker.models import ModelSpec, model_by_key
from lease_clause_checker.providers import Completion
from lease_clause_checker.report import closedbook_table, error_lines
from lease_clause_checker.statute import Section

ROOT = Path(__file__).parents[1]
HAIKU = model_by_key("claude-haiku-5-5")
CLAUSES_SHA = "cd" * 32
WIDGET_QUOTE = "A provision in a tenancy agreement prohibiting widgets is void."


def clause(clause_id: str) -> Clause:
    return Clause.model_validate(
        {
            "id": clause_id,
            "text": f"No widgets ({clause_id}).",
            "label": "void",
            "kind": "clear",
            "section": "1",
            "also_accept": [],
            "rationale": "x",
            "quote": "x",
            "source": "x",
            "reviewed": True,
        }
    )


def answer(verdict: str, section: str, quote: str) -> str:
    return json.dumps({"verdict": verdict, "section": section, "quote": quote, "reason": "r"})


ANSWERS = {
    "c01": answer("void", "1", WIDGET_QUOTE),  # right, quote verified
    "c02": answer("void", "1", "A provision prohibiting widgets is void."),  # right, misquoted
    "c03": answer("allowed", "1", "“No widget” provisions void"),  # wrong, quotes the heading
    "c04": answer("unsure", "", ""),
    "c05": answer("allowed", "", ""),  # a verdict with no section or quote
}


def ask(results: Path) -> list[tuple[ModelSpec, dict[str, Any]]]:
    """Run the closed-book prompt on c01 to c05 with a model giving `ANSWERS`; the calls made."""
    calls = []

    def complete(spec: ModelSpec, request: dict[str, Any]) -> Completion:
        calls.append((spec, request))
        prompt = request["messages"][0]["content"]
        found = re.search(r"No widgets \((c\d\d)\)", prompt)
        assert found is not None
        clause_id = found.group(1)
        return Completion(ANSWERS[clause_id], spec.model_id, "end_turn", 100, 20, {})

    prompt = closedbook.load_prompt(ROOT / "prompts")
    summary = run([clause(i) for i in ANSWERS], [HAIKU], prompt, CLAUSES_SHA, results, complete)
    assert summary.failures == []
    return calls


def test_the_closed_book_question_has_no_statute_text_and_asks_for_the_pipeline_answer(
    tmp_path: Path,
) -> None:
    calls = ask(tmp_path)

    _, request = calls[0]
    prompt = request["messages"][0]["content"]
    assert "You are not given the text of the Act." in prompt
    assert "<sections>" not in prompt
    assert "$sections" not in prompt
    assert "Read the clause as a term of an ordinary private" in prompt  # the conventions
    assert request["output_config"]["format"]["schema"] == pipeline.ANSWER_SCHEMA
    saved = tmp_path / "closedbook-v1+conventions-v1" / HAIKU.key / "c01.json"
    assert saved.exists()


def test_each_clause_is_asked_once_and_never_again(tmp_path: Path) -> None:
    assert len(ask(tmp_path)) == len(ANSWERS)
    assert ask(tmp_path) == []


def test_the_verdict_counts_whether_or_not_the_quote_passes(
    tmp_path: Path, sections: dict[str, Section]
) -> None:
    ask(tmp_path)
    prompt = closedbook.load_prompt(ROOT / "prompts")
    clauses = [clause(i) for i in ANSWERS]

    model_run = closedbook_run(tmp_path, prompt, HAIKU, clauses, CLAUSES_SHA, sections)

    outcomes = {s.clause.id: (s.outcome, s.section_result) for s in model_run.scores}
    assert outcomes == {
        "c01": ("correct", "deciding"),
        "c02": ("correct", "deciding"),
        "c03": ("missed_void", "deciding"),
        "c04": ("unsure", "none"),
        "c05": ("missed_void", "none"),
    }
    checks = {r.clause.id: r.attempt.check for r in model_run.closed_book}
    assert checks == {
        "c01": "verified",
        "c02": "not_found",
        "c03": "heading",
        "c04": None,
        "c05": "no_quote",
    }

    [header, row] = closedbook_table([model_run], len(clauses)).splitlines()
    columns = [c.strip() for c in header.split("  ") if c.strip()]
    values = [v.strip() for v in row.split("  ") if v.strip()]
    cells = dict(zip(columns, values, strict=True))
    assert cells["verdict right"] == "2 of 5"
    assert cells["unsure"] == "1"
    assert cells["quote verified"] == "1 of 4"
    assert (cells["not found"], cells["heading"], cells["too short"]) == ("1", "1", "0")
    assert (cells["no such section"], cells["no quote"]) == ("0", "1")

    details = error_lines(model_run)
    assert details[0].startswith("c02 (clear void, s. 1): verdict right")
    assert details[0].endswith("checks: not found")
    assert [line.split(" ")[0] for line in details] == ["c02", "c03", "c04", "c05"]
