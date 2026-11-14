"""Score the saved answers of one model, method and clause set; no API is called."""

from collections.abc import Collection
from pathlib import Path

from lease_clause_checker import baseline, closedbook, pipeline
from lease_clause_checker.clauses import Clause
from lease_clause_checker.models import ModelSpec
from lease_clause_checker.report import ModelRun
from lease_clause_checker.runs import Store
from lease_clause_checker.scoring import score
from lease_clause_checker.statute import Section


def baseline_run(
    results_dir: Path,
    prompt: baseline.Prompt,
    spec: ModelSpec,
    clauses: list[Clause],
    clauses_sha256: str,
    act_sections: Collection[str],
) -> ModelRun:
    records = baseline.load_records(results_dir, prompt, spec, clauses, clauses_sha256)
    scores = [
        score(clause, records[clause.id].text, act_sections)
        for clause in clauses
        if clause.id in records
    ]
    return ModelRun(spec, list(records.values()), scores)


def pipeline_run(
    store: Store,
    prompt: pipeline.PipelinePrompt,
    spec: ModelSpec,
    clauses: list[Clause],
    sections: dict[str, Section],
) -> ModelRun:
    results = pipeline.load_results(store, prompt, spec, clauses, sections)
    records = [attempt.record for result in results for attempt in result.attempts]
    return ModelRun(spec, records, [result.score(sections) for result in results], results)


def closedbook_run(
    results_dir: Path,
    prompt: baseline.Prompt,
    spec: ModelSpec,
    clauses: list[Clause],
    clauses_sha256: str,
    sections: dict[str, Section],
) -> ModelRun:
    results = closedbook.load_results(results_dir, prompt, spec, clauses, clauses_sha256, sections)
    return ModelRun(
        spec,
        [result.attempt.record for result in results],
        [result.score(sections) for result in results],
        closed_book=results,
    )
