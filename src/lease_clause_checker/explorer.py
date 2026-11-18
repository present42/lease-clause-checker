"""Export the saved answers as data for the static explorer (`lcc site`).

`lcc site` writes the page (copied from `explorer_assets/`) and `data.json`, built from the
same scored answers as `make report` (a `scoreboard.Board`), so its counts match the README's.
No API is called. The export holds the answers of the models in `models.MODELS` on the main
and stress sets, by every method; it leaves out the dropped models' records
(`models.DROPPED_MODELS`), the stress-candidate selection run and the `lcc check` demo.

Each quote is lined up with the subsection of its cited section that it matches best, by
`quotediff.align`, the alignment the quote categories use, so the page marks where a quote
leaves the Act. The statute text in `data.json` is those subsections, each clause's decisive
words and the headings of the sections cited; the models' own quotes and reasons also repeat
the Act's words. The page shows the King's Printer notice beside them.
"""

import html
import json
import re
import shutil
from collections import Counter, defaultdict
from collections.abc import Iterator, Sequence
from dataclasses import asdict
from pathlib import Path
from typing import Any
from urllib.parse import quote

from lease_clause_checker import (
    elaws,
    exhibits,
    handcheck,
    quotediff,
    quotereview,
    scoreboard,
    verify,
)
from lease_clause_checker.clauses import Clause
from lease_clause_checker.models import DROPPED_MODELS, MODELS
from lease_clause_checker.pipeline import RETRY_SUFFIX, Attempt
from lease_clause_checker.quotediff import Alignment, Classified
from lease_clause_checker.runs import RunRecord
from lease_clause_checker.scoreboard import Board, ScoredSet
from lease_clause_checker.scoring import Score
from lease_clause_checker.statute import Section

ASSETS = Path(__file__).parent / "explorer_assets"
REPOSITORY = "https://github.com/present42/lease-clause-checker"
REPOSITORY_SLOT = "{{repository}}"
"""Where the page's HTML names the repository; `write` fills it in."""
PAGES = "https://present42.github.io/lease-clause-checker/"
"""Where the page will be published (GitHub Pages), for the preview image's absolute link."""
PAGES_SLOT = "{{pages}}"
"""Where the page's HTML names that address; `write` fills it in."""
OG_ALT_SLOT = "{{og_alt}}"
"""Where the page's HTML describes the preview image (`og_alt`); `write` fills it in."""
MARKER = ".lcc-site"
"""A file `write` leaves in the folder, so the next export knows it may replace the folder."""

SubsectionKey = tuple[str, str]
"""(section number, subsection marker)."""


class SiteExportError(RuntimeError):
    """The export doesn't hold exactly the saved answers, or holds statute text no quote uses."""


def build(
    board: Board,
    sections: dict[str, Section],
    act: elaws.CachedAct,
    prompts: dict[str, str],
    rule_sections: int,
    closed_book_folder: str,
    configured: Sequence[exhibits.Exhibit],
) -> dict[str, Any]:
    """Everything the page shows: the opening story with its exhibits (`configured`), clauses,
    answers with their alignments, the hand check, the subsections the alignments use, and the
    heading and link of every section cited. `prompts` holds each method's prompt version;
    `rule_sections` is how many sections the pipeline's prompt holds; `closed_book_folder` is
    where the closed-book answers and their hand check are, relative to the repository, such as
    "results/closedbook-v1"."""
    if not board.closed_book:
        raise SiteExportError("no closed-book run is saved, so the explorer has no story")
    ids = [c.id for scored in (board.main, board.stress) for c in scored.clauses]
    if len(ids) != len(set(ids)):
        raise SiteExportError("a clause id is in both sets, so the page couldn't tell them apart")
    used: dict[SubsectionKey, list[quotediff.Token]] = {}
    answers = [
        answer
        for set_name, scored in (("main", board.main), ("stress", board.stress))
        for answer in _set_answers(set_name, scored, board, sections, used)
    ]
    clauses = [
        _clause(clause, set_name)
        for set_name, scored in (("main", board.main), ("stress", board.stress))
        for clause in scored.clauses
    ]
    cited = {c["section"] for c in clauses} | {
        number
        for answer in answers
        for number in (
            answer["section"],
            *(attempt.get("section") for attempt in answer.get("attempts", [])),
            *(a["alignment"]["section"] for a in answer.get("attempts", []) if a.get("alignment")),
        )
        if number in sections
    }
    story = _story(board, used, configured)
    act_sections: dict[str, dict[str, Any]] = {
        number: {
            "heading": sections[number].heading,
            "url": elaws.section_url(sections[number].anchor),
            "subsections": {},
        }
        for number in sorted(cited, key=_numbers)
    }
    for (number, marker), part in sorted(used.items(), key=lambda item: _order(item[0])):
        act_sections[number]["subsections"][marker] = [[t.text, t.before] for t in part]
    verified, checked = scoreboard.first_pipeline_checks(board)
    _check_hand_rows(answers, story["hand_check"])
    return {
        "notice": elaws.NOTICE,
        "prompts": prompts,
        "settings": {
            "repository": REPOSITORY,
            "pages": PAGES,
            "rule_sections": rule_sections,
            "min_run": quotediff.MIN_RUN,
            "min_quote_words": verify.MIN_QUOTE_WORDS,
        },
        "pipeline_first_checks": {"verified": verified, "checked": checked},
        "runs": _runs(board),
        "story": story,
        "hand_check": _hand_check(board, closed_book_folder),
        "act": {
            "url": elaws.PUBLIC_URL,
            "retrieved": act.retrieved_at.date().isoformat(),
            "sha256": act.content_sha256,
            "sections": act_sections,
        },
        "models": [{"key": spec.key, "name": spec.name, "weight": spec.weight} for spec in MODELS],
        "dropped_models": [spec.name for spec in DROPPED_MODELS],
        "clauses": clauses,
        "answers": answers,
    }


def check_counts(
    data: dict[str, Any], expected: dict[tuple[str, str, str], tuple[int, int]]
) -> None:
    """Raise unless `data` holds, for each run (set, method, model), the saved number of first
    answers and of retries in `expected`, and exactly the subsections its alignments use."""
    counts = Counter((a["set"], a["method"], a["model"]) for a in data["answers"])
    retries = Counter(
        (a["set"], a["method"], a["model"])
        for a in data["answers"]
        if len(a.get("attempts", [])) > 1
    )
    wrong = [
        f"{set_name} {method} {model}: {counts[key]} answers and {retries[key]} retries "
        f"exported, {first} and {retry} saved"
        for key, (first, retry) in sorted(expected.items())
        for set_name, method, model in [key]
        if (counts[key], retries[key]) != (first, retry)
    ]
    wrong += [f"{key}: exported but not expected" for key in sorted(set(counts) - set(expected))]
    used = {
        (attempt["alignment"]["section"], attempt["alignment"]["subsection"])
        for answer in data["answers"]
        for attempt in answer.get("attempts", [])
        if attempt.get("alignment")
    }
    exported = {
        (number, marker)
        for number, section in data["act"]["sections"].items()
        for marker in section["subsections"]
    }
    wrong += [f"s. {n}{m}: exported but no quote uses it" for n, m in sorted(exported - used)]
    wrong += [f"s. {n}{m}: used but not exported" for n, m in sorted(used - exported)]
    if wrong:
        raise SiteExportError("; ".join(wrong))


def saved_counts(
    folders: dict[tuple[str, str], Path], clauses: dict[str, list[Clause]]
) -> dict[tuple[str, str, str], tuple[int, int]]:
    """How many clauses of each set have a first answer, and how many a retry, saved on disk,
    by (set, method, model); `folders[set, method]` holds one folder per model key. Counted
    from the files, not the loaders, so an answer a loader drops is caught by `check_counts`."""
    return {
        (set_name, method, spec.key): (
            sum((folder / spec.key / f"{c.id}.json").exists() for c in clauses[set_name]),
            sum(
                (folder / spec.key / f"{c.id}{RETRY_SUFFIX}.json").exists()
                for c in clauses[set_name]
            ),
        )
        for (set_name, method), folder in folders.items()
        for spec in MODELS
    }


def write(data: dict[str, Any], out: Path) -> None:
    """The page's files and `data.json` into `out`, replacing an earlier export there. Refuses
    a folder that holds anything but no earlier export (no `MARKER`), so a mistyped path can't
    empty the wrong folder."""
    if out.exists() and any(out.iterdir()):
        if not (out / MARKER).exists():
            raise SiteExportError(f"{out} is not empty and holds no earlier export; not replaced")
        for path in out.iterdir():  # the folder itself stays, so a server running in it still works
            shutil.rmtree(path) if path.is_dir() else path.unlink()
    out.mkdir(parents=True, exist_ok=True)
    repository = data["settings"]["repository"]
    for asset in sorted(ASSETS.iterdir()):
        if asset.suffix == ".html":
            text = asset.read_text(encoding="utf-8").replace(REPOSITORY_SLOT, repository)
            text = text.replace(PAGES_SLOT, data["settings"]["pages"])
            text = text.replace(OG_ALT_SLOT, html.escape(og_alt(data)))
            (out / asset.name).write_text(text, encoding="utf-8")
        elif asset.is_file():
            (out / asset.name).write_bytes(asset.read_bytes())
        elif asset.is_dir() and asset.name != "__pycache__":
            shutil.copytree(asset, out / asset.name)
    text = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    (out / "data.json").write_text(text + "\n", encoding="utf-8")
    (out / MARKER).write_text("Written by `lcc site`; the next export replaces this folder.\n")


def og_alt(data: dict[str, Any]) -> str:
    """What the preview image (the #/card view) shows, for its alt text: the first exhibit."""
    first = data["story"]["exhibits"][0]
    [name] = [m["name"] for m in data["models"] if m["key"] == first["model"]]
    where = f"s. {first['alignment']['section']}{first['alignment']['subsection']}"
    return (
        f"{name}'s closed-book quote of {where} of the Residential Tenancies Act, 2006, as a "
        "redline against the Act: the Act's words it leaves out are struck in red, its own "
        "words underlined in blue."
    )


def _runs(board: Board) -> dict[str, Any]:
    """How the saved answers were asked for, read from the saved calls: each model's settings
    (one set per model, or the export stops) and the most answers any clause has from one model
    by one method (retries aside)."""
    calls: dict[str, list[RunRecord]] = defaultdict(list)
    answers: Counter[tuple[str, str, str, str]] = Counter()
    keys = {spec.key for spec in MODELS}
    runs = [
        (set_name, method, key, run)
        for set_name, scored in (("main", board.main), ("stress", board.stress))
        for (method, key), run in scored.runs.items()
    ] + [("main", "closedbook", key, run) for key, run in board.closed_book.items()]
    for set_name, method, key, run in runs:
        if key not in keys:
            continue
        calls[key] += run.records
        answers.update((set_name, method, key, s.clause.id) for s in run.scores)
    models = {}
    for spec in MODELS:
        found = {_call_settings(r.request) for r in calls[spec.key]}
        if len(found) != 1:
            raise SiteExportError(
                f"{spec.key}: the saved calls were made with {len(found)} different settings"
                if found
                else f"{spec.key}: no saved calls, so no settings to show"
            )
        tokens, effort, thinking, temperature = found.pop()
        models[spec.key] = {
            "max_output_tokens": tokens,
            "effort": effort,
            "thinking": thinking,
            "temperature": temperature,
        }
    return {"models": models, "answers_per_question": max(answers.values(), default=0)}


def _call_settings(request: dict[str, Any]) -> tuple[Any, ...]:
    """From a saved request, Claude's or Gemini's: the output-token cap, Claude's effort, the
    thinking setting (as JSON, None if the provider's default) and the temperature (None if not
    set)."""
    config = request.get("config", {})
    thinking = request.get("thinking", config.get("thinking_config"))
    return (
        request.get("max_tokens", config.get("max_output_tokens")),
        request.get("output_config", {}).get("effort"),
        None if thinking is None else json.dumps(thinking, sort_keys=True),
        request.get("temperature", config.get("temperature")),
    )


def _check_hand_rows(answers: list[dict[str, Any]], counts: dict[str, int]) -> None:
    """The hand view counts its rows; raise unless they agree with the story's counts."""
    rows = [
        a["attempts"][0]["hand_check"]
        for a in answers
        if a["method"] == "closedbook" and "hand_check" in a["attempts"][0]
    ]
    misleads = sum(row["misleads_here"] == "yes" for row in rows)
    if (len(rows), misleads) != (counts["quotes"], counts["misleads"]):
        raise SiteExportError(
            f"{len(rows)} hand-checked rows, {misleads} misleading; the story counts "
            f"{counts['quotes']} and {counts['misleads']}"
        )


def _story(
    board: Board,
    used: dict[SubsectionKey, list[quotediff.Token]],
    configured: Sequence[exhibits.Exhibit],
) -> dict[str, Any]:
    """The opening view: the short version's numbers and the exhibits, each quote set against
    the whole subsection it comes from and as the edits that turn that subsection into it."""
    numbers = scoreboard.short_numbers(board, configured)
    judged = numbers.hand_check
    return {
        "clauses": numbers.clauses,
        "rows": [asdict(row) for row in numbers.rows],
        "column_groups": [[group, list(headings)] for group, headings in scoreboard.SHORT_GROUPS],
        "table_note": scoreboard.SHORT_TABLE_NOTE,
        "failed_from": scoreboard.failed_from(numbers.rows),
        "table_title": scoreboard.short_table_title(numbers),
        "base_rate": {
            "right": numbers.right_closed,
            "misleading": numbers.right_misleading,
            "judged": judged.quotes,
            "means": scoreboard.MISLEADS_MEANS,
        },
        "hand_check": {
            "quotes": judged.quotes,
            "rules": judged.rules,
            "changes": judged.changes,
            "misleads": judged.misleads,
            "unsure": judged.unsure,
        },
        "exhibits": [_exhibit(shown, used) for shown in numbers.exhibits],
    }


def _exhibit(
    shown: scoreboard.ShownExhibit, used: dict[SubsectionKey, list[quotediff.Token]]
) -> dict[str, Any]:
    exhibit, replay = shown.exhibit, shown.replay
    given = shown.result.attempt.answer
    if given is None:  # an exhibit's verdict was checked right, so its answer was read
        raise SiteExportError(f"exhibit {shown.spec.key} {exhibit.clause}: no readable answer")
    return {
        "model": shown.spec.key,
        "clause": shown.result.clause.id,
        "label": exhibit.label,
        "punchline": exhibit.punchline,
        "note": exhibit.note,
        "verdict": shown.result.answer.verdict,
        "reason": given.reason,
        "alignment": _alignment(shown.alignment, used),
        "replay": {
            "act": list(replay.act),
            "box": [list(part) for part in replay.box],
            "edits": [
                {
                    "drop": list(edit.drop) if edit.drop else None,
                    "add": list(edit.add) if edit.add else None,
                    "said": edit.said,
                }
                for edit in replay.edits
            ],
            "tag": list(replay.tag) if replay.tag else None,
            "tag_note": exhibit.tag.note if exhibit.tag else None,
        },
    }


def _hand_check(board: Board, folder: str) -> dict[str, Any] | None:
    """How the hand check was made, with links to its record and CSV in `folder` on the
    repository's main branch; None if it is not recorded."""
    record = board.hand_check_record
    if record is None:
        return None
    where = quote(folder)
    return {
        "method": handcheck.method_sentence(record),
        "labels": handcheck.label_note(record),
        "recheck": handcheck.RECHECK_NOTE,
        "record": f"{REPOSITORY}/tree/main/{where}/{handcheck.RECORD_DIR}",
        "csv": f"{REPOSITORY}/blob/main/{where}/{quotereview.CHECKS_FILE}",
    }


def _set_answers(
    set_name: str,
    scored: ScoredSet,
    board: Board,
    sections: dict[str, Section],
    used: dict[SubsectionKey, list[quotediff.Token]],
) -> Iterator[dict[str, Any]]:
    for spec in MODELS:
        base = scored.run("baseline", spec)
        texts = {record.clause_id: record.text for record in base.records}
        for s in base.scores:
            yield _answer(set_name, "baseline", spec.key, s) | {"text": texts[s.clause.id]}
        pipe = scored.run("pipeline", spec)
        for result, s in zip(pipe.results, pipe.scores, strict=True):
            attempts = [_attempt(a, sections, used) for a in result.attempts]
            yield _answer(set_name, "pipeline", spec.key, s) | {
                "attempts": attempts,
                "gave_up": result.gave_up,
            }
        closed = board.closed_book.get(spec.key) if set_name == "main" else None
        if closed is None:
            continue
        for cb, s in zip(closed.closed_book, closed.scores, strict=True):
            attempt = _attempt(cb.attempt, sections, used, cb.quote)
            hand = board.quote_checks.get((spec.key, cb.clause.id))
            if hand is not None and cb.quote is not None and hand.hand_check:
                attempt["hand_check"] = _hand(hand, cb.quote)
            yield _answer(set_name, "closedbook", spec.key, s) | {"attempts": [attempt]}


def _answer(set_name: str, method: str, model: str, score: Score) -> dict[str, Any]:
    return {
        "set": set_name,
        "method": method,
        "model": model,
        "clause": score.clause.id,
        "verdict": score.answer.verdict,
        "section": score.answer.section,
        "outcome": score.outcome,
        "section_result": score.section_result,
    }


def _attempt(
    attempt: Attempt,
    sections: dict[str, Section],
    used: dict[SubsectionKey, list[quotediff.Token]],
    classified: Classified | None = None,
) -> dict[str, Any]:
    """One answer and its quote check. `classified`, for a closed-book answer, gives the
    quote's category, and for a wrong citation the other section it lines up with."""
    given = attempt.answer
    if given is None:
        return {"readable": False}
    entry: dict[str, Any] = {
        "readable": True,
        "verdict": given.verdict,
        "section": given.cited_section,
        "quote": given.quote,
        "reason": given.reason,
        "check": attempt.check,
    }
    section = sections.get(given.cited_section)
    if attempt.check is None or section is None or not given.quote.strip():
        return entry
    if classified is not None and classified.category != "verbatim":
        entry["category"] = quotediff.LABELS[classified.category]
    alignment = quotediff.align(given.quote, section)
    if classified is not None and classified.category == "wrong_cite" and classified.alignment:
        alignment = classified.alignment  # the section the words come from, not the one cited
    if attempt.check == "verified":
        if alignment is None or alignment.matched_words != alignment.quote_words:
            raise SiteExportError(
                f"{attempt.record.model_key} {attempt.record.clause_id}: a verified quote must "
                "line up in full"
            )
        ending = verify.ending(given.quote, section.number, sections)
        entry["stops_early"] = ending is not None and ending.completeness == "stops_early"
    if alignment is not None:
        entry["alignment"] = _alignment(alignment, used)
    if attempt.check == "not_found":
        entry["found_in"] = _found_in(given.quote, sections)
    return entry


def _alignment(
    alignment: Alignment, used: dict[SubsectionKey, list[quotediff.Token]]
) -> dict[str, Any]:
    used.setdefault((alignment.section, alignment.subsection), list(alignment.act))
    return {
        "section": alignment.section,
        "subsection": alignment.subsection,
        "quote": [[t.text, t.before] for t in alignment.quote],
        "redline": _redline(alignment),
        "lined_up": alignment.lined_up,
        "passages": len(alignment.blocks),
        "matched_words": alignment.matched_words,
        "quote_words": alignment.quote_words,
    }


def _redline(alignment: Alignment) -> list[list[Any]]:
    """The quote against its whole subsection, as `Alignment.runs(whole=True)` gives it (the
    one redline the README shows too): [kind, start, end] runs, "same" and "del" indexing the
    subsection's tokens, which the export holds, and "ins" the quote's."""
    return [list(run) for run in alignment.runs(whole=True)]


def _hand(check: quotereview.HandCheck, classified: Classified) -> dict[str, str]:
    category = check.category(classified.category)
    return {
        "code_category": quotediff.LABELS[classified.category],
        "category": quotediff.LABELS.get(category, category),  # type: ignore[call-overload]
        "states_a_rule": "no" if category in quotereview.NOT_A_RULE else "yes",
        "meaning_changed": check.meaning_changed,
        "misleads_here": check.misleads_here,
        "note": check.note,
    }


def _found_in(quote: str, sections: dict[str, Section]) -> list[str]:
    """The other sections that hold the quote word for word, if it is long enough to say."""
    wanted = verify.normalise(quote).strip('"')
    if verify.word_count(wanted) < verify.MIN_QUOTE_WORDS:
        return []
    return [n for n, s in sections.items() if verify.quote_in_text(wanted, s.text)]


def _clause(clause: Clause, set_name: str) -> dict[str, Any]:
    return {
        "id": clause.id,
        "set": set_name,
        "text": clause.text,
        "label": clause.label,
        "section": clause.section,
        "also_accept": clause.also_accept,
        "quote": clause.quote,
        "rationale": clause.rationale,
    }


def _order(key: SubsectionKey) -> tuple[tuple[float, ...], tuple[float, ...]]:
    number, marker = key
    return _numbers(number), _numbers(marker)


def _numbers(text: str) -> tuple[float, ...]:
    return tuple(float(n) for n in re.findall(r"\d+(?:\.\d+)?", text))
