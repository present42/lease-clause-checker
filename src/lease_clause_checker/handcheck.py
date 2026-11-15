"""The record of how the closed-book quotes that failed the check were judged by hand
(`RECORD_DIR`, described in its MANIFEST.md), and the agreement figures computed from it.

Claude suggested an answer for every quote (`claude_suggestions_<stage>.json`); in each round, fresh
reviewers judged the quotes blind, each quote by one reviewer (`round<n>_reviewer-*.json`); the
final suggestions are what `quotereview.CHECKS_FILE` holds. A reviewer's answer agrees with a
suggestion under `agrees`, the rule in MANIFEST.md.
"""

import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from lease_clause_checker.quotediff import Category
from lease_clause_checker.quotereview import NOT_A_RULE, HandCheck, HandChecks

RECORD_DIR = "hand-check"
"""Beside `quotereview.CHECKS_FILE`."""
MANIFEST = "MANIFEST.md"
RULES = "rules.md"
STAGES = ("round1", "round2", "final")
"""Claude's suggestions: before round 1, before round 2 (under the rules), and final."""
MEANING = "q1_says_something_the_act_doesnt"
MISLEADS = "q2_misleads_on_this_clause"


class RecordError(ValueError):
    """The record is incomplete or inconsistent with itself or with the hand checks."""


@dataclass(frozen=True)
class Judgement:
    category: str
    meaning_changed: str
    """Blank for a category that states no rule (`NOT_A_RULE`), whatever was written."""
    misleads_here: str


def agrees(first: Judgement, second: Judgement) -> bool:
    """The category matches, whether the quote changes what the Act says matches, and, where
    both say it does, whether it misleads on the clause matches."""
    if (first.category, first.meaning_changed) != (second.category, second.meaning_changed):
        return False
    return first.meaning_changed != "yes" or first.misleads_here == second.misleads_here


@dataclass(frozen=True)
class Record:
    suggestions: Mapping[str, Mapping[str, Judgement]]
    """By stage (`STAGES`), then by row id ("<model key>__<clause id>")."""
    rounds: Mapping[int, Mapping[str, Judgement]]
    """Every reviewer's answers in each round, by row id."""

    @property
    def rows(self) -> int:
        return len(self.suggestions["final"])

    def agreed(self, round_number: int) -> int:
        """How many of the round's answers agree with the suggestions it was compared with."""
        suggested = self.suggestions[f"round{round_number}"]
        answers = self.rounds[round_number]
        return sum(agrees(suggested[row], answers[row]) for row in answers)

    def adopted(self, round_number: int) -> int:
        """Of the rows where the round's answer differs from the suggestion, how many the final
        answer takes from the reviewer."""
        suggested = self.suggestions[f"round{round_number}"]
        answers, final = self.rounds[round_number], self.suggestions["final"]
        return sum(
            not agrees(suggested[row], answer) and agrees(final[row], answer)
            for row, answer in answers.items()
        )

    def check_against(
        self, checks: HandChecks, categories: Mapping[tuple[str, str], Category]
    ) -> None:
        """Raise `RecordError` unless the final suggestions are the hand checks, row for row.
        `categories` gives the code's category of each row, which "agree" stands for."""
        final = self.suggestions["final"]
        if set(final) != {_row_id(key) for key in categories}:
            raise RecordError("the final suggestions are not the quotes that failed the check")
        for key, category in categories.items():
            check = checks.get(key)
            if check is None or _from_check(check, category) != final[_row_id(key)]:
                raise RecordError(f"{key[0]} {key[1]}: the hand check is not the final suggestion")


def method_sentence(record: Record) -> str:
    """How the hand check was made, in the owner's words, with the agreement figures from the
    record. `RecordError` unless the record has rounds 1 and 2, which the words describe."""
    if sorted(record.rounds) != [1, 2]:
        raise RecordError(f"the record has rounds {sorted(record.rounds)}, not 1 and 2")
    rows, agreed = record.rows, record.agreed(2)
    return (
        "I worked four rows by hand with Claude to set the approach. Claude then suggested an "
        "answer for every row. A first round of blind Claude Opus review, each row judged by one "
        "reviewer, surfaced three unclear points, which I settled as written rules; a fourth rule "
        "came from rows where the reviewers caught dropped sentence endings. Under the four "
        f"rules, a second blind round agreed with the suggestions on {agreed} of {rows} rows; on "
        f"{record.adopted(2)} of the other {rows - agreed} the reviewer's answer was adopted. "
        "I approved the result."
    )


def label_note(record: Record) -> str:
    """Who made the labels (category, meaning changed, misleads), in the owner's words, with
    round 2's agreement from the record."""
    return (
        "Claude drafted the hand-check labels, Claude Opus reviewed them blind in two rounds "
        f"({record.agreed(2)} of {record.rows} agreed independently), and the author approved "
        "them; not reviewed by a lawyer."
    )


RECHECK_NOTE = (
    "Still to come, by Wednesday 18 November 2026: the author's own re-check of 20 rows. It is "
    "not fully blind, as the author had seen some of Claude's suggestions."
)
"""The slot for the author's re-check, until its results are in the record."""


def load_record(folder: Path) -> Record | None:
    """The record in `folder`, or None if there is none; `RecordError` if it is incomplete or
    inconsistent."""
    if not (folder / MANIFEST).exists():
        return None
    try:
        return _load(folder)
    except (OSError, KeyError, TypeError, ValueError) as error:
        if isinstance(error, RecordError):
            raise
        raise RecordError(f"{folder}: {type(error).__name__}: {error}") from error


def _load(folder: Path) -> Record:
    suggestions = {
        stage: _by_row(_read(folder / f"claude_suggestions_{stage}.json")["suggestions"], stage)
        for stage in STAGES
    }
    rounds: dict[int, dict[str, Judgement]] = {}
    for path in sorted(folder.glob("round*_reviewer-*.json")):
        data = _read(path)
        number = int(data["round"].removeprefix("round"))
        if not path.name.startswith(f"{data['round']}_"):
            raise RecordError(f"{path.name}: its round is {data['round']!r}")
        answers = _by_row(data["answers"], path.name)
        given = [row["id"] for row in _read(folder / data["input_file"])]
        if list(answers) != given:
            raise RecordError(f"{path.name}: its answers are not the rows in {data['input_file']}")
        if len(answers) != data["rows"]:
            raise RecordError(f"{path.name}: {len(answers)} answers, but rows says {data['rows']}")
        shared = rounds.setdefault(number, {}).keys() & answers.keys()
        if shared:
            raise RecordError(f"{path.name}: rows judged twice in round {number}: {sorted(shared)}")
        rounds[number].update(answers)
    rows = set(suggestions["final"])
    for name, judged in [*suggestions.items(), *((f"round {n}", r) for n, r in rounds.items())]:
        if set(judged) != rows:
            raise RecordError(f"{name} does not cover the same rows as the final suggestions")
    return Record(suggestions, rounds)


def _read(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _by_row(entries: list[dict[str, str]], where: str) -> dict[str, Judgement]:
    judged = {entry["id"]: _judgement(entry) for entry in entries}
    if len(judged) != len(entries):
        raise RecordError(f"{where}: a row appears twice")
    return judged


def _judgement(entry: Mapping[str, str]) -> Judgement:
    category = entry["category"]
    meaning = "" if category in NOT_A_RULE else entry[MEANING].strip().lower()
    return Judgement(category, meaning, entry[MISLEADS].strip().lower())


def _from_check(check: HandCheck, category: Category) -> Judgement:
    resolved = check.category(category)
    return Judgement(
        resolved, "" if resolved in NOT_A_RULE else check.meaning_changed, check.misleads_here
    )


def _row_id(key: tuple[str, str]) -> str:
    return f"{key[0]}__{key[1]}"
