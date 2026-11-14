"""The grounded pipeline: the Act's text in the prompt, a structured answer, a checked quote.

For each model and clause:
1. Ask for a JSON answer (verdict `void`, `allowed` or `unsure`; section; quote; reason), with
   the text of every rule section in the prompt. Every clause gets the same sections, so the
   prompt never hints at the answer.
2. Check in code that the quote appears in the cited section (`verify.check_quote`).
3. If the answer can't be read or its quote fails the check, ask once more, saying what failed.
4. If the second answer fails too, the result is `unsure`.

An `unsure` answer is neither checked nor retried. Each call is saved as a record (`<clause
id>.json`, then `<clause id>.retry.json`), so results are re-scored without calling any API.

Which records exist depends on the quote check and on the retry note it produces, so a change
to `verify.py`, `PROBLEMS` or `ANSWER_SCHEMA` that alters the check or the retry note of any
saved record needs a new `PIPELINE_PROMPT` version, as a change to the prompt files does.
Re-check every saved answer after such a change. What shows if one is missed: a changed retry
note makes saved retry records stale, which is caught; a stricter check that fails a saved
first answer leaves its clause without a retry record, so `load_results` drops the clause and
the README's tables show the run as partial; a looser check that passes a saved first answer
makes its saved retry unnecessary, which nothing catches.
"""

import re
import string
from collections.abc import Callable, Collection, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, ValidationError

from lease_clause_checker.clauses import Clause
from lease_clause_checker.conventions import CONVENTIONS, load_conventions
from lease_clause_checker.models import ModelSpec
from lease_clause_checker.providers import Complete
from lease_clause_checker.runs import (
    ClauseText,
    Question,
    RunRecord,
    RunSummary,
    Store,
    run_jobs,
    sha256_text,
)
from lease_clause_checker.scoring import Answer, Score, score_answer
from lease_clause_checker.statute import Section
from lease_clause_checker.verify import MIN_QUOTE_WORDS, QuoteCheck, check_quote

PIPELINE_PROMPT = "pipeline-v1"
"""Names `prompts/pipeline-v1.txt` and its retry note `prompts/pipeline-v1-retry.txt`."""
RETRY_SUFFIX = ".retry"

# The section field should be a bare number, but "s. 14", "ss 14", "Section 14", "RTA s. 14",
# "14(1)(a)", "14 (1)" and "14." also read as 14.
CITED_SECTION = re.compile(
    r"(?:(?:RTA|the Act),?\s*)?(?:ss?\.?|sections?)?\s*(\d+(?:\.\d+)?)(?:\s*\(\w+(?:\.\w+)?\))*\.?",
    re.IGNORECASE,
)
PROBLEMS = {
    "no_quote": "It gave no quote.",
    "no_such_section": "Section {section} is not a section of the Act.",
    "too_short": f"The quote has fewer than {MIN_QUOTE_WORDS} words.",
    "heading": "The quote is from a heading, not the text of section {section}.",
    "not_found": "The quote does not appear word for word in section {section}.",
    "drops_negation": (
        "The quote leaves out the negating word just before it in section {section} "
        '("no", "not", "neither" or "nor").'
    ),
}

PipelineVerdict = Literal["void", "allowed", "unsure"]
ANSWER_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "verdict": {"type": "string", "enum": ["void", "allowed", "unsure"]},
        "section": {"type": "string"},
        "quote": {"type": "string"},
        "reason": {"type": "string"},
    },
    "required": ["verdict", "section", "quote", "reason"],
    "additionalProperties": False,
}


class PipelineAnswer(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    verdict: PipelineVerdict
    section: str
    quote: str
    reason: str

    @property
    def cited_section(self) -> str:
        return cited_section(self.section)


def cited_section(text: str) -> str:
    """The section number in a citation such as "s. 14" or "27(2)"; else the text, stripped."""
    match = CITED_SECTION.fullmatch(text.strip())
    return match.group(1) if match else text.strip()


@dataclass(frozen=True)
class Attempt:
    record: RunRecord
    answer: PipelineAnswer | None
    """None if the text was not JSON in `ANSWER_SCHEMA`'s shape."""
    check: QuoteCheck | None
    """None if the answer is missing or `unsure`; those are not checked."""

    @property
    def passed(self) -> bool:
        if self.answer is None:
            return False
        return self.answer.verdict == "unsure" or self.check == "verified"

    def problem(self) -> str:
        """What failed, as told to the model when asking again."""
        if self.passed:
            raise ValueError("this attempt passed its check")
        if self.answer is None or self.check is None:
            return "It was not a JSON object in the required format."
        return PROBLEMS[self.check].format(section=self.answer.cited_section)


def assess(record: RunRecord, sections: dict[str, Section]) -> Attempt:
    try:
        answer = PipelineAnswer.model_validate_json(record.text)
    except ValidationError:
        return Attempt(record, None, None)
    if answer.verdict == "unsure":
        return Attempt(record, answer, None)
    return Attempt(record, answer, check_quote(answer.quote, answer.cited_section, sections))


@dataclass(frozen=True)
class Result:
    clause: Clause
    attempts: tuple[Attempt, ...]
    """One, or two if the first failed its check."""

    @property
    def gave_up(self) -> bool:
        """Every attempt failed its check, so the pipeline answers `unsure`."""
        return not self.attempts[-1].passed

    @property
    def answer(self) -> Answer:
        final = self.attempts[-1].answer
        if self.gave_up or final is None or final.verdict == "unsure":
            return Answer("unsure", None)
        return Answer(final.verdict, final.cited_section)

    def score(self, act_sections: Collection[str]) -> Score:
        return score_answer(self.clause, self.answer, act_sections)


@dataclass(frozen=True)
class PipelinePrompt:
    version: str
    template: str
    """With the conventions filled in; `$sections` and `$clause` still to fill."""
    retry_template: str
    sections_text: str
    sections_reference: str
    """Stands in for `sections_text` in saved records."""

    def first(self, spec: ModelSpec, clause: ClauseText) -> Question:
        def render(sections: str) -> str:
            return string.Template(self.template).substitute(sections=sections, clause=clause.text)

        return Question(
            spec,
            clause,
            prompt=render(self.sections_text),
            saved_prompt=render(self.sections_reference),
            schema=ANSWER_SCHEMA,
        )

    def retry(self, first: Question, failed: Attempt) -> Question:
        def render(prompt: str) -> str:
            return string.Template(self.retry_template).substitute(
                prompt=prompt, previous=failed.record.text, problem=failed.problem()
            )

        return Question(
            first.spec,
            first.clause,
            prompt=render(first.prompt),
            saved_prompt=render(first.saved_prompt),
            schema=ANSWER_SCHEMA,
        )


def load_pipeline_prompt(prompts_dir: Path, sections: list[Section]) -> PipelinePrompt:
    """The pipeline prompt with the conventions and the text of `sections` from the cached Act.

    Saved records name the sections and the SHA-256 of their text, not of the whole Act, so
    they stay valid when e-Laws changes other parts of the Act.
    """
    template = (prompts_dir / f"{PIPELINE_PROMPT}.txt").read_text(encoding="utf-8")
    retry = (prompts_dir / f"{PIPELINE_PROMPT}-retry.txt").read_text(encoding="utf-8")
    numbers = ", ".join(section.number for section in sections)
    sections_text = "\n\n".join(
        f"Section {section.number}: {section.heading}\n{section.text}" for section in sections
    )
    return PipelinePrompt(
        version=f"{PIPELINE_PROMPT}+{CONVENTIONS}",
        template=string.Template(template).safe_substitute(
            conventions=load_conventions(prompts_dir)
        ),
        retry_template=retry,
        sections_text=sections_text,
        sections_reference=(
            f"[The headings and text of sections {numbers} of the Act, from e-Laws; SHA-256 "
            f"{sha256_text(sections_text)}. Left out of saved records.]"
        ),
    )


GetRecord = Callable[[Question, str], RunRecord | None]
"""Returns the record for a question saved under a name, by calling a model or by loading it."""


def attempts_for(
    prompt: PipelinePrompt,
    spec: ModelSpec,
    clause: ClauseText,
    sections: dict[str, Section],
    get: GetRecord,
) -> tuple[Attempt, ...] | None:
    """The pipeline's attempts on one clause; None if a record it needs is missing."""
    first_question = prompt.first(spec, clause)
    first_record = get(first_question, clause.id)
    if first_record is None:
        return None
    first = assess(first_record, sections)
    if first.passed:
        return (first,)
    retry_record = get(prompt.retry(first_question, first), clause.id + RETRY_SUFFIX)
    if retry_record is None:
        return None
    return first, assess(retry_record, sections)


def run(
    clauses: Sequence[ClauseText],
    specs: list[ModelSpec],
    prompt: PipelinePrompt,
    sections: dict[str, Section],
    store: Store,
    complete: Complete,
    *,
    workers: int = 8,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> RunSummary:
    """Run the pipeline for every model on every clause, reusing saved answers."""
    for spec in specs:  # every saved record, retries included, must be current before any call
        for clause in clauses:
            attempts_for(prompt, spec, clause, sections, store.load)

    def job(spec: ModelSpec, clause: ClauseText) -> Callable[[RunSummary], None]:
        def go(summary: RunSummary) -> None:
            def get(question: Question, name: str) -> RunRecord:
                record, called = store.answer(question, name, complete, now)
                summary.count(called=called)
                return record

            attempts_for(prompt, spec, clause, sections, get)

        return go

    jobs = [(f"{spec.key} {clause.id}", job(spec, clause)) for spec in specs for clause in clauses]
    return run_jobs(jobs, workers)


def load_results(
    store: Store,
    prompt: PipelinePrompt,
    spec: ModelSpec,
    clauses: list[Clause],
    sections: dict[str, Section],
) -> list[Result]:
    """The saved results for `spec`; a clause whose records are incomplete is left out."""
    results = []
    for clause in clauses:
        attempts = attempts_for(prompt, spec, clause, sections, store.load)
        if attempts is not None:
            results.append(Result(clause, attempts))
    return results
