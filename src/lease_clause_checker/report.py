"""Plain-text tables of scored runs, for the terminal and for reports."""

from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field

from lease_clause_checker.closedbook import ClosedBookResult
from lease_clause_checker.models import ModelSpec
from lease_clause_checker.pipeline import Attempt, Result
from lease_clause_checker.runs import RunRecord
from lease_clause_checker.scoring import Score, Summary, summarize
from lease_clause_checker.verify import QuoteCheck

# Finish reasons of an answer that ended normally; anything else (hitting the token limit,
# a refusal, a safety stop) is counted and listed, since its verdict may be missing.
FINISHED = frozenset({"end_turn", "STOP"})
KIND_NAMES = {"clear": "clear", "near_miss": "near-miss"}
OUTCOME_NAMES = {
    "false_alarm": "false alarm: called a lawful clause void",
    "missed_void": "missed: called a void clause lawful",
    "unsure": "unsure",
    "no_verdict": "no readable verdict",
}
SECTION_NAMES = {
    "deciding": "the deciding section",
    "also_accepted": "an accepted alternative",
    "other_section": "a wrong section",
    "not_in_act": "not an in-force section of the Act",
    "none": "no section",
}


@dataclass(frozen=True)
class ModelRun:
    spec: ModelSpec
    records: list[RunRecord]
    """Every saved call, retries included."""
    scores: list[Score]
    """One score per clause answered."""
    results: list[Result] = field(default_factory=list)
    """The pipeline's result per clause answered, in the order of `scores`; empty for the
    baseline."""
    closed_book: list[ClosedBookResult] = field(default_factory=list)
    """The closed-book result per clause answered, in the order of `scores`; empty otherwise."""

    @property
    def cost_usd(self) -> float:
        """Each call at the price in force on the (UTC) day it was made."""
        return sum(
            self.spec.cost_usd(r.input_tokens, r.output_tokens, r.requested_at.date())
            for r in self.records
        )

    @property
    def unfinished(self) -> list[RunRecord]:
        return [r for r in self.records if r.finish_reason not in FINISHED]


def summary_table(runs: list[ModelRun], clause_count: int) -> str:
    """One row per model: the baseline's scores."""
    header = (*_HEADER_START, "no verdict", "section ok", "section exact", "not in Act")
    rows: list[tuple[str, ...]] = [(*header, *_HEADER_END)]
    for run in runs:
        summary = summarize(run.scores)
        rows.append(
            (
                *_row_start(run, summary, clause_count),
                str(summary.outcomes["no_verdict"]),
                *_section_cells(summary),
                str(summary.sections["not_in_act"]),
                *_row_end(run),
            )
        )
    return _table(rows)


def pipeline_table(runs: list[ModelRun], clause_count: int) -> str:
    """One row per model: the pipeline's scores, with how often it was unsure or retried.

    No "no verdict" or "not in Act" columns: every result is void, allowed or unsure, and a
    verified quote's section is in the Act.
    """
    header = (*_HEADER_START, "unsure", "gave up", "retried", "section ok", "section exact")
    rows: list[tuple[str, ...]] = [(*header, *_HEADER_END)]
    for run in runs:
        summary = summarize(run.scores)
        rows.append(
            (
                *_row_start(run, summary, clause_count),
                str(summary.outcomes["unsure"]),
                str(sum(result.gave_up for result in run.results)),
                str(sum(len(result.attempts) > 1 for result in run.results)),
                *_section_cells(summary),
                *_row_end(run),
            )
        )
    return _table(rows)


def closedbook_table(runs: list[ModelRun], clause_count: int) -> str:
    """One row per model: the closed-book scores, and what the check found in each quote.

    "quote verified" counts the answers with a verdict whose quote passed; the five columns
    after it say why the others failed.
    """
    header = (
        *_HEADER_START,
        "unsure",
        "no verdict",
        "quote verified",
        "not found",
        "heading",
        "too short",
        "no such section",
        "no quote",
        "section ok",
        "section exact",
    )
    rows: list[tuple[str, ...]] = [(*header, *_HEADER_END)]
    for run in runs:
        summary = summarize(run.scores)
        checks = Counter(result.attempt.check for result in run.closed_book)
        quoted = sum(checks[name] for name in QUOTE_CHECKS)
        rows.append(
            (
                *_row_start(run, summary, clause_count),
                str(summary.outcomes["unsure"]),
                str(summary.outcomes["no_verdict"]),
                f"{checks['verified']} of {quoted}",
                *(str(checks[name]) for name in QUOTE_CHECKS[1:]),
                *_section_cells(summary),
                *_row_end(run),
            )
        )
    return _table(rows)


QUOTE_CHECKS: tuple[QuoteCheck, ...] = (
    "verified",
    "not_found",
    "heading",
    "too_short",
    "no_such_section",
    "no_quote",
)
"""The checks an answer with a verdict can get, as the closed-book table's columns."""


_HEADER_START = ("model", "weight", "answered", "verdict right", "false alarms", "missed voids")
_HEADER_END = ("unfinished", "cost USD")


def _row_start(run: ModelRun, summary: Summary, clause_count: int) -> tuple[str, ...]:
    return (
        run.spec.key,
        run.spec.weight,
        f"{len(run.scores)} of {clause_count}",
        f"{summary.correct} of {summary.total}",
        f"{summary.outcomes['false_alarm']} of {summary.allowed_total}",
        f"{summary.outcomes['missed_void']} of {summary.void_total}",
    )


def _section_cells(summary: Summary) -> tuple[str, ...]:
    return (
        f"{summary.section_ok} of {summary.total}",
        f"{summary.sections['deciding']} of {summary.total}",
    )


def _row_end(run: ModelRun) -> tuple[str, ...]:
    return (str(len(run.unfinished)), f"{run.cost_usd:.4f}")


def _table(rows: Sequence[Sequence[str]]) -> str:
    widths = [max(len(row[i]) for row in rows) for i in range(len(rows[0]))]
    return "\n".join(
        "  ".join(cell.ljust(w) for cell, w in zip(row, widths, strict=True)).rstrip()
        for row in rows
    )


def error_lines(run: ModelRun) -> list[str]:
    """One line per clause the model got wrong in verdict or section, that the pipeline had to
    retry or whose closed-book quote failed, for gap analysis; then one per answer that did not
    finish normally."""
    if not run.records:
        return ["no saved answers"]
    attempts = {result.clause.id: result.attempts for result in run.results}
    attempts |= {result.clause.id: (result.attempt,) for result in run.closed_book}
    lines = []
    for item in run.scores:
        clause, answer = item.clause, item.answer
        checked = attempts.get(clause.id, ())
        verdict_wrong = item.outcome != "correct"
        section_wrong = item.section_result not in ("deciding", "also_accepted")
        retried = len(checked) > 1
        quote_failed = bool(run.closed_book) and not checked[0].passed
        if not (verdict_wrong or section_wrong or retried or quote_failed):
            continue
        cited = f"s. {answer.section}" if answer.section else "none"
        verdict = OUTCOME_NAMES[item.outcome] if verdict_wrong else "verdict right"
        line = (
            f"{clause.id} ({KIND_NAMES[clause.kind]} {clause.label}, s. {clause.section}): "
            f"{verdict}; cited {cited}, {SECTION_NAMES[item.section_result]}"
        )
        if checked:
            line += "; checks: " + ", then ".join(_check_name(a) for a in checked)
        lines.append(line)
    lines += [f"{r.clause_id}: answer ended with {r.finish_reason}" for r in run.unfinished]
    return lines or ["no wrong verdicts or sections"]


def _check_name(attempt: Attempt) -> str:
    if attempt.answer is None:
        return "unreadable"
    if attempt.check is None:
        return "unsure"
    return attempt.check.replace("_", " ")
