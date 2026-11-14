import json
import xml.etree.ElementTree as ET
from dataclasses import replace
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Literal
from unittest.mock import Mock

import pytest

from lease_clause_checker import scoreboard
from lease_clause_checker.clauses import Clause
from lease_clause_checker.models import MODELS, ModelSpec, model_by_key
from lease_clause_checker.pipeline import Attempt, PipelineAnswer, Result
from lease_clause_checker.report import ModelRun
from lease_clause_checker.runs import RunRecord
from lease_clause_checker.scoring import Answer, Score, Verdict, score_answer
from lease_clause_checker.stats import difference_interval

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


def pipeline_run(spec: ModelSpec, exact_misses: int = 0) -> ModelRun:
    scores, records, results = [], [], []
    for i, c in enumerate(CLAUSES):
        section = "4" if i < exact_misses and c.label == "void" else "14"
        quote = "A provision in a tenancy agreement prohibiting animals is void."
        answer = PipelineAnswer(verdict=c.label, section=section, quote=quote, reason="r")
        attempts: tuple[Attempt, ...] = (
            Attempt(record(spec, c.id, answer.model_dump_json()), answer, "verified"),
        )
        if c.id == "c14":
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

    assert "the frozen labels of c29 and c32." in header
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
        "Without the contested clauses (c21, c22; see Notes), 22 clauses, verdicts right and "
        "exact sections: Claude Haiku 5.5 + pipeline 22 and 19; Claude Opus 5.5 alone 22 and 21."
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

    assert "- **Contested labels.** Answers argue against the frozen labels of c21 and c22." in text
    assert "Without the contested clauses (c21, c22; see Notes), 22 clauses" in text


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
