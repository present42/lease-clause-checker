import json
import re
import xml.etree.ElementTree as ET
from dataclasses import replace
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Literal
from unittest.mock import Mock

import pytest

from lease_clause_checker import exhibits, handcheck, quotereview, scoreboard
from lease_clause_checker.clauses import Clause
from lease_clause_checker.closedbook import ClosedBookResult
from lease_clause_checker.models import MODELS, ModelSpec, model_by_key
from lease_clause_checker.pipeline import Attempt, PipelineAnswer, Result
from lease_clause_checker.quotediff import Category, Classified, classify
from lease_clause_checker.report import ModelRun
from lease_clause_checker.runs import RunRecord
from lease_clause_checker.scoring import Answer, Score, Verdict, score_answer
from lease_clause_checker.stats import difference_interval
from lease_clause_checker.statute import Section
from lease_clause_checker.verify import QuoteCheck

HAIKU = model_by_key("claude-haiku-5-5")
OPUS = model_by_key("claude-opus-5-5")
GALLERY_IDS = {exhibit.clause_id for exhibit in scoreboard.GALLERY}
# Haiku's baseline misses in the test data; the gallery's clauses (c01, c10, c14, c24) must be
# among them. 3 false alarms (c21, c22, c24), 1 missed void (c01), 4 wrong sections.
HAIKU_WRONG: dict[str, Literal["verdict", "section", "both"]] = {
    "c01": "both",
    "c02": "section",
    "c10": "section",
    "c14": "section",
    "c21": "verdict",
    "c22": "verdict",
    "c24": "verdict",
}


def clause(clause_id: str, label: str = "void") -> Clause:
    return Clause.model_validate(
        {
            "id": clause_id,
            "text": f"Clause {clause_id}.",
            "label": label,
            "kind": "clear",
            "section": "14",
            "also_accept": ["4"],
            "rationale": "x",
            "quote": "x",
            "source": "x",
            "reviewed": True,
        }
    )


# c01 to c24, so the gallery's clauses are all present: 16 void, 8 allowed.
CLAUSES = [clause(f"c{i:02d}", "void" if i <= 16 else "allowed") for i in range(1, 25)]


def record(spec: ModelSpec, clause_id: str, text: str) -> RunRecord:
    return RunRecord(
        clause_id=clause_id,
        model_key=spec.key,
        model_id=spec.model_id,
        served_model=spec.model_id,
        prompt_version="test-v1",
        prompt_sha256="0" * 64,
        clauses_sha256="0" * 64,
        request={},
        requested_at=datetime(2026, 11, 12, tzinfo=UTC),
        text=text,
        finish_reason="end_turn",
        input_tokens=1000,
        output_tokens=100,
        raw={},
    )


def gallery_text(clause_id: str) -> str:
    excerpts = next(e.excerpts for e in scoreboard.GALLERY if e.clause_id == clause_id)
    return " ".join(excerpts)


def baseline(
    spec: ModelSpec, wrong: dict[str, Literal["verdict", "section", "both"]] | None = None
) -> ModelRun:
    """Right on every clause with the deciding section, except as listed in `wrong`."""
    wrong = wrong or {}
    scores, records = [], []
    for c in CLAUSES:
        flip: Verdict = "allowed" if c.label == "void" else "void"
        verdict = flip if wrong.get(c.id) in ("verdict", "both") else c.label
        section = "99" if wrong.get(c.id) in ("section", "both") else "14"
        scores.append(score_answer(c, Answer(verdict, section), {"4", "14", "99"}))
        records.append(record(spec, c.id, gallery_text(c.id) if c.id in GALLERY_IDS else "x"))
    return ModelRun(spec, records, scores)


def pipeline_run(spec: ModelSpec, exact_misses: int = 0, retry_c14: bool = True) -> ModelRun:
    """Right on every clause with a verified quote; c14's first quote fails and is retried."""
    scores, records, results = [], [], []
    for i, c in enumerate(CLAUSES):
        section = "4" if i < exact_misses and c.label == "void" else "14"
        quote = "A provision in a tenancy agreement prohibiting animals is void."
        answer = PipelineAnswer(verdict=c.label, section=section, quote=quote, reason="r")
        attempts: tuple[Attempt, ...] = (
            Attempt(record(spec, c.id, answer.model_dump_json()), answer, "verified"),
        )
        if c.id == "c14" and retry_c14:
            full = quote.replace(
                "tenancy agreement", "tenancy agreement" + scoreboard.RETRY_DROPPED
            )
            cut = answer.model_copy(update={"quote": quote})
            whole = answer.model_copy(update={"quote": full})
            attempts = (
                Attempt(record(spec, c.id, cut.model_dump_json()), cut, "not_found"),
                Attempt(record(spec, c.id, whole.model_dump_json()), whole, "verified"),
            )
        result = Result(c, attempts)
        results.append(result)
        records += [attempt.record for attempt in attempts]
        scores.append(result.score({"4", "14"}))
    return ModelRun(spec, records, scores, results)


def empty(spec: ModelSpec) -> ModelRun:
    return ModelRun(spec, [], [])


def board(
    overrides: dict[tuple[scoreboard.Method, str], ModelRun] | None = None,
) -> scoreboard.Board:
    """Claude runs as below and Gemini not run; the stress set has a clause of each kind."""
    runs: dict[tuple[scoreboard.Method, str], ModelRun] = {
        (method, spec.key): empty(spec) for method in scoreboard.METHODS for spec in MODELS
    }
    runs["baseline", HAIKU.key] = baseline(HAIKU, HAIKU_WRONG)
    runs["pipeline", HAIKU.key] = pipeline_run(HAIKU, exact_misses=3)
    runs["baseline", OPUS.key] = baseline(OPUS, {"c03": "section"})
    runs["pipeline", OPUS.key] = pipeline_run(OPUS)
    runs |= overrides or {}
    stress = [*CLAUSES[:3], CLAUSES[20]]  # c01, c02, c03 and c21
    return scoreboard.Board(
        scoreboard.ScoredSet(CLAUSES, runs),
        scoreboard.ScoredSet(stress, {k: scoreboard.restrict(r, stress) for k, r in runs.items()}),
        scoreboard.restrict(runs["baseline", HAIKU.key], stress),
    )


def table_rows(markdown: str, first_cell: str) -> list[list[str]]:
    return [
        [cell.strip() for cell in line.strip("|").split("|")]
        for line in markdown.splitlines()
        if line.startswith(f"| {first_cell}")
    ]


def test_headline_pairs_the_light_pipeline_with_the_heavy_baseline() -> None:
    text = scoreboard.render(board(), "chart.svg")

    [light] = table_rows(text, "Claude Haiku 5.5 + pipeline")
    [heavy] = table_rows(text, "Claude Opus 5.5 alone")
    [difference] = table_rows(text, "difference")
    low, high = difference_interval(
        [i >= 3 for i in range(24)], [c.id != "c03" for c in CLAUSES]
    )  # Haiku's pipeline misses the exact section on c01-c03, Opus's baseline on c03
    assert (light[1], light[2][:8]) == ("24 of 24 (see Notes)", "21 of 24")
    assert (heavy[1], heavy[2][:8]) == ("24 of 24 (see Notes)", "23 of 24")
    assert difference[1] == "0 (see Notes)"
    signed = scoreboard._signed
    assert difference[2] == f"{signed(-2)} ({signed(low)} to {signed(high)})"
    assert (
        "| Gemini 3.8 Flash + pipeline (light) vs Gemini 4 Argon alone (heavy) | not run yet"
        in text
    )


def test_main_table_counts_each_kind_of_miss_and_marks_models_not_run() -> None:
    text = scoreboard.render(board(), "chart.svg")

    [haiku_baseline, haiku_pipeline] = table_rows(text, "Claude Haiku 5.5 (light)")[:2]
    assert haiku_baseline[1] == "baseline"
    assert haiku_baseline[2].startswith("20 of 24 (")
    assert haiku_baseline[3:7] == ["3 of 8", "1 of 16", scoreboard.DASH, scoreboard.DASH]
    assert haiku_baseline[7] == "20 of 24"  # section ok: c01, c02, c10 and c14 cite s. 99
    assert haiku_baseline[8].startswith("20 of 24 (")
    assert haiku_pipeline[5:7] == ["0", "24 of 24"]  # no unsure; every final quote verified
    assert haiku_pipeline[7] == "24 of 24"  # s. 4 is accepted on c01-c03
    assert haiku_pipeline[8].startswith("21 of 24 (")
    assert table_rows(text, "Gemini 4 Argon (heavy)")[0][2] == "not run yet"


def test_stress_rows_count_wrong_verdicts_and_wrong_sections_apart() -> None:
    text = scoreboard.render(board(), "chart.svg")

    selection = next(r for r in table_rows(text, "Claude Haiku 5.5") if r[2] == "selection")
    assert selection[3:7] == ["2 of 4", scoreboard.DASH, "1 of 4", "1 of 4"]
    opus = next(r for r in table_rows(text, "Claude Opus 5.5") if r[1:3] == ["baseline", "fresh"])
    assert opus[3:7] == ["0 of 4", scoreboard.DASH, "1 of 4", "3 of 4"]


def test_light_right_heavy_wrong_names_the_clause_and_both_citations() -> None:
    text = scoreboard.render(board(), "chart.svg")

    assert "c03 (baseline: Claude Opus 5.5 cited s. 99; Claude Haiku 5.5 cited s. 14)" in text


def test_a_gallery_excerpt_missing_from_its_answer_stops_the_report() -> None:
    changed = baseline(HAIKU, HAIKU_WRONG)
    changed.records[0] = record(HAIKU, "c01", "Something else entirely.")

    with pytest.raises(scoreboard.ReportDataError, match="c01: not in the answer"):
        scoreboard.render(board({("baseline", HAIKU.key): changed}), "chart.svg")


def test_the_retry_exhibit_must_match_its_description() -> None:
    run = pipeline_run(HAIKU)
    first, second = run.results[13].attempts
    assert second.answer is not None
    unrelated = second.answer.model_copy(update={"quote": "Another quote."})
    run.results[13] = Result(CLAUSES[13], (first, Attempt(second.record, unrelated, "verified")))

    with pytest.raises(scoreboard.ReportDataError, match="retry"):
        scoreboard.render(board({("pipeline", HAIKU.key): run}), "chart.svg")


def test_replace_block_keeps_the_text_around_the_markers() -> None:
    readme = f"before\n{scoreboard.START}\nold\n{scoreboard.END}\nafter\n"

    replaced = scoreboard.replace_block(readme, f"{scoreboard.START}\nnew\n{scoreboard.END}\n")

    assert replaced == f"before\n{scoreboard.START}\nnew\n{scoreboard.END}\nafter\n"
    with pytest.raises(scoreboard.MissingMarkersError):
        scoreboard.replace_block("no markers", "x")


def test_replace_block_takes_other_markers() -> None:
    start, end = scoreboard.TLDR_START, scoreboard.TLDR_END
    readme = f"top\n{start}\nold\n{end}\n{scoreboard.START}\nkept\n{scoreboard.END}\n"

    replaced = scoreboard.replace_block(readme, f"{start}\nnew\n{end}", start, end)

    assert replaced == f"top\n{start}\nnew\n{end}\n{scoreboard.START}\nkept\n{scoreboard.END}\n"


def test_chart_is_valid_svg_with_one_mark_per_complete_run_and_panel() -> None:
    svg = scoreboard.render_chart(board().main)

    root = ET.fromstring(svg)
    titles = [el.text for el in root.iter("{http://www.w3.org/2000/svg}title")]
    assert len(titles) == 9  # the chart's own, then 2 models x 2 methods x 2 panels
    assert titles[0] == "Main test set, 24 clauses: counts with 95% bootstrap intervals"
    assert "Claude Haiku 5.5, baseline: 20 of 24" in (titles[1] or "")
    assert svg.count("not run yet") == 8  # 2 Gemini models x 2 methods x 2 panels


def test_restrict_keeps_only_the_named_clauses() -> None:
    run = scoreboard.restrict(pipeline_run(HAIKU), CLAUSES[12:14])

    assert [s.clause.id for s in run.scores] == ["c13", "c14"]
    assert [r.clause_id for r in run.records] == ["c13", "c14", "c14"]
    assert json.loads(run.records[0].text)["verdict"] == "void"


def test_scores_need_the_same_clause_text() -> None:
    edited = [c.model_copy(update={"text": "Changed."}) if c.id == "c01" else c for c in CLAUSES]

    run = scoreboard.restrict(baseline(HAIKU), edited)

    assert "c01" not in {s.clause.id for s in run.scores}
    assert isinstance(run.scores[0], Score)


def test_a_gallery_answer_that_is_right_stops_the_report() -> None:
    right_on_c10 = {k: v for k, v in HAIKU_WRONG.items() if k != "c10"}

    with pytest.raises(scoreboard.ReportDataError, match="c10: a gallery answer must be wrong"):
        scoreboard.render(board({("baseline", HAIKU.key): baseline(HAIKU, right_on_c10)}), "x")


def test_a_selection_run_missing_a_stress_clause_stops_the_report() -> None:
    full = board()
    short = scoreboard.Board(
        full.main, full.stress, scoreboard.restrict(full.selection, CLAUSES[:2])
    )

    with pytest.raises(scoreboard.ReportDataError, match="selection run"):
        scoreboard.render(short, "x")


def test_a_partly_answered_run_says_so() -> None:
    partial = scoreboard.restrict(baseline(OPUS), CLAUSES[:5])
    scored = board({("baseline", OPUS.key): partial}).main

    assert scored.missing("baseline", OPUS) == "partial: 5 of 24 answered"
    assert scored.missing("baseline", model_by_key("gemini-4-argon")) == "not run yet"
    assert scored.missing("baseline", HAIKU) is None


def test_the_collection_dates_come_from_the_records() -> None:
    assert "Answers collected 12 November 2026 (UTC)." in scoreboard.render(board(), "x")
    assert (
        scoreboard._day_span(date(2026, 11, 11), date(2026, 11, 13)) == "11\u201313 November 2026"
    )
    assert (
        scoreboard._day_span(date(2026, 11, 30), date(2026, 12, 1))
        == "30 November 2026 to 1 December 2026"
    )


def guideline_clause(also_accept: list[str]) -> Clause:
    return clause("c29", "allowed").model_copy(
        update={"section": "116", "also_accept": also_accept}
    )


def contested_set(c32_text: str = "a promise argument") -> scoreboard.ScoredSet:
    """c29 (Opus cites s. 120) and c32 (Haiku's baseline calls it void, arguing `c32_text`)."""
    c29, c32 = guideline_clause(["119"]), clause("c32", "allowed")

    def answers(spec: ModelSpec, c29_section: str, c32_verdict: Verdict) -> ModelRun:
        scores = [
            score_answer(c29, Answer("allowed", c29_section), {"14", "116", "120"}),
            score_answer(c32, Answer(c32_verdict, "14"), {"14", "116", "120"}),
        ]
        return ModelRun(spec, [record(spec, "c29", "x"), record(spec, "c32", c32_text)], scores)

    runs: dict[tuple[scoreboard.Method, str], ModelRun] = {
        ("baseline", OPUS.key): answers(OPUS, "120", "allowed"),
        ("baseline", HAIKU.key): answers(HAIKU, "116", "void"),
    }
    return scoreboard.ScoredSet([c29, c32], runs)


CONTESTED = (
    scoreboard.ContestedLabel(
        "c29", "that c29 is void", (), unaccepted_section="120", why_cited="it uses s. 120"
    ),
    scoreboard.ContestedLabel("c32", "that c32 is void", ((HAIKU.key, "promise argument"),)),
)


def test_the_contested_labels_note_lists_wrong_verdicts_and_unaccepted_citations(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(scoreboard, "CONTESTED_LABELS", CONTESTED)

    header, c29_line, c32_line = scoreboard._contested_labels_note(contested_set())

    assert "the frozen labels of c29 and c32: a label is listed here when" in header
    assert "is labelled lawful under s. 116, with s. 119 also accepted." in c29_line
    assert "Wrong verdict in the tables: none." in c29_line
    assert c29_line.endswith("Cited s. 120: Claude Opus 5.5 (baseline).")
    assert "Claude Haiku 5.5's baseline answer argued that c32 is void." in c32_line
    assert c32_line.endswith("Wrong verdict in the tables: Claude Haiku 5.5 (baseline).")
    with pytest.raises(scoreboard.ReportDataError, match="now accepts"):
        scoreboard._contested_labels_note(scoreboard.ScoredSet([guideline_clause(["120"])], {}))


def test_a_contested_label_argument_must_be_in_a_wrong_answer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(scoreboard, "CONTESTED_LABELS", CONTESTED)
    with pytest.raises(scoreboard.ReportDataError, match="not in the answer"):
        scoreboard._contested_labels_note(contested_set("something else"))

    right = (scoreboard.ContestedLabel("c32", "that x", ((OPUS.key, "x"),)),)
    monkeypatch.setattr(scoreboard, "CONTESTED_LABELS", right)
    with pytest.raises(scoreboard.ReportDataError, match="not wrong"):
        scoreboard._contested_labels_note(contested_set())


def test_a_capped_answer_counts_as_a_wrong_verdict_in_the_contested_note(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(scoreboard, "CONTESTED_LABELS", CONTESTED[:1])
    scored = contested_set()
    opus = scored.runs["baseline", OPUS.key]
    opus.scores[0] = score_answer(opus.scores[0].clause, Answer(None, None), {"116"})

    [_, c29_line] = scoreboard._contested_labels_note(scored)

    assert "Wrong verdict in the tables: Claude Opus 5.5 (baseline)." in c29_line


def test_the_headline_is_recounted_without_the_contested_clauses(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    main = board().main
    assert scoreboard._without_contested(main) == []  # c29 and c32 aren't in the test data

    contested = tuple(
        scoreboard.ContestedLabel(i, "that x", ((HAIKU.key, "x"),)) for i in ("c21", "c22")
    )
    monkeypatch.setattr(scoreboard, "CONTESTED_LABELS", contested)
    [_, line] = scoreboard._without_contested(main)

    assert line == (
        "Without the contested clauses (c21 and c22; see Notes), 22 clauses, verdicts right and "
        "exact sections: Claude Haiku 5.5 + pipeline 22 and 19; Claude Opus 5.5 alone 22 and 21."
    )


@pytest.mark.parametrize(
    ("opus_wrong", "disclosed"),
    [
        (
            {"c21": "verdict"},
            "the only ones that Claude Opus 5.5 alone (c21) got wrong or were "
            "unsure on, so that count is perfect by construction.",
        ),
        ({"c03": "verdict"}, None),  # c03 isn't contested
        ({"c21": "verdict", "c03": "verdict"}, None),  # a miss outside the contested clauses
        ({}, None),  # no misses to account for
    ],
)
def test_the_recount_says_when_the_contested_clauses_are_all_of_a_models_misses(
    monkeypatch: pytest.MonkeyPatch,
    opus_wrong: dict[str, Literal["verdict", "section", "both"]],
    disclosed: str | None,
) -> None:
    contested = tuple(
        scoreboard.ContestedLabel(i, "that x", ((HAIKU.key, "x"),)) for i in ("c21", "c22")
    )
    monkeypatch.setattr(scoreboard, "CONTESTED_LABELS", contested)
    main = board({("baseline", OPUS.key): baseline(OPUS, opus_wrong)}).main

    [_, line] = scoreboard._without_contested(main)

    if disclosed is None:
        assert "by construction" not in line
    else:
        assert line.endswith(f"The contested clauses are {disclosed}")


def test_the_recount_names_every_model_its_clauses_account_for(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    contested = tuple(
        scoreboard.ContestedLabel(i, "that x", ((HAIKU.key, "x"),)) for i in ("c21", "c22")
    )
    monkeypatch.setattr(scoreboard, "CONTESTED_LABELS", contested)
    haiku = with_result(pipeline_run(HAIKU), 21, unsure_result(CLAUSES[21], HAIKU, gave_up=False))
    haiku.scores[21] = score_answer(CLAUSES[21], Answer("unsure", None), {"4", "14"})
    runs: dict[tuple[scoreboard.Method, str], ModelRun] = {
        ("pipeline", HAIKU.key): haiku,
        ("baseline", OPUS.key): baseline(OPUS, {"c21": "verdict"}),
    }

    [_, line] = scoreboard._without_contested(board(runs).main)

    assert line.endswith(
        "the only ones that Claude Haiku 5.5 + pipeline (c22) and Claude Opus 5.5 alone (c21) got "
        "wrong or were unsure on, so those counts are perfect by construction."
    )


def test_a_contested_clause_where_light_beats_heavy_points_to_the_note(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pointer = "On c03, see the note on contested labels."
    assert pointer not in scoreboard.render(board(), "chart.svg")

    monkeypatch.setattr(
        scoreboard, "CONTESTED_LABELS", (scoreboard.ContestedLabel("c03", "x", ()),)
    )

    assert pointer in scoreboard._light_right_heavy_wrong(board().main)


def test_the_thinking_note_gives_median_output_and_names_a_model_that_never_thought() -> None:
    flash, argon = model_by_key("gemini-3.8-flash"), model_by_key("gemini-4-argon")

    def thinking(spec: ModelSpec, tokens: list[int]) -> ModelRun:
        run = baseline(spec)
        for i, n in enumerate(tokens * (len(CLAUSES) // len(tokens))):
            run.records[i] = run.records[i].model_copy(
                update={
                    "output_tokens": 100 + n,
                    "raw": {"usage_metadata": {"thoughts_token_count": n}},
                }
            )
        return run

    text = scoreboard.render(
        board(
            {
                ("baseline", flash.key): thinking(flash, [0, 800]),
                ("baseline", argon.key): thinking(argon, [0]),
            }
        ),
        "chart.svg",
    )

    note = next(line for line in text.splitlines() if line.startswith("- **Thinking.**"))
    assert "Claude Haiku 5.5 100; Claude Opus 5.5 100;" in note
    assert "Gemini 3.8 Flash 500 (thinking 400); Gemini 4 Argon 100 (thinking 0)." in note
    assert note.endswith(
        "At its default, Gemini 4 Argon did not think on any main-set baseline answer."
    )


def test_the_report_shows_the_contested_note_and_the_recount(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    contested = tuple(
        scoreboard.ContestedLabel(i, "that x", ((HAIKU.key, "x"),)) for i in ("c21", "c22")
    )
    monkeypatch.setattr(scoreboard, "CONTESTED_LABELS", contested)

    text = scoreboard.render(board(), "chart.svg")

    assert "- **Contested labels.** Answers argue against the frozen labels of c21 and c22:" in text
    assert "Without the contested clauses (c21 and c22; see Notes), 22 clauses" in text


def test_the_scored_set_holds_the_current_models_only(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(scoreboard, "baseline_run", lambda _d, _p, spec, *_: empty(spec))
    monkeypatch.setattr(scoreboard, "pipeline_run", lambda _s, _p, spec, *_: empty(spec))
    prompts = (Mock(), Mock(version="p-v1"))

    scored = scoreboard.score_set(CLAUSES, "0" * 64, Path("unused"), prompts, {})

    assert set(scored.runs) == {(m, spec.key) for m in scoreboard.METHODS for spec in MODELS}
    assert "gemini-4-carbon" not in {spec.key for spec in MODELS}


def test_each_dropped_model_gets_the_one_line_note() -> None:
    text = scoreboard.render(board(), "chart.svg")

    assert "- Gemini 4 Carbon was run first and dropped as a top-tier model; records kept." in text
    assert "Gemini 4 Carbon (heavy)" not in text


@pytest.mark.parametrize("reason", ["max_tokens", "MAX_TOKENS"])
def test_an_answer_stopped_by_the_output_cap_is_named_and_shown_as_no_answer(reason: str) -> None:
    capped = baseline(OPUS, {"c03": "section"})
    capped.scores[2] = score_answer(CLAUSES[2], Answer(None, None), {"4", "14"})
    capped.records[2] = capped.records[2].model_copy(update={"text": "", "finish_reason": reason})

    text = scoreboard.render(board({("baseline", OPUS.key): capped}), "chart.svg")

    assert "c03 (baseline: Claude Opus 5.5 gave no answer; Claude Haiku 5.5 cited s. 14)" in text
    note = next(line for line in text.splitlines() if line.startswith("- **Output cap.**"))
    assert (
        "Claude Opus 5.5, baseline, main set: c03; Claude Opus 5.5, baseline, stress set: c03."
        in note
    )
    headline = table_rows(text, "Claude Opus 5.5 alone")[0]
    assert headline[1].endswith("; 1 gave no verdict")
    main_row = next(r for r in table_rows(text, "Claude Opus 5.5 (heavy)") if r[1] == "baseline")
    assert main_row[2].endswith("; 1 gave no verdict")


def test_a_capped_pipeline_attempt_that_was_retried_is_not_in_the_output_cap_note() -> None:
    run = pipeline_run(HAIKU, exact_misses=3)
    i = next(i for i, r in enumerate(run.results) if r.clause.id == "c14")
    first, retry = run.results[i].attempts
    capped = replace(first, record=first.record.model_copy(update={"finish_reason": "MAX_TOKENS"}))
    run.results[i] = replace(run.results[i], attempts=(capped, retry))
    run.records[run.records.index(first.record)] = capped.record

    assert "Output cap" not in scoreboard.render(board({("pipeline", HAIKU.key): run}), "chart.svg")


def test_without_capped_answers_there_is_no_output_cap_note() -> None:
    assert "Output cap" not in scoreboard.render(board(), "chart.svg")


def test_the_late_fee_note_needs_its_clause_decided_by_section_15() -> None:
    s17 = clause("s17").model_copy(update={"section": "15", "text": "A $50 late charge."})

    [note] = scoreboard._late_fee_note(scoreboard.ScoredSet([s17], {}))

    assert "**s17** (stress set: “A $50 late charge.”) is decided by s. 15's" in note
    assert scoreboard._late_fee_note(scoreboard.ScoredSet(CLAUSES, {})) == []
    with pytest.raises(scoreboard.ReportDataError, match=r"s\. 15"):
        scoreboard._late_fee_note(
            scoreboard.ScoredSet([s17.model_copy(update={"section": "134"})], {})
        )


@pytest.mark.parametrize(
    ("items", "joined"),
    [
        (["c29"], "c29"),
        (["c29", "c32"], "c29 and c32"),
        (["c29", "c31", "c32"], "c29, c31 and c32"),
    ],
)
def test_and_list(items: list[str], joined: str) -> None:
    assert scoreboard._and_list(items) == joined


FLASH, ARGON = model_by_key("gemini-3.8-flash"), model_by_key("gemini-4-argon")


def with_tokens(run: ModelRun, output: int, thinking: int) -> ModelRun:
    """`run` with every record's output and thinking tokens set."""
    records = [
        r.model_copy(
            update={
                "output_tokens": output,
                "raw": {"usage_metadata": {"thoughts_token_count": thinking}},
            }
        )
        for r in run.records
    ]
    return replace(run, records=records)


def closed_book_run(
    spec: ModelSpec, quotes_fail: int = 0, unsure: int = 0, wrong: int = 0, cosmetic: int = 0
) -> ModelRun:
    """Right on every clause with the deciding section; the first `unsure` clauses are unsure,
    the next `quotes_fail` have a quote that fails its check (the first `cosmetic` of them sorted
    as cosmetic, the rest as content words differ), and the last `wrong` get the wrong verdict (with
    a verified quote)."""
    results = []
    for i, c in enumerate(CLAUSES):
        if i < unsure:
            answer = PipelineAnswer(verdict="unsure", section="", quote="", reason="r")
            check: QuoteCheck | None = None
        else:
            right = i < len(CLAUSES) - wrong
            verdict = c.label if right else ("allowed" if c.label == "void" else "void")
            answer = PipelineAnswer(verdict=verdict, section="14", quote="q", reason="r")
            check = "not_found" if i < unsure + quotes_fail else "verified"
        attempt = Attempt(record(spec, c.id, answer.model_dump_json()), answer, check)
        quote = None
        if check is not None:
            category: Category = "verbatim" if check == "verified" else "content_words_differ"
            if check != "verified" and i < unsure + cosmetic:
                category = "cosmetic"
            quote = Classified(category, check, check, None)
        results.append(ClosedBookResult(c, attempt, quote))
    return ModelRun(
        spec,
        [r.attempt.record for r in results],
        [r.score({"4", "14"}) for r in results],
        closed_book=results,
    )


def tldr_closed_book() -> dict[str, ModelRun]:
    """Closed book, Haiku is unsure on 6 of the 24 main-set clauses; Opus and Flash get every
    verdict right with 4 and 7 failed quotes (1 and 3 cosmetic); Argon gets 1 verdict wrong
    (c24)."""
    return {
        HAIKU.key: closed_book_run(HAIKU, unsure=6),
        OPUS.key: closed_book_run(OPUS, quotes_fail=4, cosmetic=1),
        FLASH.key: closed_book_run(FLASH, quotes_fail=7, cosmetic=3),
        ARGON.key: closed_book_run(ARGON, quotes_fail=2, wrong=1),
    }


def tldr_board(
    overrides: dict[tuple[scoreboard.Method, str], ModelRun] | None = None,
) -> scoreboard.Board:
    """Every model run (closed book: `tldr_closed_book`); only Claude Haiku 5.5's pipeline
    retries (c14). Haiku's baseline cites the deciding section on c01
    with the wrong verdict. Flash's answers think: its pipeline answers have 1,000 output tokens,
    800 of them thinking ($0.0045 each, $0.003 of it thinking), and its baseline answers 2,000
    ($0.00825 each), against $0.003 for each of Argon's answers by either method."""
    runs: dict[tuple[scoreboard.Method, str], ModelRun] = {
        ("baseline", HAIKU.key): baseline(HAIKU, {"c01": "verdict"}),
        ("pipeline", OPUS.key): pipeline_run(OPUS, retry_c14=False),
        ("baseline", FLASH.key): with_tokens(baseline(FLASH), 2000, 1900),
        ("pipeline", FLASH.key): with_tokens(pipeline_run(FLASH, retry_c14=False), 1000, 800),
        ("baseline", ARGON.key): baseline(ARGON),
        ("pipeline", ARGON.key): pipeline_run(ARGON, retry_c14=False),
    }
    return replace(board(runs | (overrides or {})), closed_book=tldr_closed_book())


def tldr_lines(b: scoreboard.Board) -> list[str]:
    text = scoreboard.render_tldr(b)
    lines = text.splitlines()
    assert (lines[0], lines[1], lines[-1]) == (
        scoreboard.TLDR_START,
        "**TL;DR**",
        scoreboard.TLDR_END,
    )
    return [line.removeprefix("- ") for line in lines[3:-1]]


def unsure_result(c: Clause, spec: ModelSpec, gave_up: bool) -> Result:
    if gave_up:
        answer = PipelineAnswer(verdict="void", section="14", quote="Not in the Act.", reason="r")
        failed = Attempt(record(spec, c.id, answer.model_dump_json()), answer, "not_found")
        return Result(c, (failed, failed))
    answer = PipelineAnswer(verdict="unsure", section="", quote="", reason="r")
    return Result(c, (Attempt(record(spec, c.id, answer.model_dump_json()), answer, None),))


def with_result(run: ModelRun, index: int, result: Result) -> ModelRun:
    results = list(run.results)
    results[index] = result
    return replace(run, results=results)


def test_the_tldr_states_each_lesson_from_the_saved_answers() -> None:
    lead, checked, headline, statute, section, cost = tldr_lines(tldr_board())

    assert lead == (
        "Closed book (no statute text in the prompt), Claude Opus 5.5 and Gemini 3.8 Flash got "
        "all 24 verdicts right, yet 4 and 7 of their 24 quotes, respectively, failed the "
        "word-for-word check (1 and 3 of them differing only cosmetically or by quoting "
        "conventions). With the statute in the prompt, 95 of 96 first pipeline "
        "answers with a verdict on the main set, from the 4 models, had a quote that passed."
    )
    # 4 models x (24 main + 4 stress clauses); every final quote is verified.
    assert checked.endswith(
        "Of 112 pipeline answers (4 models, 28 clauses), 112 had a checked quote. A checked quote "
        "shows the words are in the Act, not that the section decides the clause: 0 of the 112 "
        "cited a section the label doesn't accept."
    )
    assert headline == (
        "On the 24-clause main set (synthetic clauses; the pipeline is given the text of the rule "
        "sections), Claude Haiku 5.5 with the pipeline got 24 of 24 verdicts right for $0.004; "
        "Claude Opus 5.5 alone got 24 of 24 for $0.14. 24 clauses can't tell them apart."
    )
    assert statute == (
        "For Claude Haiku 5.5, the statute text moved the verdicts, not the quote check: closed "
        "book, it got 18 of 24 verdicts right; with the text, 24. The check failed 1 of 112 "
        "checked first answers (c14: Claude Haiku 5.5 dropped words from the middle of s. 14(2)), "
        "and the retry changed no verdict."
    )
    assert section == (
        "A right section is not a right answer: on c01, Claude Haiku 5.5's baseline cited s. 14, "
        "which voids no-pet provisions, and still called the clause enforceable."
    )
    assert cost == (
        "Cost followed each model's default thinking setting, not its tier: Gemini 3.8 Flash with "
        "the pipeline cost $0.11 on the main set, 67% of it for thinking tokens, and Gemini 4 "
        "Argon alone $0.072. By the same method, too, Gemini 3.8 Flash cost more (baseline $0.20 "
        "vs $0.072; pipeline $0.11 vs $0.072); at its default, Gemini 4 Argon did not think on "
        "any baseline answer."
    )


@pytest.mark.parametrize(
    ("gave_up", "says"),
    [(False, "1 was the model's own `unsure`"), (True, "1 was `unsure` after two failed quotes")],
)
def test_the_tldr_says_why_an_answer_is_unsure(gave_up: bool, says: str) -> None:
    run = with_tokens(pipeline_run(FLASH, retry_c14=False), 1000, 800)
    run = with_result(run, 5, unsure_result(CLAUSES[5], FLASH, gave_up))  # c06: main set only

    _, checked, *_ = tldr_lines(tldr_board({("pipeline", FLASH.key): run}))

    assert f"(4 models, 28 clauses), 111 had a checked quote; {says}. A checked" in checked


def test_the_tldr_counts_checked_quotes_whose_section_the_label_does_not_accept() -> None:
    opus = pipeline_run(OPUS, retry_c14=False)
    c06 = CLAUSES[5]
    opus.scores[5] = score_answer(c06, Answer(c06.label, "99"), {"4", "14", "99"})

    _, checked, *_ = tldr_lines(tldr_board({("pipeline", OPUS.key): opus}))

    assert checked.endswith("1 of the 112 cited a section the label doesn't accept.")


def test_the_tldr_headline_only_calls_a_tie_when_the_counts_match() -> None:
    _, _, headline, *_ = tldr_lines(
        tldr_board({("baseline", OPUS.key): baseline(OPUS, {"c03": "verdict"})})
    )

    assert headline.endswith("Claude Opus 5.5 alone got 23 of 24 for $0.14.")


def retried(c: Clause, spec: ModelSpec, first: PipelineAnswer | None) -> Result:
    """A result whose first answer (`first`, or unreadable if None) failed and whose retry
    verified, with the clause's own verdict."""
    final = PipelineAnswer(verdict=c.label, section="14", quote="q", reason="r")
    text = first.model_dump_json() if first else "not JSON"
    return Result(
        c,
        (
            Attempt(record(spec, c.id, text), first, "not_found" if first else None),
            Attempt(record(spec, c.id, final.model_dump_json()), final, "verified"),
        ),
    )


def test_the_tldr_counts_failed_checks_and_verdicts_the_retry_changed() -> None:
    c06 = CLAUSES[5]  # void; main set only
    wrong_first = PipelineAnswer(verdict="allowed", section="14", quote="q", reason="r")
    opus = with_result(pipeline_run(OPUS, retry_c14=False), 5, retried(c06, OPUS, wrong_first))

    _, _, _, statute, *_ = tldr_lines(tldr_board({("pipeline", OPUS.key): opus}))

    assert statute.endswith(
        "The check failed 2 of 112 checked first answers, and the retry changed 1 verdict."
    )


def test_an_unreadable_first_answer_is_not_a_failed_quote_check() -> None:
    opus = with_result(pipeline_run(OPUS, retry_c14=False), 5, retried(CLAUSES[5], OPUS, None))

    _, _, _, statute, *_ = tldr_lines(tldr_board({("pipeline", OPUS.key): opus}))

    assert "The check failed 1 of 111 checked first answers (c14:" in statute


@pytest.mark.parametrize(
    ("haiku", "error"),
    [
        (None, "Claude Haiku 5.5's closed-book run is not complete"),
        (closed_book_run(HAIKU, quotes_fail=3), "statute text no longer explains"),
        (
            scoreboard.restrict(closed_book_run(HAIKU, unsure=6), CLAUSES[:10]),
            "Claude Haiku 5.5's closed-book run is not complete",
        ),
    ],
)
def test_the_tldr_needs_a_closed_book_run_that_does_worse(
    haiku: ModelRun | None, error: str
) -> None:
    closed_book = {key: run for key, run in tldr_closed_book().items() if key != HAIKU.key}
    if haiku is not None:
        closed_book[HAIKU.key] = haiku

    with pytest.raises(scoreboard.ReportDataError, match=error):
        scoreboard.render_tldr(replace(tldr_board(), closed_book=closed_book))


def test_the_tldr_lead_needs_every_closed_book_run() -> None:
    closed_book = {key: run for key, run in tldr_closed_book().items() if key != ARGON.key}

    with pytest.raises(scoreboard.ReportDataError, match="Gemini 4 Argon's closed-book run"):
        scoreboard.render_tldr(replace(tldr_board(), closed_book=closed_book))


def test_the_tldr_lead_leaves_out_a_model_whose_quotes_all_passed() -> None:
    closed_book = tldr_closed_book() | {
        OPUS.key: closed_book_run(OPUS, quotes_fail=1, wrong=1),  # a wrong verdict
        FLASH.key: closed_book_run(FLASH),  # every verdict right, every quote verified
        ARGON.key: closed_book_run(ARGON, quotes_fail=1),
    }

    lead, *_ = tldr_lines(replace(tldr_board(), closed_book=closed_book))

    assert lead.startswith(
        "Closed book (no statute text in the prompt), Gemini 4 Argon got all 24 verdicts right, "
        "yet 1 of its 24 quotes failed the word-for-word check. With"
    )


@pytest.mark.parametrize(
    "perfect_quotes",
    [
        {OPUS.key: closed_book_run(OPUS), FLASH.key: closed_book_run(FLASH)},
        {OPUS.key: closed_book_run(OPUS, wrong=1), FLASH.key: closed_book_run(FLASH, wrong=1)},
    ],
)
def test_the_tldr_lead_needs_a_right_verdict_with_a_failed_quote(
    perfect_quotes: dict[str, ModelRun],
) -> None:
    closed_book = tldr_closed_book() | perfect_quotes  # Argon has a wrong verdict

    with pytest.raises(scoreboard.ReportDataError, match="right verdicts and failed quotes"):
        scoreboard.render_tldr(replace(tldr_board(), closed_book=closed_book))


def test_the_tldr_needs_its_right_section_answer_to_cite_the_deciding_section() -> None:
    wrong_section = baseline(HAIKU, {"c01": "both"})

    with pytest.raises(scoreboard.ReportDataError, match="not a right section"):
        scoreboard.render_tldr(tldr_board({("baseline", HAIKU.key): wrong_section}))


def test_the_tldr_right_section_answer_needs_a_verdict() -> None:
    no_verdict = baseline(HAIKU, {"c01": "verdict"})
    no_verdict.scores[0] = score_answer(CLAUSES[0], Answer(None, "14"), {"4", "14"})

    with pytest.raises(scoreboard.ReportDataError, match="not a right section"):
        scoreboard.render_tldr(tldr_board({("baseline", HAIKU.key): no_verdict}))


@pytest.mark.parametrize(
    "override",
    [
        ("baseline", ARGON.key, with_tokens(baseline(ARGON), 100, 10)),  # the heavy model thinks
        ("pipeline", FLASH.key, pipeline_run(FLASH, retry_c14=False)),  # light pipeline cheaper
        ("baseline", FLASH.key, baseline(FLASH)),  # light baseline cheaper
    ],
)
def test_the_tldr_stops_when_the_thinking_cost_line_no_longer_holds(
    override: tuple[scoreboard.Method, str, ModelRun],
) -> None:
    method, key, run = override

    with pytest.raises(scoreboard.ReportDataError, match="thinking-cost"):
        scoreboard.render_tldr(tldr_board({(method, key): run}))


def test_the_tldr_needs_every_pipeline_run() -> None:
    with pytest.raises(scoreboard.ReportDataError, match="Gemini 4 Argon pipeline not run yet"):
        scoreboard.render_tldr(tldr_board({("pipeline", ARGON.key): empty(ARGON)}))


def full_board(
    overrides: dict[tuple[scoreboard.Method, str], ModelRun] | None = None,
) -> scoreboard.Board:
    """`tldr_board`'s runs with Haiku's baseline misses (`HAIKU_WRONG`) that the gallery needs,
    and no closed-book run."""
    gallery: dict[tuple[scoreboard.Method, str], ModelRun] = {
        ("baseline", HAIKU.key): baseline(HAIKU, HAIKU_WRONG)
    }
    return replace(tldr_board(gallery | (overrides or {})), closed_book={})


def test_the_closed_book_table_counts_verified_quotes_among_answers_with_a_verdict() -> None:
    b = replace(
        full_board(), closed_book={HAIKU.key: closed_book_run(HAIKU, quotes_fail=5, unsure=3)}
    )

    text = scoreboard.render(b, "chart.svg")

    [haiku] = table_rows(text, "Claude Haiku 5.5 (light) | 21 of 24")
    assert haiku[2:] == ["3", "16 of 21", f"21 of 24 (18{scoreboard.DASH}24)", "$0.004"]
    [opus] = table_rows(text, "Claude Opus 5.5 (heavy) | not run yet")
    assert opus[1] == "not run yet"
    # Four pipelines: 24 first answers each, all with a verdict; only Haiku's c14 failed.
    assert "the statute in the prompt, 95 of 96 first pipeline answers with a verdict" in text


def test_the_closed_book_comparison_needs_every_main_set_pipeline_run() -> None:
    b = replace(board(), closed_book={HAIKU.key: closed_book_run(HAIKU)})  # Gemini not run

    text = scoreboard.render(b, "chart.svg")

    assert "| Claude Haiku 5.5 (light) | 24 of 24" in text
    assert "With the statute in the prompt" not in text


def test_a_partial_closed_book_run_says_so() -> None:
    partial = scoreboard.restrict(closed_book_run(HAIKU), CLAUSES[:10])
    partial = replace(partial, closed_book=closed_book_run(HAIKU).closed_book[:10])

    text = scoreboard.render(replace(board(), closed_book={HAIKU.key: partial}), "chart.svg")

    assert "| Claude Haiku 5.5 (light) | partial: 10 of 24 answered |" in text


def test_the_closed_book_comparison_leaves_out_unsure_pipeline_answers() -> None:
    # Only Haiku's c14 first answer fails (see `tldr_board`); Opus is unsure on c06.
    opus = pipeline_run(OPUS, retry_c14=False)
    opus = with_result(opus, 5, unsure_result(CLAUSES[5], OPUS, gave_up=False))
    b = full_board({("pipeline", OPUS.key): opus})

    text = scoreboard.render(b, "chart.svg")

    assert "the statute in the prompt, 94 of 95 first pipeline answers with a verdict" in text


def test_the_closed_book_section_sorts_the_failed_quotes_once_every_model_has_run() -> None:
    text = scoreboard.render(replace(full_board(), closed_book=tldr_closed_book()), "chart.svg")

    assert "The categories have not yet been checked by hand." in text
    assert "#### How the quotes that failed the check differ from the Act" in text
    assert "(results/test-v1/quote-review.md)" in text
    [opus] = table_rows(text, "Claude Opus 5.5 | 24 | 4")
    assert opus[1:] == ["24", "4", "0", "1", "0", "3", "0"]
    [haiku] = table_rows(text, "Claude Haiku 5.5 | 18 | 0")
    assert haiku[1:] == ["18", "0", "0", "0", "0", "0", "0"]

    partial = tldr_closed_book()
    del partial[ARGON.key]
    text = scoreboard.render(replace(full_board(), closed_book=partial), "chart.svg")
    assert "How the quotes that failed the check" not in text


def test_passing_quotes_that_stop_early_are_counted_as_an_upper_limit() -> None:
    assert "rough rule in code" not in scoreboard._closed_book_table(tldr_board())
    # Every quote is word for word in its section: the pipeline's, and "q" closed book. s. 14
    # goes on with a list, s. 4 doesn't.
    quote = "A provision in a tenancy agreement prohibiting animals is void."
    sections = {
        "14": Section("14", "Pets", f"{quote} q\n(a) Widgets too.", "BK14"),
        "4": Section("4", "Other", quote, "BK4"),
    }
    b = replace(tldr_board(), sections=sections)

    text = scoreboard._closed_book_table(b)

    # Haiku's first pipeline answers: c14 fails and is left out, and 3 cite s. 4, where the
    # quote ends cleanly; the other models' 24 each pass and are flagged.
    assert (
        "flags 92 of the 95 passing first pipeline quotes and 77 of the 77 passing closed-book "
        "quotes as possibly stopping early" in text
    )


def hand_check_record(rounds: tuple[int, ...] = (1, 2)) -> handcheck.Record:
    """Four rows; round 1 agrees with its suggestions on none, round 2 on one, and on two of
    round 2's other three the final answer is the reviewer's."""
    same, other = (
        handcheck.Judgement("cosmetic", "no", ""),
        handcheck.Judgement("wrong_cite", "", ""),
    )
    suggested = dict.fromkeys("abcd", same)
    reviewed = {"a": same, "b": other, "c": other, "d": other}
    final = {"a": same, "b": other, "c": other, "d": same}
    return handcheck.Record(
        {"round1": suggested, "round2": suggested, "final": final},
        {n: reviewed if n > 1 else dict.fromkeys("abcd", other) for n in rounds},
    )


def test_the_hand_check_method_comes_from_its_record() -> None:
    runs = list(tldr_closed_book().values())
    folder = f"results/{runs[0].records[0].prompt_version}/{handcheck.RECORD_DIR}"

    text = scoreboard._failed_quotes(runs, {}, hand_check_record())

    assert (
        "Under the four rules, a second blind round agreed with the suggestions on 1 of 4 rows; "
        "on 2 of the other 3 the reviewer's answer was adopted. I approved the result." in text
    )
    assert f"({folder}/{handcheck.RULES})" in text
    assert f"({folder}/{handcheck.MANIFEST})" in text
    assert f"I approved the result. ([The rules]({folder}/{handcheck.RULES})" in text
    assert (
        f"[the full record]({folder}/{handcheck.MANIFEST}).) Not reviewed by a lawyer. "
        f"{handcheck.RECHECK_NOTE} " in text
    )
    assert "I worked four rows by hand with Claude" not in scoreboard._failed_quotes(runs, {})


def test_the_hand_check_method_describes_two_rounds() -> None:
    runs = list(tldr_closed_book().values())

    with pytest.raises(scoreboard.ReportDataError, match="rounds \\[1, 2, 3\\]"):
        scoreboard._failed_quotes(runs, {}, hand_check_record((1, 2, 3)))


@pytest.mark.parametrize(
    ("markdown", "relinked"),
    [
        ("[a](results/x.md) and ![b](demo/c.gif)", "[a](../results/x.md) and ![b](../demo/c.gif)"),
        ("[a](#notes), [b](https://e.org/x), [c](../README.md)", None),  # left as they are
        ("[two\nlines](src/a.py#L1)", "[two\nlines](../src/a.py#L1)"),
    ],
)
def test_relink_prefixes_links_to_repo_files_only(markdown: str, relinked: str | None) -> None:
    assert scoreboard.relink(markdown, "../") == (relinked or markdown)


def short_board(misleads: str = "yes") -> scoreboard.Board:
    """`tldr_board`, with Argon's pipeline wrong on one clause, Flash's pipeline unsure on c01,
    Opus's closed-book c05 quote lined up with s. 14, and every failed closed-book quote
    hand-checked: all change what the Act says, and two (Opus c01 and c05) would mislead."""
    flash = tldr_board().main.run("pipeline", FLASH)
    unsure = unsure_result(CLAUSES[0], FLASH, gave_up=False)
    flash = replace(
        with_result(flash, 0, unsure), scores=[unsure.score({"4", "14"}), *flash.scores[1:]]
    )
    b = tldr_board(
        {("pipeline", ARGON.key): closed_book_run(ARGON, wrong=1), ("pipeline", FLASH.key): flash}
    )
    act = "A provision in a tenancy agreement prohibiting widgets is void."
    section = Section("14", "Pets", act, "BK14")
    opus = b.closed_book[OPUS.key]
    quote = classify(act.replace("widgets", "gadgets"), "14", {"14": section})
    results = [replace(r, quote=quote) if r.clause.id == "c05" else r for r in opus.closed_book]
    closed_book = b.closed_book | {OPUS.key: replace(opus, closed_book=results)}
    runs = list(closed_book.values())
    checks = {
        key: quotereview.HandCheck(
            "agree", "yes", misleads if key == (OPUS.key, "c01") else "no", ""
        )
        for key in quotereview.failed_categories(runs)
    }
    checks[OPUS.key, "c05"] = quotereview.HandCheck("agree", "yes", "yes", "Widgets, not gadgets.")
    return replace(
        b, closed_book=closed_book, quote_checks=checks, hand_check_record=hand_check_record()
    )


EXHIBIT = exhibits.Exhibit(
    model=OPUS.key,
    clause="c05",
    label="Swapped the thing",
    punchline="Verdict: void, right. Read alone, it bans gadgets.",
)


def test_the_short_version_gives_the_exhibit_a_table_and_the_hand_check() -> None:
    text = scoreboard.render_short(short_board(), "NOTICE", (EXHIBIT,))
    # First pipeline quotes: Argon's run stands in from the closed book, with no pipeline
    # attempts; Haiku's first c14 quote fails.

    lines = text.splitlines()
    assert (lines[0], lines[-1]) == (scoreboard.SHORT_START, scoreboard.SHORT_END)
    assert lines[1] == (
        "**Claude Opus 5.5 on clause c05, with no statute text in the prompt. Verdict: void, "
        "right. Read alone, it bans gadgets.**"
    )
    assert lines[3] == (
        "> A provision in a tenancy agreement prohibiting ~~widgets~~ **gadgets** is void."
    )
    assert lines[5].startswith("Redline of s. 14 against the quote, from a run with no statute")
    assert lines[5].endswith("\u201d NOTICE")
    table = lines.index("| " + " | ".join(scoreboard.SHORT_COLUMNS) + " |")
    assert lines[table - 2].startswith("**Four models, 24 synthetic lease clauses**")
    assert lines[table + 2 : table + 6] == [
        "| Claude Haiku 5.5 | 24 of 24 | 23 of 24 | 18 of 24 (6 unsure) | **18 of 18** |",
        "| Claude Opus 5.5 | 24 of 24 | 24 of 24 | 24 of 24 | **20 of 24** |",
        "| Gemini 3.8 Flash | 23 of 24 (1 unsure) | 23 of 23 | 24 of 24 | **17 of 24** |",
        "| Gemini 4 Argon | 23 of 24 | 0 of 0 | 23 of 24 | **22 of 24** |",
    ]
    assert lines[table + 7] == scoreboard.SHORT_TABLE_NOTE
    assert lines[-2] == (
        "**The 14 closed-book quotes that failed,** from 3 of the 4 models: 14 state a real "
        "provision of the Act; 14 of those change the rule, and 2 of those 14 would mislead "
        "about the clause they were quoted for. Claude drafted the hand-check labels, Claude Opus "
        "reviewed them blind in two rounds (1 of 4 agreed independently), and the author approved "
        "them; not reviewed by a lawyer. " + handcheck.RECHECK_NOTE
    )


@pytest.mark.parametrize(
    ("change", "error"),
    [
        ({"hand_check_record": None}, "no record of how it was made"),
        ({"clause": "c99"}, "exhibit claude-opus-5-5 c99: no saved closed-book answer"),
        ({"model": "nobody"}, "exhibit nobody c05: no saved closed-book answer"),
        ({"_verdict_right": lambda score: False}, "c05: the verdict is not right"),
        ({"meaning_changed": "no"}, "c05: the hand check must find"),
        ({"misleads_here": "no"}, "c05: the hand check must find"),
        ({"punchline": "Verdict: allowed, right. No."}, "must start 'Verdict: void, right.'"),
        ({"punchline": "Verdict: void. No."}, "must start 'Verdict: void, right.'"),
        ({"tag": exhibits.Tag(item="(z)", note="n")}, "c05: s. 14 has 0 items (z)"),
    ],
)
def test_the_short_version_refuses_an_exhibit_the_answers_do_not_support(
    monkeypatch: pytest.MonkeyPatch, change: dict[str, object], error: str
) -> None:
    board = short_board()
    exhibit = EXHIBIT.model_copy(
        update={k: v for k, v in change.items() if k in exhibits.Exhibit.model_fields}
    )
    if "hand_check_record" in change:
        board = replace(board, hand_check_record=None)
    key = (OPUS.key, "c05")
    for name in ("meaning_changed", "misleads_here"):
        if name in change:
            check = replace(board.quote_checks[key], **{name: str(change[name])})
            board = replace(board, quote_checks=board.quote_checks | {key: check})
    if "_verdict_right" in change:
        monkeypatch.setattr(scoreboard, "_verdict_right", change["_verdict_right"])

    with pytest.raises(scoreboard.ReportDataError, match=re.escape(error)):
        scoreboard.render_short(board, "NOTICE", (exhibit,))


def test_the_short_version_needs_a_finished_hand_check() -> None:
    with pytest.raises(scoreboard.ReportDataError, match="hand check is not finished"):
        scoreboard.render_short(short_board(misleads=""), "NOTICE", (EXHIBIT,))
