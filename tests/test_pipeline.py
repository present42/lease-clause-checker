import dataclasses
import json
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from lease_clause_checker import cli
from lease_clause_checker.baseline import load_prompt
from lease_clause_checker.clauses import Clause, file_sha256, load_clauses
from lease_clause_checker.models import ModelSpec, model_by_key
from lease_clause_checker.pipeline import (
    ANSWER_SCHEMA,
    PipelineAnswer,
    PipelinePrompt,
    Result,
    load_pipeline_prompt,
    load_results,
    run,
)
from lease_clause_checker.providers import Completion
from lease_clause_checker.runs import RunSummary, StaleRecordError, Store, sha256_text
from lease_clause_checker.statute import Section

ROOT = Path(__file__).parents[1]
HAIKU = model_by_key("claude-haiku-5-5")
SHA = "ab" * 32
WHEN = datetime(2026, 11, 12, 12, 0, tzinfo=UTC)
# Text of the sample Act (tests/fixtures/elaws_sample.html), sections 1 and 2.1:
S1_QUOTE = "A provision in a tenancy agreement prohibiting widgets is void."
S2_TEXT = "A landlord may require a gadget deposit if,"
RETRY_MARK = "Your previous answer was:"
FAIL = "<the call fails>"


def clause(clause_id: str) -> Clause:
    return Clause.model_validate(
        {
            "id": clause_id,
            "text": f"Clause {clause_id}.",
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


def answer(verdict: str = "void", section: str = "1", quote: str = S1_QUOTE) -> str:
    return json.dumps({"verdict": verdict, "section": section, "quote": quote, "reason": "r"})


class ScriptedModels:
    """Gives each clause's first and retry answers from a script; records every prompt."""

    def __init__(self, script: dict[str, list[str]]) -> None:
        self.script = script
        self.prompts: list[str] = []

    def __call__(self, spec: ModelSpec, request: dict[str, Any]) -> Completion:
        assert request["output_config"]["format"]["schema"] == ANSWER_SCHEMA
        prompt = request["messages"][0]["content"]
        self.prompts.append(prompt)
        clause_id = prompt.split("<clause>\nClause ")[1].split(".")[0]
        text = self.script[clause_id][1 if RETRY_MARK in prompt else 0]
        if text == FAIL:
            raise RuntimeError("503 overloaded")
        return Completion(text, spec.model_id, "end_turn", 100, 20, {"id": "fake"})


@pytest.fixture
def prompt(sections: dict[str, Section]) -> PipelinePrompt:
    return load_pipeline_prompt(ROOT / "prompts", [sections["1"], sections["2.1"]])


def run_scripted(
    results_dir: Path,
    prompt: PipelinePrompt,
    sections: dict[str, Section],
    script: dict[str, list[str]],
) -> tuple[ScriptedModels, RunSummary]:
    models = ScriptedModels(script)
    store = Store(results_dir, prompt.version, SHA)
    clauses = [clause(clause_id) for clause_id in script]
    summary = run(clauses, [HAIKU], prompt, sections, store, models, now=lambda: WHEN)
    return models, summary


def saved_results(
    results_dir: Path, prompt: PipelinePrompt, sections: dict[str, Section], *ids: str
) -> list[Result]:
    store = Store(results_dir, prompt.version, SHA)
    return load_results(store, prompt, HAIKU, [clause(i) for i in ids], sections)


def test_a_verified_first_answer_is_final(
    tmp_path: Path, prompt: PipelinePrompt, sections: dict[str, Section]
) -> None:
    models, summary = run_scripted(tmp_path, prompt, sections, {"c01": [answer()]})

    [result] = saved_results(tmp_path, prompt, sections, "c01")
    assert len(models.prompts) == 1
    assert (summary.called, summary.cached, summary.failures) == (1, 0, [])
    assert [a.check for a in result.attempts] == ["verified"]
    assert (result.answer.verdict, result.answer.section, result.gave_up) == ("void", "1", False)
    assert result.score({"1", "2.1"}).outcome == "correct"


def test_the_prompts_send_the_statute_but_the_records_save_a_reference(
    tmp_path: Path, prompt: PipelinePrompt, sections: dict[str, Section]
) -> None:
    models, _ = run_scripted(tmp_path, prompt, sections, {"c01": [answer(section="2.1"), answer()]})

    reference = (
        f"sections 1, 2.1 of the Act, from e-Laws; SHA-256 {sha256_text(prompt.sections_text)}"
    )
    for sent, name in zip(models.prompts, ("c01.json", "c01.retry.json"), strict=True):
        saved = (tmp_path / prompt.version / HAIKU.key / name).read_text(encoding="utf-8")
        assert S2_TEXT in sent
        assert "Section 2.1: Gadget deposits" in sent
        assert S2_TEXT not in saved
        assert reference in saved


@pytest.mark.parametrize(
    ("first", "problem"),
    [
        (answer(quote="A provision prohibiting widgets is void."), "does not appear word for word"),
        (answer(section="2.1"), "does not appear word for word in section 2.1"),
        (answer(section="s. 2.1"), "does not appear word for word in section 2.1."),
        (answer(quote="Gadget deposits", section="2.1"), "is from a heading"),
        (answer(quote="is void"), "fewer than 5 words"),
        (answer(quote=""), "gave no quote"),
        (answer(section="99"), "Section 99 is not a section of the Act"),
        ('{"verdict": "void"}', "not a JSON object in the required format"),
        ("The clause is void.", "not a JSON object in the required format"),
    ],
)
def test_a_failed_check_is_retried_once_saying_what_failed(
    tmp_path: Path,
    prompt: PipelinePrompt,
    sections: dict[str, Section],
    first: str,
    problem: str,
) -> None:
    models, summary = run_scripted(tmp_path, prompt, sections, {"c01": [first, answer()]})

    [result] = saved_results(tmp_path, prompt, sections, "c01")
    retry_prompt = models.prompts[1]
    assert summary.called == 2
    assert retry_prompt.startswith(models.prompts[0])
    assert f"{RETRY_MARK}\n{first}\n" in retry_prompt
    assert problem in retry_prompt
    assert (result.answer.verdict, result.gave_up, len(result.attempts)) == ("void", False, 2)
    assert (tmp_path / prompt.version / HAIKU.key / "c01.retry.json").exists()


def test_two_failed_checks_give_unsure(
    tmp_path: Path, prompt: PipelinePrompt, sections: dict[str, Section]
) -> None:
    wrong = answer(section="2.1")
    run_scripted(tmp_path, prompt, sections, {"c01": [wrong, wrong]})

    [result] = saved_results(tmp_path, prompt, sections, "c01")
    assert [a.check for a in result.attempts] == ["not_found", "not_found"]
    assert (result.answer.verdict, result.answer.section, result.gave_up) == ("unsure", None, True)
    assert result.score({"1", "2.1"}).outcome == "unsure"


def test_an_unsure_answer_is_neither_checked_nor_retried(
    tmp_path: Path, prompt: PipelinePrompt, sections: dict[str, Section]
) -> None:
    models, _ = run_scripted(
        tmp_path, prompt, sections, {"c01": [answer(verdict="unsure", section="", quote="")]}
    )

    [result] = saved_results(tmp_path, prompt, sections, "c01")
    assert len(models.prompts) == 1
    assert (result.attempts[0].check, result.gave_up, result.answer.verdict) == (
        None,
        False,
        "unsure",
    )


def test_a_rerun_reuses_both_attempts_without_calling_a_model(
    tmp_path: Path, prompt: PipelinePrompt, sections: dict[str, Section]
) -> None:
    script = {"c01": [answer(section="2.1"), answer()], "c02": [answer()]}
    run_scripted(tmp_path, prompt, sections, script)

    models, summary = run_scripted(tmp_path, prompt, sections, script)

    assert models.prompts == []
    assert (summary.called, summary.cached) == (0, 3)


def test_a_retry_left_unmade_is_made_by_the_next_run(
    tmp_path: Path, prompt: PipelinePrompt, sections: dict[str, Section]
) -> None:
    script = {"c01": [answer(section="2.1"), answer()]}
    run_scripted(tmp_path, prompt, sections, script)
    (tmp_path / prompt.version / HAIKU.key / "c01.retry.json").unlink()
    assert saved_results(tmp_path, prompt, sections, "c01") == []  # incomplete: not scored

    models, summary = run_scripted(tmp_path, prompt, sections, script)

    assert [RETRY_MARK in p for p in models.prompts] == [True]
    assert (summary.called, summary.cached) == (1, 1)


def test_other_statute_text_under_the_same_version_is_refused(
    tmp_path: Path, prompt: PipelinePrompt, sections: dict[str, Section]
) -> None:
    run_scripted(tmp_path, prompt, sections, {"c01": [answer()]})
    fewer_sections = load_pipeline_prompt(ROOT / "prompts", [sections["1"]])

    with pytest.raises(StaleRecordError, match="new version"):
        saved_results(tmp_path, fewer_sections, sections, "c01")


def test_a_quote_is_checked_in_the_section_as_read(
    tmp_path: Path, prompt: PipelinePrompt, sections: dict[str, Section]
) -> None:
    models, _ = run_scripted(tmp_path, prompt, sections, {"c01": [answer(section="s. 1")]})

    [result] = saved_results(tmp_path, prompt, sections, "c01")
    assert len(models.prompts) == 1
    assert (result.attempts[0].check, result.answer.section) == ("verified", "1")


def test_amended_rule_section_text_under_the_same_version_is_refused(
    tmp_path: Path, prompt: PipelinePrompt, sections: dict[str, Section]
) -> None:
    run_scripted(tmp_path, prompt, sections, {"c01": [answer()]})
    amended = dataclasses.replace(sections["2.1"], text=sections["2.1"].text + " Amended.")
    amended_prompt = load_pipeline_prompt(ROOT / "prompts", [sections["1"], amended])

    with pytest.raises(StaleRecordError, match="new version"):
        saved_results(tmp_path, amended_prompt, sections, "c01")


def test_a_failed_retry_call_still_counts_the_first_call(
    tmp_path: Path, prompt: PipelinePrompt, sections: dict[str, Section]
) -> None:
    _, summary = run_scripted(tmp_path, prompt, sections, {"c01": [answer(section="2.1"), FAIL]})

    assert (summary.called, summary.cached) == (1, 0)
    assert summary.failures == ["claude-haiku-5-5 c01: RuntimeError: 503 overloaded"]


def test_a_stale_record_stops_the_run_before_any_call(
    tmp_path: Path, prompt: PipelinePrompt, sections: dict[str, Section]
) -> None:
    run_scripted(tmp_path, prompt, sections, {"c01": [answer()]})
    changed = dataclasses.replace(prompt, template=prompt.template.replace("Decide", "Judge"))
    models = ScriptedModels({"c01": [answer()], "c02": [answer()]})
    store = Store(tmp_path, prompt.version, SHA)

    with pytest.raises(StaleRecordError):
        run([clause("c01"), clause("c02")], [HAIKU], changed, sections, store, models, workers=2)

    assert models.prompts == []  # not even c02, which had no saved answer


def test_a_stale_retry_record_stops_the_run(
    tmp_path: Path, prompt: PipelinePrompt, sections: dict[str, Section]
) -> None:
    run_scripted(tmp_path, prompt, sections, {"c01": [answer(section="2.1"), answer()]})
    changed = dataclasses.replace(prompt, retry_template=prompt.retry_template + "\nThanks.")
    models = ScriptedModels({"c01": [answer(section="2.1"), answer()], "c02": [answer()]})
    store = Store(tmp_path, prompt.version, SHA)

    with pytest.raises(StaleRecordError, match="new version"):
        run([clause("c01"), clause("c02")], [HAIKU], changed, sections, store, models, workers=2)
    assert models.prompts == []  # the saved retry is checked before c02 is asked


@pytest.mark.parametrize(
    ("written", "read"),
    [
        ("14", "14"),
        (" s. 14 ", "14"),
        ("s 14", "14"),
        ("ss. 14", "14"),
        ("Section 14", "14"),
        ("RTA s. 14", "14"),
        ("14(1)(a)", "14"),
        ("14 (1)", "14"),
        ("14.", "14"),
        ("8.1", "8.1"),
        ("14 or 15", "14 or 15"),  # not one section: left as written, so it can't verify
    ],
)
def test_the_cited_section_is_read_as_a_bare_number(written: str, read: str) -> None:
    cited = PipelineAnswer(verdict="void", section=written, quote="", reason="").cited_section

    assert cited == read


def test_pipeline_and_baseline_prompts_share_the_conventions_word_for_word(
    prompt: PipelinePrompt,
) -> None:
    conventions = (ROOT / "prompts" / "conventions-v1.txt").read_text(encoding="utf-8").strip()
    pipeline_text = prompt.first(HAIKU, clause("c01")).prompt
    baseline_text = load_prompt(ROOT / "prompts").render("Clause c01.")

    assert prompt.version == "pipeline-v1+conventions-v1"
    assert conventions in pipeline_text
    assert conventions in baseline_text
    assert "$" not in pipeline_text


def test_cli_pipeline_run_then_report(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    cache_dir: Path,
) -> None:
    clauses_path = tmp_path / "clauses.jsonl"
    shutil.copyfile(ROOT / "data" / "clauses.jsonl", clauses_path)
    rules_path = tmp_path / "rules.yaml"
    rules_path.write_text(
        'rules:\n  - section: "1"\n    heading: x\n    paraphrase: x\n    source: x\n',
        encoding="utf-8",
    )

    prompts: list[str] = []

    def always_section_1(spec: ModelSpec, request: dict[str, Any]) -> Completion:
        prompts.append(request["messages"][0]["content"])
        return Completion(answer(), spec.model_id, "end_turn", 100, 20, {})

    monkeypatch.setattr(cli, "live_models", lambda specs: always_section_1)
    common = ["--models", "claude-haiku-5-5", "--clauses", str(clauses_path)]
    common += ["--results", str(tmp_path / "results"), "--rules", str(rules_path)]
    cache = ["--cache-dir", str(cache_dir)]

    run_exit = cli.main([*cache, "pipeline", "run", *common])
    report_exit = cli.main([*cache, "pipeline", "report", "--details", *common])

    output = capsys.readouterr().out
    clauses = load_clauses(clauses_path)
    voids = sum(c.label == "void" for c in clauses)
    assert (run_exit, report_exit) == (0, 0)
    row = next(line for line in output.splitlines() if line.startswith("claude-haiku-5-5"))
    # Every answer is "void, s. 1" with a verified quote: right on the void clauses, no
    # unsure answers, no retries, and s. 1 of the sample Act is no clause's deciding section.
    assert row.split()[2:6] == [str(len(clauses)), "of", str(len(clauses)), f"{voids}"]
    assert "Not legal advice" in output
    # Only the sections in the rules file are sent:
    assert "Section 1: " in prompts[0]
    assert "Section 2.1: " not in prompts[0]


def test_cli_pipeline_draft_saves_answers_tied_to_the_draft_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cache_dir: Path
) -> None:
    draft = tmp_path / "stress.jsonl"
    draft.write_bytes((ROOT / "data" / "clauses.jsonl").read_bytes().replace(b'"c01"', b'"s01"'))
    monkeypatch.setattr(
        cli,
        "live_models",
        lambda specs: (
            lambda spec, request: Completion(answer(), spec.model_id, "end_turn", 1, 1, {})
        ),
    )
    args = ["--models", "claude-haiku-5-5", "--clauses", str(draft), "--draft"]
    rules_path = tmp_path / "rules.yaml"
    rules_path.write_text(
        'rules:\n  - section: "1"\n    heading: x\n    paraphrase: x\n    source: x\n',
        encoding="utf-8",
    )
    args += ["--results", str(tmp_path / "results"), "--rules", str(rules_path)]

    run_exit = cli.main(["--cache-dir", str(cache_dir), "pipeline", "run", *args])
    report_exit = cli.main(["--cache-dir", str(cache_dir), "pipeline", "report", *args])

    saved = tmp_path / "results" / "pipeline-v1+conventions-v1" / "claude-haiku-5-5" / "s01.json"
    assert (run_exit, report_exit) == (0, 0)
    assert json.loads(saved.read_text(encoding="utf-8"))["clauses_sha256"] == file_sha256(draft)
