"""The test set: synthetic lease clauses, each labelled with the RTA section that decides it.

`data/clauses.jsonl` is hand-written, one JSON object per line. The loader accepts exactly one
byte form of the file (UTF-8, no byte-order mark, `\\n` line endings, final newline), so its
SHA-256 identifies its content.

`check_clauses` ties every clause to rules.yaml and to the cached Act, so a label's stated
evidence is checked in code: the deciding section must be one of the rules, the link must be
that rule's link, the quoted decisive words must appear exactly (quotation marks included,
since they reproduce the Act), as whole words, within one subsection of the section's
operative text, and every other accepted section must exist.
"""

import hashlib
import json
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, StringConstraints

from lease_clause_checker.fields import OneLine, SectionNumber
from lease_clause_checker.rules import Rule
from lease_clause_checker.statute import Section
from lease_clause_checker.verify import quote_in_text

# The reviewed test set, frozen on 11 Nov 2026 before any model saw it (see data/README.md).
# The main scoreboard only uses clauses.jsonl when its SHA-256 is this.
FROZEN_CLAUSES_SHA256 = "4570d03b345c4232c2514d9f1d98ce03a0f98f6ee9f9fa54baddfe53ce8bf917"
# The stress set, frozen on 13 Nov 2026: clauses chosen because Claude Haiku 5.5 failed them.
FROZEN_STRESS_SHA256 = "0519e3fbb53fb3c0e871f7f50a9bd48f8bca8aadb8e78d8ac43045341265dee5"
FROZEN_SETS = frozenset({FROZEN_CLAUSES_SHA256, FROZEN_STRESS_SHA256})
"""The SHA-256 of every reviewed, frozen clauses file; only these are scored."""


class Clause(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    id: Annotated[str, StringConstraints(pattern=r"^[cs]\d{2}$")]
    """`c01`… in the main set, `s01`… in the stress set."""
    text: OneLine
    """Invented lease wording. The only field a model under test is shown."""
    label: Literal["void", "allowed"]
    kind: Literal["clear", "near_miss"]
    """`near_miss`: a lawful clause that sounds restrictive, or a void clause phrased softly."""
    section: SectionNumber
    also_accept: list[SectionNumber]
    """Other sections that decide the label just as well, so citing them isn't an error."""
    rationale: OneLine
    quote: OneLine
    """The decisive words of `section`, verbatim from one subsection of its operative text."""
    source: OneLine
    reviewed: bool


class ClausesFileError(ValueError):
    """clauses.jsonl is malformed."""


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_clauses(path: Path) -> list[Clause]:
    data = path.read_bytes()
    if data.startswith(b"\xef\xbb\xbf"):
        raise ClausesFileError(f"{path}: starts with a byte-order mark; save as UTF-8 without one")
    if b"\r" in data:
        raise ClausesFileError(f"{path}: contains a carriage return; use \\n line endings")
    if not data:
        raise ClausesFileError(f"{path}: no clauses")
    if not data.endswith(b"\n"):
        raise ClausesFileError(f"{path}: must end with a newline")
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as error:
        raise ClausesFileError(f"{path}: not UTF-8: {error}") from error
    # Split on "\n" only: str.splitlines() would also split on U+2028 inside a JSON string.
    clauses = [
        _parse_line(path, line_number, line)
        for line_number, line in enumerate(text[:-1].split("\n"), start=1)
    ]
    ids = [clause.id for clause in clauses]
    duplicates = sorted({clause_id for clause_id in ids if ids.count(clause_id) > 1})
    if duplicates:
        raise ClausesFileError(f"{path}: id(s) used more than once: {', '.join(duplicates)}")
    return clauses


def check_clauses(
    clauses: list[Clause], rules: list[Rule], sections: dict[str, Section]
) -> list[str]:
    """Describe every way the clauses disagree with the rules or the Act; empty means none."""
    rules_by_section = {rule.section: rule for rule in rules}
    problems = []
    for clause in clauses:
        rule = rules_by_section.get(clause.section)
        if rule is None:
            problems.append(f"{clause.id}: s. {clause.section} is not one of the rules")
            continue
        if clause.source != rule.source:
            problems.append(f"{clause.id}: source {clause.source}, rule has {rule.source}")
        for other in sorted(set(clause.also_accept)):
            if other == clause.section or clause.also_accept.count(other) > 1:
                problems.append(f"{clause.id}: s. {other} repeated in also_accept")
            elif other not in sections:
                problems.append(f"{clause.id}: also_accept s. {other} is not in the Act")
        section = sections.get(clause.section)
        if section is None:
            problems.append(f"{clause.id}: s. {clause.section} is not in the Act")
        elif not quote_in_text(clause.quote, section.text, fold_quotation_marks=False):
            problems.append(
                f"{clause.id}: quote is not whole words within one subsection of "
                f"s. {clause.section}"
            )
    return problems


def _parse_line(path: Path, line_number: int, line: str) -> Clause:
    if not line:
        raise ClausesFileError(f"{path}:{line_number}: blank line")
    try:
        return Clause.model_validate(json.loads(line, object_pairs_hook=_reject_duplicate_keys))
    except ValueError as error:  # JSONDecodeError and pydantic's ValidationError included
        raise ClausesFileError(f"{path}:{line_number}: {error}") from error


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    keys = [key for key, _ in pairs]
    repeated = sorted({key for key in keys if keys.count(key) > 1})
    if repeated:
        raise ValueError(f"key(s) given more than once: {', '.join(repeated)}")
    return dict(pairs)
