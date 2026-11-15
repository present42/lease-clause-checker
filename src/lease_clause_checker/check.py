"""`lcc check`: the grounded pipeline on each clause of a lease, printed for a terminal.

A lease is plain text. A clause starts on a line that begins with its number: "1.", "1)",
"1.1" or "1.1.". The lines after it belong to it until a blank line, and so does a following
paragraph that starts with a sub-item marker such as "(a)". Any other paragraph, such as a
title or a signature block, is skipped and reported.

Every clause goes through the same pipeline as the evaluation: the rule sections of the Act in
the prompt, the quote checked in code, one retry, and `unsure` if the retry fails too. Answers
are saved like the evaluation's, in a directory named for the lease's SHA-256, so checking the
same lease again makes no calls: under `results/check/` for a lease whose answers are committed
(the sample), otherwise under the gitignored cache.
"""

import os
import re
import shutil
import textwrap
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from lease_clause_checker import elaws, pipeline, verify
from lease_clause_checker.models import ModelSpec
from lease_clause_checker.pipeline import Attempt, PipelinePrompt
from lease_clause_checker.providers import Complete
from lease_clause_checker.runs import RunSummary, Store, sha256_text
from lease_clause_checker.statute import Section

CLAUSE_START = re.compile(r"\s*(\d+(?:\.\d+)+\.?|\d+[.)])\s+(\S.*)")
SUB_ITEM = re.compile(r"\s*\(?(?:[a-z]|[ivx]+|\d+)\)\s")
SAVED_CHECKS = Path("results/check")
"""Committed answers for leases the README shows; other leases' answers go in the cache."""
WIDTH = 96
"""The widest line printed; fits a terminal at 1280 px with room to spare."""
LABEL_WIDTH = 9
COLOURS = {"void": "1;31", "allowed": "1;32", "unsure": "1;33", "failed": "1"}
DIM = "2"
WARNING = "33"

Verdict = Literal["void", "allowed", "unsure"]


class LeaseFormatError(ValueError):
    """The lease has no numbered clauses, or two with the same number."""


@dataclass(frozen=True)
class LeaseClause:
    number: str
    """As in the lease, without the trailing "." or ")" and leading zeros: "3", "4.1"."""
    text: str

    @property
    def id(self) -> str:
        return f"clause-{self.number}"


@dataclass(frozen=True)
class Lease:
    clauses: tuple[LeaseClause, ...]
    skipped: tuple[str, ...]
    """Paragraphs that are not part of any clause, such as a title."""


@dataclass(frozen=True)
class Finding:
    clause: LeaseClause
    attempts: tuple[Attempt, ...]

    @property
    def verdict(self) -> Verdict:
        final = self.attempts[-1]
        if not final.passed or final.answer is None:
            return "unsure"
        return final.answer.verdict


def split_lease(text: str) -> Lease:
    clauses: list[tuple[str, list[str]]] = []
    skipped: list[str] = []
    current: list[str] | None = None
    for paragraph in re.split(r"\n[ \t]*\n", text.replace("﻿", "").replace("\r\n", "\n")):
        lead: list[str] = []
        opening = True  # the paragraph's first line
        for line in paragraph.splitlines():
            if not line.strip():
                continue
            start = CLAUSE_START.match(line)
            if start:
                current = [start.group(2)]
                clauses.append((_number(start.group(1)), current))
            elif current is not None and not lead and (not opening or SUB_ITEM.match(line)):
                current.append(line)  # wrapped text, or a paragraph of sub-items
            else:
                lead.append(line)
            opening = False
        if lead:  # not numbered and not a sub-item of the clause before it
            skipped.append(" ".join(" ".join(lead).split()))
            current = None
    if not clauses:
        raise LeaseFormatError('no numbered clauses: start each clause\'s line with "1." etc.')
    numbers = [number for number, _ in clauses]
    if len(set(numbers)) != len(numbers):
        raise LeaseFormatError(f"clause numbers repeat: {', '.join(numbers)}")
    return Lease(
        tuple(LeaseClause(number, " ".join(" ".join(parts).split())) for number, parts in clauses),
        tuple(skipped),
    )


def _number(written: str) -> str:
    return ".".join(str(int(part)) for part in written.rstrip(".)").split("."))


def store_for(
    lease_text: str, cache_dir: Path, prompt_version: str, saved_dir: Path = SAVED_CHECKS
) -> Store:
    """Where a lease's answers are kept: one directory per lease text."""
    digest = sha256_text(lease_text)
    name = digest[:16]
    root = saved_dir if (saved_dir / name).is_dir() else cache_dir / "check"
    return Store(root / name, prompt_version, digest)


def check(
    clauses: Sequence[LeaseClause],
    spec: ModelSpec,
    prompt: PipelinePrompt,
    sections: dict[str, Section],
    store: Store,
    complete: Complete,
    *,
    workers: int = 8,
) -> tuple[list[Finding], RunSummary]:
    """Each clause's pipeline attempts; a clause whose call failed has no finding."""
    summary = pipeline.run(clauses, [spec], prompt, sections, store, complete, workers=workers)
    findings = []
    for clause in clauses:
        attempts = pipeline.attempts_for(prompt, spec, clause, sections, store.load)
        if attempts is not None:
            findings.append(Finding(clause, attempts))
    return findings, summary


def use_color(choice: str, is_tty: bool, environ: Mapping[str, str] = os.environ) -> bool:
    """`--color`: "always", "never", or "auto" (a terminal, unless NO_COLOR is set)."""
    if choice == "auto":
        return is_tty and not environ.get("NO_COLOR")
    return choice == "always"


def terminal_width() -> int:
    return min(WIDTH, shutil.get_terminal_size().columns)


def render(
    findings: Sequence[Finding],
    clauses: Sequence[LeaseClause],
    sections: dict[str, Section],
    *,
    color: bool,
    width: int = WIDTH,
) -> str:
    """One block per clause, in lease order, then a count of verdicts.

    A verdict is shown with its section, its checked quote and a link to the section. The
    model's one-sentence reason is not checked by anything, so it is shown only for `unsure`.
    """
    by_id = {finding.clause.id: finding for finding in findings}
    number_width = max(3, *(len(clause.number) for clause in clauses))
    blocks = [
        _block(clause, by_id.get(clause.id), sections, number_width, color=color, width=width)
        for clause in clauses
    ]
    counts = {v: sum(f.verdict == v for f in findings) for v in ("void", "allowed", "unsure")}
    noun = "clause" if len(clauses) == 1 else "clauses"
    tally = f"{len(clauses)} {noun}: " + ", ".join(f"{n} {v}" for v, n in counts.items())
    if len(findings) < len(clauses):
        tally += f", {len(clauses) - len(findings)} not answered"
    return "\n\n".join(blocks) + "\n\n" + tally + "."


def _block(
    clause: LeaseClause,
    finding: Finding | None,
    sections: dict[str, Section],
    number_width: int,
    *,
    color: bool,
    width: int,
) -> str:
    label = finding.verdict if finding else "failed"
    gutter = number_width + 2 + LABEL_WIDTH
    indent = " " * gutter
    head = f"{clause.number:>{number_width}}  " + _paint(
        f"{label.upper():<{LABEL_WIDTH}}", COLOURS[label], color
    )
    text = _wrap(clause.text, indent, width)
    lines = [head + text[gutter:]]  # the head takes exactly `gutter` columns
    if finding is None:
        lines.append(_wrap("The call failed; run the check again.", indent, width))
        return "\n".join(lines)
    final = finding.attempts[-1]
    answer = final.answer
    if not final.passed:
        lines.append(_wrap(f"No verdict: both answers failed. {final.problem()}", indent, width))
    elif answer is not None and answer.verdict != "unsure":
        section = sections[answer.cited_section]
        lines.append(_wrap(f"s. {section.number}, {section.heading}", indent, width))
        lines.append(_paint(_wrap(f"“{answer.quote}”", indent, width), DIM, color))
        ending = verify.ending(answer.quote, section.number, sections)
        if ending is not None and ending.warning is not None:
            lines.append(_paint(_wrap(ending.warning, indent, width), WARNING, color))
        lines.append(indent + elaws.section_url(section.anchor))
    elif answer is not None:  # unsure: its reason is the only thing to show
        lines.append(_wrap(f"Unsure because: {answer.reason}", indent, width))
    if len(finding.attempts) > 1 and final.passed:
        first = finding.attempts[0].problem()
        lines.append(_wrap(f"(Asked again; the first answer failed: {first})", indent, width))
    return "\n".join(lines)


def _wrap(text: str, indent: str, width: int) -> str:
    return textwrap.fill(text, width, initial_indent=indent, subsequent_indent=indent)


def _paint(text: str, code: str, color: bool) -> str:
    return f"\033[{code}m{text}\033[0m" if color else text
