"""The baseline: the plain prompt on every clause and model, one saved answer per pair.

Records are saved by `runs.Store` under `results/<prompt version>/<model>/<clause id>.json`.
"""

import string
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from lease_clause_checker.clauses import Clause
from lease_clause_checker.conventions import CONVENTIONS, load_conventions
from lease_clause_checker.models import ModelSpec
from lease_clause_checker.providers import Complete
from lease_clause_checker.runs import Question, RunRecord, RunSummary, Store, run_jobs

BASELINE_PROMPT = "baseline-v1"


@dataclass(frozen=True)
class Prompt:
    version: str
    """Names the prompt file and the conventions file, e.g. "baseline-v1+conventions-v1"."""
    template: str

    def render(self, clause_text: str) -> str:
        return string.Template(self.template).substitute(clause=clause_text)

    def question(self, spec: ModelSpec, clause: Clause) -> Question:
        text = self.render(clause.text)
        return Question(spec, clause, prompt=text, saved_prompt=text)


def load_prompt(prompts_dir: Path, name: str = BASELINE_PROMPT) -> Prompt:
    """The prompt `name`, with the shared reading conventions filled in."""
    template = (prompts_dir / f"{name}.txt").read_text(encoding="utf-8")
    filled = string.Template(template).safe_substitute(conventions=load_conventions(prompts_dir))
    return Prompt(version=f"{name}+{CONVENTIONS}", template=filled)


def load_records(
    results_dir: Path,
    prompt: Prompt,
    spec: ModelSpec,
    clauses: list[Clause],
    clauses_sha256: str,
) -> dict[str, RunRecord]:
    """The saved records for `spec`, by clause id; clauses with no record are left out.

    Every record must match the current prompt, request and clauses file, so a report never
    scores answers to a question that is no longer the one asked.
    """
    store = Store(results_dir, prompt.version, clauses_sha256)
    records = {}
    for clause in clauses:
        record = store.load(prompt.question(spec, clause), clause.id)
        if record is not None:
            records[clause.id] = record
    return records


def run(
    clauses: list[Clause],
    specs: list[ModelSpec],
    prompt: Prompt,
    clauses_sha256: str,
    results_dir: Path,
    complete: Complete,
    *,
    workers: int = 8,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> RunSummary:
    """Ask every model about every clause, skipping answers already saved."""
    store = Store(results_dir, prompt.version, clauses_sha256)
    questions = [prompt.question(spec, clause) for spec in specs for clause in clauses]
    for question in questions:  # every saved answer must be current before any call is paid for
        store.load(question, question.clause.id)

    def job(question: Question) -> Callable[[RunSummary], None]:
        def answer(summary: RunSummary) -> None:
            _, called = store.answer(question, question.clause.id, complete, now)
            summary.count(called=called)

        return answer

    return run_jobs([(f"{q.spec.key} {q.clause.id}", job(q)) for q in questions], workers)
