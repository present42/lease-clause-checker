"""The closed-book quotes that failed the word-for-word check: counts per model by how each
quote differs from the Act (`quotediff.classify`), a page listing every one with its difference
from the Act, and the hand check of each.

The hand checks live in their own file, `CHECKS_FILE`, beside the page: one row per quote, keyed
by model and clause. A person fills in `hand_check` with "agree" or the category they would
give instead, and `meaning_changed` with "yes" or "no": the code sorts wording, and only a
reader can say whether a different word changes what the provision means. Writing the page
adds a row for any new quote and keeps every hand check already written, so regenerating never
erases one.
"""

import csv
import io
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from lease_clause_checker.closedbook import ClosedBookResult
from lease_clause_checker.quotediff import Category, Classified
from lease_clause_checker.report import ModelRun

REVIEW_FILE = "quote-review.md"
CHECKS_FILE = "quote-hand-check.csv"
CHECKS_COLUMNS = ("model", "clause", "category", "hand_check", "meaning_changed", "note")
LABELS: dict[Category, str] = {
    "drops_negation": "drops a negation",
    "conventions": "passes with quoting conventions",
    "cosmetic": "cosmetic",
    "wrong_cite": "wrong section cited",
    "content_words_differ": "content words differ",
    "not_in_act": "not in the Act",
    "no_quote": "no quote",
    "too_short": "too short",
    "heading": "heading only",
}
"""Every category but `verbatim`, as the table's columns."""
ALWAYS_SHOWN: tuple[Category, ...] = (
    "conventions",
    "cosmetic",
    "wrong_cite",
    "content_words_differ",
    "not_in_act",
)


class HandCheckError(ValueError):
    """The hand-check file has a value it doesn't allow."""


@dataclass(frozen=True)
class HandCheck:
    hand_check: str
    """"" if not checked yet, "agree", or the category the checker would give instead."""
    meaning_changed: str
    """"" if not judged yet, "yes" or "no": whether the quote changes what the Act says."""
    note: str

    def agrees(self, category: Category) -> bool:
        return self.hand_check in ("agree", category)


HandChecks = dict[tuple[str, str], HandCheck]
"""By (model key, clause id)."""


def failed(run: ModelRun) -> list[tuple[ClosedBookResult, Classified]]:
    """The answers with a verdict whose quote is not `verbatim`, whatever the reason."""
    return [
        (result, result.quote)
        for result in run.closed_book
        if result.quote is not None and result.quote.category != "verbatim"
    ]


def columns(runs: Sequence[ModelRun]) -> list[Category]:
    """`ALWAYS_SHOWN`, plus any other category that some quote falls in, in `LABELS` order."""
    present = {quote.category for run in runs for _, quote in failed(run)}
    return [c for c in LABELS if c in ALWAYS_SHOWN or c in present]


def summary_table(runs: Sequence[ModelRun]) -> str:
    """One markdown row per model: answers with a verdict, how many quotes failed the strict
    check, and how they differ from the Act."""
    shown = columns(runs)
    lines = [
        "| model | answers with a verdict | failed the check | "
        + " | ".join(LABELS[c] for c in shown)
        + " |",
        "|---" * (3 + len(shown)) + "|",
    ]
    for run in runs:
        counts = Counter(quote.category for _, quote in failed(run))
        with_verdict = sum(result.quote is not None for result in run.closed_book)
        cells = [str(counts[c]) for c in shown]
        lines.append(
            f"| {run.spec.name} | {with_verdict} | {sum(counts.values())} | "
            + " | ".join(cells)
            + " |"
        )
    return "\n".join(lines)


def hand_check_line(runs: Sequence[ModelRun], checks: HandChecks) -> str:
    """How many quotes have had their category checked by hand, and how many of those the check
    agreed with; then how many were read for meaning, and how many of those change what the Act
    says, also among those the code sorted as "content words differ"."""
    rows = [(run.spec.key, result.clause.id, q) for run in runs for result, q in failed(run)]
    found = [(checks[m, c], q) for m, c, q in rows if (m, c) in checks]
    done = [(check, q) for check, q in found if check.hand_check]
    if not done:
        line = "The categories have not yet been checked by hand."
    else:
        agreed = sum(check.agrees(quote.category) for check, quote in done)
        so_far = "" if len(done) == len(rows) else " so far"
        line = (
            f"{len(done)} of the {len(rows)} categories have been checked by hand{so_far}; the "
            f"hand check agreed with {agreed} of them."
        )
    judged = [(check.meaning_changed, q.category) for check, q in found if check.meaning_changed]
    if not judged:
        return f"{line} No quote has yet been read by hand for meaning."
    changes = sum(meaning == "yes" for meaning, _ in judged)
    line += f" Read by hand for meaning, {changes} of {len(judged)} quotes change what the Act says"
    content = [meaning for meaning, category in judged if category == "content_words_differ"]
    if content:
        line += (
            f" ({content.count('yes')} of the {len(content)} that the code sorted as "
            f"{LABELS['content_words_differ']})"
        )
    return line + "."


def write_review(
    runs: Sequence[ModelRun], folder: Path, notice: str, prompt_version: str
) -> HandChecks:
    """Write `REVIEW_FILE` and bring `CHECKS_FILE` up to date in `folder`: a row for each failed
    quote, its category refreshed, its hand check and note kept. Returns the hand checks."""
    path = folder / CHECKS_FILE
    checks = load_checks(path)
    rows = [
        (run.spec.key, result.clause.id, quote.category)
        for run in runs
        for result, quote in failed(run)
    ]
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(CHECKS_COLUMNS)
    for model, clause, category in rows:
        check = checks.get((model, clause), HandCheck("", "", ""))
        writer.writerow(
            (model, clause, category, check.hand_check, check.meaning_changed, check.note)
        )
    path.write_text(out.getvalue(), encoding="utf-8")
    (folder / REVIEW_FILE).write_text(
        review_page(runs, checks, notice, prompt_version), encoding="utf-8"
    )
    return checks


def load_checks(path: Path) -> HandChecks:
    if not path.exists():
        return {}
    with path.open(encoding="utf-8", newline="") as file:
        checks = {}
        for row in csv.DictReader(file):
            where = f"{path}: {row['model']} {row['clause']}"
            hand = row["hand_check"].strip().lower()
            if hand not in ("", "agree", *LABELS):
                raise HandCheckError(
                    f"{where}: hand_check {hand!r} is not 'agree' or one of {', '.join(LABELS)}"
                )
            meaning = (row.get("meaning_changed") or "").strip().lower()
            if meaning not in ("", "yes", "no"):
                raise HandCheckError(f"{where}: meaning_changed {meaning!r} is not 'yes' or 'no'")
            checks[row["model"], row["clause"]] = HandCheck(hand, meaning, row["note"].strip())
    return checks


def review_page(
    runs: Sequence[ModelRun], checks: HandChecks, notice: str, prompt_version: str
) -> str:
    """Every quote that is not verbatim, with its category, its difference from the Act and its
    hand check, by model."""
    total = sum(len(failed(run)) for run in runs)
    lines = [
        "# Closed-book quotes that failed the check",
        "",
        f"Generated by `uv run lcc closedbook quotes` (and `make report`) from the saved "
        f"`{prompt_version}` answers on the main test set; do not edit. {total} quotes: every "
        "answer with a verdict whose quote failed the strict check against the section it "
        "cites. The "
        "categories, and the order they are tried in, are defined in "
        "[quotediff.py](../../src/lease_clause_checker/quotediff.py). They describe wording "
        "only: a cosmetic difference is not a wrong statement of the law, and whether a changed "
        "word changes the meaning is for the hand check.",
        "",
        f"Hand checks are kept in [{CHECKS_FILE}]({CHECKS_FILE}): fill in `hand_check` with "
        "`agree` or the category you would give instead (one of "
        + ", ".join(f"`{c}`" for c in LABELS)
        + "), `meaning_changed` with `yes` or `no` (does the quote change what the Act says?), "
        "add a note if useful, and regenerate. " + hand_check_line(runs, checks),
        "",
        "In the **quote vs the Act** column, `[-…-]` marks the Act's words that the quote drops "
        "or replaces and `{+…+}` the quote's own words; the rest is in both. It runs from the "
        "quote's first matched word to its last, in the subsection named under **lined up "
        "with**: the cited section's, or another section's for a wrong citation. A quote that "
        "lines up with nothing in the Act is shown as given.",
        "",
        f"Statute words in this file: {notice}",
        "",
        summary_table(runs),
    ]
    for run in runs:
        rows = failed(run)
        if not rows:
            continue
        lines += [
            "",
            f"## {run.spec.name}",
            "",
            "| clause | verdict | cited | category | lined up with | words lined up | "
            "critical words | quote vs the Act | hand check |",
            "|---|---|---|---|---|---|---|---|---|",
        ]
        for result, quote in rows:
            check = checks.get((run.spec.key, result.clause.id), HandCheck("", "", ""))
            lines.append(_row(result, quote, check))
    return "\n".join(lines) + "\n"


def _row(result: ClosedBookResult, quote: Classified, check: HandCheck) -> str:
    given = result.attempt.answer
    cited = f"s. {given.cited_section}" if given and given.cited_section else "none"
    verdict = "right" if result.answer.verdict == result.clause.label else "wrong"
    alignment = quote.alignment
    if alignment is None:
        where, words = "nothing", ""
        text = f"“{given.quote}”" if given and given.quote else ""
    else:
        where = f"s. {alignment.section}{alignment.subsection}"
        words = f"{alignment.matched_words} of {alignment.quote_words}"
        text = alignment.wdiff()
    category = LABELS[quote.category]
    if quote.category == "wrong_cite":
        others = ", ".join(f"s. {n}" for n in quote.found_in)
        changed = ", words changed" if quote.words_changed else ""
        category += f" ({others}{changed})"
    parts = [
        check.hand_check,
        f"meaning changed: {check.meaning_changed}" if check.meaning_changed else "",
        check.note,
    ]
    hand = "; ".join(part for part in parts if part)
    cells = (
        result.clause.id,
        verdict,
        cited,
        category,
        where,
        words,
        ", ".join(quote.critical),
        text,
        hand,
    )
    return "| " + " | ".join(cell.replace("|", "\\|") for cell in cells) + " |"
