"""The closed-book run: the pipeline's question and answer format, with no statute text.

Each model answers each clause once, from what it knows of the Act, as a JSON answer in the
pipeline's shape (verdict, section, quote, reason). There is no retry: without the text, a
model told that its quote failed has nothing to check it against. The quote is checked by the
pipeline's code (`pipeline.assess`) against the cached Act, and the verdict is scored as given,
whether or not the quote passes, so the run shows how often a model recalls the Act's words
correctly.

Records are saved by `runs.Store` under `results/<prompt version>/<model>/<clause id>.json`,
asked and loaded as the baseline's are (`baseline.run`, `baseline.load_records`). The run uses
the pipeline's `ANSWER_SCHEMA` and quote check (`verify.py`), so a change to either needs a new
`CLOSED_BOOK_PROMPT` version, as it does for the pipeline's. Each quote is also sorted by how it
differs from the Act (`quotediff.py`); that sorting is reporting only and can change freely.
"""

from collections.abc import Collection
from dataclasses import dataclass
from pathlib import Path

from lease_clause_checker import baseline, pipeline
from lease_clause_checker.clauses import Clause
from lease_clause_checker.models import ModelSpec
from lease_clause_checker.quotediff import Classified, classify
from lease_clause_checker.scoring import Answer, Score, score_answer
from lease_clause_checker.statute import Section

CLOSED_BOOK_PROMPT = "closedbook-v1"


def load_prompt(prompts_dir: Path) -> baseline.Prompt:
    return baseline.load_prompt(prompts_dir, CLOSED_BOOK_PROMPT, pipeline.ANSWER_SCHEMA)


@dataclass(frozen=True)
class ClosedBookResult:
    clause: Clause
    attempt: pipeline.Attempt
    quote: Classified | None
    """How the quote differs from the Act (`quotediff.classify`); None if the answer is
    unreadable or unsure, and so has no quote to sort."""

    @property
    def answer(self) -> Answer:
        """The verdict and section as given; an unreadable answer has neither, and an empty
        section is no section."""
        given = self.attempt.answer
        if given is None:
            return Answer(None, None)
        if given.verdict == "unsure":
            return Answer("unsure", None)
        return Answer(given.verdict, given.cited_section or None)

    def score(self, act_sections: Collection[str]) -> Score:
        return score_answer(self.clause, self.answer, act_sections)


def load_results(
    results_dir: Path,
    prompt: baseline.Prompt,
    spec: ModelSpec,
    clauses: list[Clause],
    clauses_sha256: str,
    sections: dict[str, Section],
) -> list[ClosedBookResult]:
    """The saved results for `spec`, each quote checked; clauses with no answer are left out."""
    records = baseline.load_records(results_dir, prompt, spec, clauses, clauses_sha256)
    results = []
    for clause in clauses:
        if clause.id not in records:
            continue
        attempt = pipeline.assess(records[clause.id], sections)
        given = attempt.answer
        quote = None
        if given is not None and given.verdict != "unsure":
            quote = classify(given.quote, given.cited_section, sections)
        results.append(ClosedBookResult(clause, attempt, quote))
    return results
