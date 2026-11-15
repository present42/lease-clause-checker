"""The closed-book quotes that failed the word-for-word check: counts per model by how each
quote differs from the Act (`quotediff.classify`), a page listing every one with its difference
from the Act, and the hand check of each.

The hand checks live in their own file, `CHECKS_FILE`, beside the page: one row per quote, keyed
by model and clause. A person fills in `hand_check` with "agree" or the category they would
give instead, and `meaning_changed` with "yes" or "no": the code sorts wording, and only a
reader can say whether a different word changes what the provision means. Where it does,
`misleads_here` says whether the quote would mislead on the clause being checked ("yes", "no"
or "unsure"): a reader's judgement, not legal advice. Writing the page adds a row for any new
quote and keeps every hand check already written, so regenerating never erases one.
"""

import csv
import io
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from lease_clause_checker import quotediff
from lease_clause_checker.closedbook import ClosedBookResult
from lease_clause_checker.quotediff import Category, Classified
from lease_clause_checker.report import ModelRun

REVIEW_FILE = "quote-review.md"
CHECKS_FILE = "quote-hand-check.csv"
CHECKS_COLUMNS = (
    "model",
    "clause",
    "category",
    "hand_check",
    "meaning_changed",
    "misleads_here",
    "note",
)
LABELS: dict[Category, str] = {c: label for c, label in quotediff.LABELS.items() if c != "verbatim"}
"""Every category but `verbatim`, as the table's columns."""
NOT_A_RULE: frozenset[str] = frozenset({"no_quote", "not_in_act", "too_short", "heading"})
"""Categories of a quote that states no rule from the Act's operative text."""
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
    misleads_here: str
    """"" if not judged yet, "yes", "no" or "unsure": whether a quote that changes what the Act
    says would mislead on the clause being checked. Only for `meaning_changed` "yes"."""
    note: str

    def agrees(self, category: Category) -> bool:
        return self.hand_check in ("agree", category)

    def category(self, category: Category) -> str:
        """The category the hand check gives to a quote the code sorted as `category`."""
        return category if self.hand_check == "agree" else self.hand_check


HandChecks = dict[tuple[str, str], HandCheck]
"""By (model key, clause id)."""


def misleads(check: HandCheck | None, category: Category) -> bool:
    """Whether the hand check found that a quote the code sorted as `category` states a rule
    from the Act, changes what the Act says, and would mislead on the clause: the quotes
    `judged` counts as misleading."""
    return (
        check is not None
        and check.category(category) not in NOT_A_RULE
        and (check.meaning_changed, check.misleads_here) == ("yes", "yes")
    )


NOT_CHECKED = HandCheck("", "", "", "")


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


@dataclass(frozen=True)
class Judged:
    """A finished hand check: every quote checked, read for meaning where it states a rule, and
    judged for misleading where it changes what the Act says."""

    quotes: int
    rules: int
    """Quotes that state a rule from the Act (their hand category is not in `NOT_A_RULE`)."""
    changes: int
    """Of those, the quotes that change what the Act says."""
    misleads: int
    """Of those, the quotes that would mislead on the clause being checked."""
    unsure: int


def judged(runs: Sequence[ModelRun], checks: HandChecks) -> Judged | None:
    """The finished hand check's counts; None while any part of it is missing."""
    rows = [(checks.get((run.spec.key, r.clause.id)), q) for run in runs for r, q in failed(run)]
    if not rows or any(check is None or not check.hand_check for check, _ in rows):
        return None
    done = [(check, quote) for check, quote in rows if check is not None]
    rules = [check for check, quote in done if check.category(quote.category) not in NOT_A_RULE]
    changes = [check for check in rules if check.meaning_changed == "yes"]
    if any(not check.meaning_changed for check in rules) or any(
        not check.misleads_here for check in changes
    ):
        return None
    misleads = [check.misleads_here for check in changes]
    return Judged(
        len(done), len(rules), len(changes), misleads.count("yes"), misleads.count("unsure")
    )


def failed_categories(runs: Sequence[ModelRun]) -> dict[tuple[str, str], Category]:
    """The code's category of every quote that failed the check, by (model key, clause id)."""
    return {
        (run.spec.key, result.clause.id): quote.category
        for run in runs
        for result, quote in failed(run)
    }


def hand_check_line(runs: Sequence[ModelRun], checks: HandChecks, labelled_by: str = "") -> str:
    """How many quotes' categories have a label, and, once all have, how the labels were made
    (`labelled_by`, from the hand check's record; none if there is no record); how many the check
    agreed with; then, from the hand check alone, how many state a rule from the Act, how many
    of those change what it says, and how many of those would mislead on the clause being
    checked. A meaning judgement on a quote that states no rule is not counted, and said so."""
    rows = [(run.spec.key, result.clause.id, q) for run in runs for result, q in failed(run)]
    found = [(checks[m, c], q) for m, c, q in rows if (m, c) in checks]
    done = [(check, q) for check, q in found if check.hand_check]
    if not done:
        line = "No category has a label yet."
        meanings = [check.meaning_changed for check, _ in found if check.meaning_changed]
        if not meanings:
            return f"{line} No quote has yet been read by hand for meaning."
        return (
            f"{line} Read by hand for meaning, {meanings.count('yes')} of {len(meanings)} quotes "
            f"change what the Act says{_misleads_line([check for check, _ in found])}"
        )
    agreed = sum(check.agrees(quote.category) for check, quote in done)
    complete = len(done) == len(rows)
    states = [check.category(quote.category) not in NOT_A_RULE for check, quote in done]
    rules = [check for (check, _), rule in zip(done, states, strict=True) if rule]
    uncounted = sum(
        bool(check.meaning_changed)
        for (check, _), rule in zip(done, states, strict=True)
        if not rule
    )
    unread = sum(not check.meaning_changed for check in rules)
    changes = sum(check.meaning_changed == "yes" for check in rules)
    scope = (
        f"Of the {_quotes(len(rows))}"
        if complete
        else f"Of the {_quotes(len(done))} checked so far"
    )
    line = (
        (
            (
                f"Every category has a reviewed label: {labelled_by}"
                if labelled_by
                else "Every category has a label"
            )
            if complete
            else f"{len(done)} of the {len(rows)} categories have a label so far"
        )
        + "; the hand check agreed with the code's category for "
        f"{agreed}. {scope}, {len(rules)} {_verb(len(rules), 'state')} a rule from the Act"
        + (f" ({unread} not yet read for meaning)" if unread else "")
        + f", {changes} {_verb(changes, 'change')} what it says"
        + _misleads_line(rules)
    )
    if uncounted:
        line += (
            f" A meaning judgement on {_quotes(uncounted)} that {_verb(uncounted, 'state')} no "
            "rule is not counted."
        )
    return line


def _quotes(count: int) -> str:
    return f"{count} quote" if count == 1 else f"{count} quotes"


def _verb(count: int, verb: str) -> str:
    """`verb` agreeing with `count` quotes: "1 states", "2 state"."""
    return f"{verb}s" if count == 1 else verb


def _misleads_line(checks: list[HandCheck]) -> str:
    """How many of the quotes that change what the Act says would mislead on the clause being
    checked, with those unsure and those not yet judged apart."""
    changes = [check.misleads_here for check in checks if check.meaning_changed == "yes"]
    if not changes:
        return "."
    question = "would mislead on the clause being checked"
    if not any(changes):
        return f"; whether each {question} has not yet been judged."
    unsure = changes.count("unsure")
    waiting = changes.count("")
    return (
        f", and {changes.count('yes')} {question} (not legal advice; "
        f"{unsure} marked unsure" + (f", {waiting} not yet judged" if waiting else "") + ")."
    )


def write_review(
    runs: Sequence[ModelRun],
    folder: Path,
    notice: str,
    prompt_version: str,
    labelled_by: str = "",
) -> HandChecks:
    """Write `REVIEW_FILE` and bring `CHECKS_FILE` up to date in `folder`: a row for each failed
    quote, its category refreshed, its hand check and note kept. Returns the hand checks.
    `labelled_by` is as for `hand_check_line`."""
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
        check = checks.get((model, clause), NOT_CHECKED)
        writer.writerow(
            (
                model,
                clause,
                category,
                check.hand_check,
                check.meaning_changed,
                check.misleads_here,
                check.note,
            )
        )
    path.write_text(out.getvalue(), encoding="utf-8")
    (folder / REVIEW_FILE).write_text(
        review_page(runs, checks, notice, prompt_version, labelled_by), encoding="utf-8"
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
            misleads = (row.get("misleads_here") or "").strip().lower()
            if misleads not in ("", "yes", "no", "unsure"):
                raise HandCheckError(
                    f"{where}: misleads_here {misleads!r} is not 'yes', 'no' or 'unsure'"
                )
            if misleads and meaning != "yes":
                raise HandCheckError(
                    f"{where}: misleads_here is only for a quote whose meaning_changed is 'yes'"
                )
            checks[row["model"], row["clause"]] = HandCheck(
                hand, meaning, misleads, row["note"].strip()
            )
    return checks


def review_page(
    runs: Sequence[ModelRun],
    checks: HandChecks,
    notice: str,
    prompt_version: str,
    labelled_by: str = "",
) -> str:
    """Every quote that is not verbatim, with its category, its difference from the Act and its
    hand check, by model. `labelled_by` is as for `hand_check_line`."""
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
        "and, only where it does, `misleads_here` with `yes`, `no` or `unsure` (would it "
        "mislead on the clause being checked? not legal advice); add a "
        "note if useful, and regenerate. " + hand_check_line(runs, checks, labelled_by),
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
            check = checks.get((run.spec.key, result.clause.id), NOT_CHECKED)
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
        f"misleads here: {check.misleads_here}" if check.misleads_here else "",
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
