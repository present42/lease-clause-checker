import json
import shutil
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from lease_clause_checker import cli, providers
from lease_clause_checker.baseline import Prompt, load_prompt, load_records, run
from lease_clause_checker.clauses import (
    FROZEN_CLAUSES_SHA256,
    FROZEN_STRESS_SHA256,
    Clause,
    file_sha256,
    load_clauses,
)
from lease_clause_checker.models import ModelSpec, model_by_key
from lease_clause_checker.providers import Completion
from lease_clause_checker.runs import RunRecord, RunSummary, StaleRecordError

ROOT = Path(__file__).parents[1]
HAIKU = model_by_key("claude-haiku-5-5")
OPUS = model_by_key("claude-opus-5-5")
PROMPT = Prompt(version="test-v1", template="Is this void? $clause")
WHEN = datetime(2026, 11, 11, 12, 0, tzinfo=UTC)
CLAUSES_SHA = "ab" * 32


def saved(results: Path, model_key: str, clause_id: str) -> Path:
    return results / "test-v1" / model_key / f"{clause_id}.json"


def records(results: Path, spec: ModelSpec, *ids: str) -> dict[str, RunRecord]:
    return load_records(results, PROMPT, spec, clauses(*ids), CLAUSES_SHA)


def clauses(*ids: str) -> list[Clause]:
    return [
        Clause.model_validate(
            {
                "id": clause_id,
                "text": f"Clause {clause_id}.",
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
        for clause_id in ids
    ]


class FakeModels:
    """Answers every question, records the calls, and fails on the prompts it's told to."""

    def __init__(self, fail_on: str = "") -> None:
        self.calls: list[tuple[str, str]] = []
        self.fail_on = fail_on

    def __call__(self, spec: ModelSpec, request: dict[str, Any]) -> Completion:
        prompt = request["messages"][0]["content"]
        self.calls.append((spec.key, prompt))
        if self.fail_on and self.fail_on in prompt:
            raise RuntimeError("503 overloaded")
        return Completion(
            text="Verdict: not enforceable\nSection: 14",
            served_model=spec.model_id,
            finish_reason="end_turn",
            input_tokens=100,
            output_tokens=40,
            raw={"id": "fake"},
        )


def run_fake(
    results: Path, models: FakeModels, ids: tuple[str, ...] = ("c01", "c02")
) -> RunSummary:
    return run(clauses(*ids), [HAIKU, OPUS], PROMPT, CLAUSES_SHA, results, models, now=lambda: WHEN)


def test_run_saves_one_record_per_model_and_clause(tmp_path: Path) -> None:
    models = FakeModels()

    summary = run_fake(tmp_path, models)

    assert sorted(models.calls) == sorted(
        (spec.key, f"Is this void? Clause {cid}.")
        for spec in (HAIKU, OPUS)
        for cid in ("c01", "c02")
    )
    record = records(tmp_path, OPUS, "c01")["c01"]
    assert record.model_id == "claude-opus-5-5"
    assert record.prompt_version == "test-v1"
    assert record.clauses_sha256 == CLAUSES_SHA
    assert record.requested_at == WHEN
    assert record.request["messages"] == [{"role": "user", "content": "Is this void? Clause c01."}]
    assert (record.input_tokens, record.output_tokens, record.raw) == (100, 40, {"id": "fake"})
    assert (summary.called, summary.cached, summary.failures) == (4, 0, [])


class TickingModels(FakeModels):
    """A fake model whose every call moves the clock on by a minute."""

    def __init__(self) -> None:
        super().__init__()
        self.clock = WHEN

    def now(self) -> datetime:
        return self.clock

    def __call__(self, spec: ModelSpec, request: dict[str, Any]) -> Completion:
        self.clock += timedelta(minutes=1)
        return super().__call__(spec, request)


def test_record_time_is_taken_before_the_call(tmp_path: Path) -> None:
    models = TickingModels()

    run(clauses("c01"), [HAIKU], PROMPT, CLAUSES_SHA, tmp_path, models, now=models.now)

    assert records(tmp_path, HAIKU, "c01")["c01"].requested_at == WHEN


def test_an_interrupt_cancels_the_calls_still_queued(tmp_path: Path) -> None:
    class InterruptedModels(FakeModels):
        def __call__(self, spec: ModelSpec, request: dict[str, Any]) -> Completion:
            completion = super().__call__(spec, request)
            if len(self.calls) == 1:
                raise KeyboardInterrupt
            time.sleep(0.2)  # later calls take long enough for the interrupt to be handled
            return completion

    models = InterruptedModels()
    ids = ("c01", "c02", "c03", "c04", "c05")

    with pytest.raises(KeyboardInterrupt):
        run(clauses(*ids), [HAIKU], PROMPT, CLAUSES_SHA, tmp_path, models, workers=1)

    # The worker may start one more call before the interrupt reaches the main thread; the
    # rest are cancelled. Without cancelling, all five would run.
    assert len(models.calls) <= 2


def test_rerun_reuses_saved_answers_without_calling_a_model(tmp_path: Path) -> None:
    run_fake(tmp_path, FakeModels())
    models = FakeModels()

    summary = run_fake(tmp_path, models, ids=("c01", "c02", "c03"))

    assert models.calls == [
        ("claude-haiku-5-5", "Is this void? Clause c03."),
        ("claude-opus-5-5", "Is this void? Clause c03."),
    ]
    assert (summary.called, summary.cached) == (2, 4)


def test_a_failed_call_is_reported_and_retried_next_run(tmp_path: Path) -> None:
    summary = run_fake(tmp_path, FakeModels(fail_on="c02"))

    assert summary.called == 2
    assert sorted(summary.failures) == [
        "claude-haiku-5-5 c02: RuntimeError: 503 overloaded",
        "claude-opus-5-5 c02: RuntimeError: 503 overloaded",
    ]
    assert not saved(tmp_path, "claude-haiku-5-5", "c02").exists()
    models = FakeModels()
    run_fake(tmp_path, models)
    assert len(models.calls) == 2


def test_a_changed_prompt_with_the_same_version_is_refused(tmp_path: Path) -> None:
    run_fake(tmp_path, FakeModels())
    changed = Prompt(version="test-v1", template="Is this lawful? $clause")

    models = FakeModels()

    with pytest.raises(StaleRecordError, match="new version"):
        run(clauses("c01", "c03"), [HAIKU], changed, CLAUSES_SHA, tmp_path, models, workers=2)
    assert models.calls == []  # not even c03, which had no saved answer


def test_a_changed_request_with_the_same_prompt_version_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_fake(tmp_path, FakeModels())
    monkeypatch.setattr(providers, "MAX_OUTPUT_TOKENS", 8_000)

    with pytest.raises(StaleRecordError, match="new version"):
        run(clauses("c01"), [HAIKU], PROMPT, CLAUSES_SHA, tmp_path, FakeModels())
    with pytest.raises(StaleRecordError, match="new version"):
        records(tmp_path, HAIKU, "c01")


def test_a_record_saved_under_the_wrong_name_is_refused(tmp_path: Path) -> None:
    run_fake(tmp_path, FakeModels())
    c01 = saved(tmp_path, HAIKU.key, "c01")
    c02 = saved(tmp_path, HAIKU.key, "c02")
    c02.write_bytes(c01.read_bytes())

    with pytest.raises(StaleRecordError, match="answer to c01"):
        records(tmp_path, HAIKU, "c02")


def test_a_record_made_from_another_clauses_file_is_refused(tmp_path: Path) -> None:
    run_fake(tmp_path, FakeModels())

    with pytest.raises(StaleRecordError, match="different clauses file"):
        load_records(tmp_path, PROMPT, HAIKU, clauses("c01"), "cd" * 32)


def test_baseline_prompt_states_the_shared_conventions() -> None:
    prompt = load_prompt(ROOT / "prompts")
    conventions = (ROOT / "prompts" / "conventions-v1.txt").read_text(encoding="utf-8").strip()

    rendered = prompt.render("The Tenant shall keep no pets.")

    assert prompt.version == "baseline-v1+conventions-v1"
    assert rendered.startswith("Is this clause enforceable in a residential lease in Ontario?")
    assert conventions in rendered
    assert "Clause: The Tenant shall keep no pets." in rendered
    assert "$" not in rendered


def test_frozen_hash_matches_the_committed_set_and_its_record() -> None:
    frozen_record = (ROOT / "data" / "README.md").read_text(encoding="utf-8")

    assert file_sha256(ROOT / "data" / "clauses.jsonl") == FROZEN_CLAUSES_SHA256
    assert f"SHA-256  {FROZEN_CLAUSES_SHA256}  " in frozen_record


def test_cli_run_refuses_a_clauses_file_that_is_not_the_frozen_set(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    edited = tmp_path / "clauses.jsonl"
    edited.write_bytes((ROOT / "data" / "clauses.jsonl").read_bytes().replace(b"Dogs", b"Cats"))
    monkeypatch.setattr(cli, "live_models", lambda specs: pytest.fail("must not call a model"))

    exit_code = cli.main(["baseline", "run", "--clauses", str(edited)])

    assert exit_code == 1
    assert "not a frozen test set" in capsys.readouterr().err


def test_cli_draft_runs_and_reports_a_clauses_file_that_is_not_frozen(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    cache_dir: Path,
) -> None:
    draft = tmp_path / "stress.jsonl"
    draft.write_bytes((ROOT / "data" / "clauses.jsonl").read_bytes().replace(b'"c01"', b'"s01"'))
    models = FakeModels()
    monkeypatch.setattr(cli, "live_models", lambda specs: models)
    common = ["--models", "claude-haiku-5-5", "--clauses", str(draft), "--draft"]
    common += ["--prompts", str(ROOT / "prompts"), "--results", str(tmp_path / "results")]

    run_exit = cli.main(["baseline", "run", *common])
    report_exit = cli.main(["--cache-dir", str(cache_dir), "baseline", "report", *common])

    assert (run_exit, report_exit, len(models.calls)) == (0, 0, 38)
    header = f"draft labels (not frozen), SHA-256 {file_sha256(draft)[:12]}"
    assert header in capsys.readouterr().out
    saved = tmp_path / "results" / "baseline-v1+conventions-v1" / "claude-haiku-5-5" / "s01.json"
    assert json.loads(saved.read_text(encoding="utf-8"))["clauses_sha256"] == file_sha256(draft)


@pytest.mark.parametrize("method", ["baseline", "pipeline"])
def test_cli_draft_needs_its_own_results_directory(
    method: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    draft = tmp_path / "stress.jsonl"
    draft.write_bytes((ROOT / "data" / "clauses.jsonl").read_bytes().replace(b'"c01"', b'"s01"'))
    monkeypatch.setattr(cli, "live_models", lambda specs: pytest.fail("must not call a model"))

    exit_code = cli.main([method, "run", "--clauses", str(draft), "--draft"])

    assert exit_code == 1
    assert "--draft needs --results" in capsys.readouterr().err


def test_cli_report_finds_the_stress_set_answers_in_their_own_directory(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], cache_dir: Path
) -> None:
    monkeypatch.chdir(ROOT)

    exit_code = cli.main(
        ["--cache-dir", str(cache_dir), "baseline", "report", "--clauses", "data/stress.jsonl"]
    )

    output = capsys.readouterr().out
    assert exit_code == 0
    assert f"frozen SHA-256 {FROZEN_STRESS_SHA256[:12]}" in output
    row = next(line for line in output.splitlines() if line.startswith("claude-haiku-5-5"))
    assert "14 of 14" in row


@pytest.mark.parametrize(
    "args",
    [["--models", "gpt-x"], ["--workers", "0"]],
    ids=["unknown-model", "no-workers"],
)
def test_cli_run_rejects_bad_arguments_before_any_call(
    args: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cli, "live_models", lambda specs: pytest.fail("must not call a model"))

    with pytest.raises(SystemExit) as exit_info:
        cli.main(["baseline", "run", *args])

    assert exit_info.value.code == 2


def test_cli_run_then_report_scores_the_saved_answers(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    cache_dir: Path,
) -> None:
    clauses_path = tmp_path / "clauses.jsonl"
    shutil.copyfile(ROOT / "data" / "clauses.jsonl", clauses_path)
    models = FakeModels()
    monkeypatch.setattr(cli, "live_models", lambda specs: models)
    common = ["--models", "claude-haiku-5-5", "--clauses", str(clauses_path)]
    common += ["--prompts", str(ROOT / "prompts"), "--results", str(tmp_path / "results")]

    run_exit = cli.main(["baseline", "run", *common])
    report_exit = cli.main(
        ["--cache-dir", str(cache_dir), "baseline", "report", "--details", *common]
    )

    output = capsys.readouterr().out
    total = len(load_clauses(clauses_path))
    voids = sum(c.label == "void" for c in load_clauses(clauses_path))
    assert (run_exit, report_exit, len(models.calls)) == (0, 0, total)
    # The fake always answers "void, s. 14": right on every void clause, a false alarm on
    # every allowed one, and an exact section only on the s. 14 clauses.
    row = next(line for line in output.splitlines() if line.startswith("claude-haiku-5-5"))
    assert f"{voids} of {total}" in row
    assert f"{total - voids} of {total - voids}" in row
    assert "c03 (clear allowed, s. 14): false alarm: called a lawful clause void" in output
    assert "c01 (" not in output  # right verdict and exact section: not an error
    assert f"frozen SHA-256 {FROZEN_CLAUSES_SHA256[:12]}" in output
    assert "Not legal advice" in output
    json.loads(
        (
            tmp_path / "results" / "baseline-v1+conventions-v1" / "claude-haiku-5-5" / "c01.json"
        ).read_text()
    )
