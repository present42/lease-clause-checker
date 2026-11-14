"""Score saved answers against the frozen labels. Deterministic: no model judges another.

The baseline prompt asks the model to end with "Verdict: enforceable" or "Verdict: not
enforceable" and "Section: <number>". "Enforceable" maps to the label `allowed`, and "not
enforceable" (or "unenforceable") to `void`. Only those two lines are read; the last of each
counts. A verdict line must hold nothing but the verdict (Markdown and end punctuation aside),
so an echoed instruction such as "enforceable (or: not enforceable)" is not read as one. An
answer without a readable verdict line scores as `no_verdict`, never as a guess.

The pipeline answers in JSON instead, and may also answer `unsure`; `score_answer` scores an
`Answer` from either method.
"""

import re
from collections import Counter
from collections.abc import Collection
from dataclasses import dataclass
from typing import Literal

from lease_clause_checker.clauses import Clause

Verdict = Literal["void", "allowed"]
Outcome = Literal["correct", "false_alarm", "missed_void", "unsure", "no_verdict"]
"""`false_alarm`: called a lawful (allowed) clause void. `missed_void`: called a void one lawful.
`unsure`: declined to decide (pipeline only)."""
SectionResult = Literal["deciding", "also_accepted", "other_section", "not_in_act", "none"]
"""How the cited section compares with the label: the deciding section, a section in
`also_accept`, another section of the Act, a number that isn't an in-force section, or none."""

# Markdown such as "**" or "- " is allowed around the label, the colon and the verdict.
VERDICT_LINE = re.compile(
    r"^[\s*_#-]*verdict[\s*_]*:[\s*_]*(not\s+enforceable|unenforceable|enforceable)[\s*_.!]*$",
    re.IGNORECASE | re.MULTILINE,
)
SECTION_LINE = re.compile(r"^[\s*_#-]*sections?[\s*_]*:(.*)$", re.IGNORECASE | re.MULTILINE)
# Years ("Act, 2006") and the Act's chapter ("c. 17") are not section numbers; no RTA
# section number is anywhere near 1900.
NOT_A_SECTION = re.compile(r"\b(?:19|20)\d\d\b|\bc\.\s*17\b", re.IGNORECASE)
SECTION_NUMBER = re.compile(r"\d+(?:\.\d+)?")


@dataclass(frozen=True)
class Answer:
    verdict: Verdict | Literal["unsure"] | None
    section: str | None
    """The cited section number, without subsections; for the baseline, the first number on
    the last "Section:" line."""


@dataclass(frozen=True)
class Score:
    clause: Clause
    answer: Answer
    outcome: Outcome
    section_result: SectionResult


def parse_answer(text: str) -> Answer:
    verdicts = VERDICT_LINE.findall(text)
    verdict: Verdict | None = None
    if verdicts:
        verdict = "allowed" if verdicts[-1].lower() == "enforceable" else "void"
    section_lines = SECTION_LINE.findall(text)
    section = None
    if section_lines:
        number = SECTION_NUMBER.search(NOT_A_SECTION.sub(" ", section_lines[-1]))
        section = number.group() if number else None
    return Answer(verdict, section)


def score(clause: Clause, text: str, act_sections: Collection[str]) -> Score:
    """Score a baseline answer's text."""
    return score_answer(clause, parse_answer(text), act_sections)


def score_answer(clause: Clause, answer: Answer, act_sections: Collection[str]) -> Score:
    return Score(
        clause=clause,
        answer=answer,
        outcome=_outcome(clause.label, answer.verdict),
        section_result=_section_result(clause, answer.section, act_sections),
    )


@dataclass(frozen=True)
class Summary:
    total: int
    void_total: int
    allowed_total: int
    outcomes: Counter[Outcome]
    sections: Counter[SectionResult]

    @property
    def correct(self) -> int:
        return self.outcomes["correct"]

    @property
    def section_ok(self) -> int:
        """Lenient: the deciding section or one listed in `also_accept`."""
        return self.sections["deciding"] + self.sections["also_accepted"]


def summarize(scores: list[Score]) -> Summary:
    return Summary(
        total=len(scores),
        void_total=sum(s.clause.label == "void" for s in scores),
        allowed_total=sum(s.clause.label == "allowed" for s in scores),
        outcomes=Counter(s.outcome for s in scores),
        sections=Counter(s.section_result for s in scores),
    )


def _outcome(label: Verdict, verdict: Verdict | Literal["unsure"] | None) -> Outcome:
    if verdict is None:
        return "no_verdict"
    if verdict == "unsure":
        return "unsure"
    if verdict == label:
        return "correct"
    return "false_alarm" if label == "allowed" else "missed_void"


def _section_result(
    clause: Clause, section: str | None, act_sections: Collection[str]
) -> SectionResult:
    if section is None:
        return "none"
    if section == clause.section:
        return "deciding"
    if section in clause.also_accept:
        return "also_accepted"
    return "other_section" if section in act_sections else "not_in_act"
