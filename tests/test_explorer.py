import re
from dataclasses import replace
from datetime import UTC, datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

import pytest

from lease_clause_checker import exhibits, explorer, handcheck, quotediff, scoreboard
from lease_clause_checker.clauses import Clause
from lease_clause_checker.closedbook import ClosedBookResult
from lease_clause_checker.elaws import CachedAct
from lease_clause_checker.handcheck import Judgement, Record
from lease_clause_checker.models import MODELS
from lease_clause_checker.pipeline import Attempt, PipelineAnswer, Result, assess
from lease_clause_checker.quotediff import classify
from lease_clause_checker.quotereview import HandCheck
from lease_clause_checker.report import ModelRun
from lease_clause_checker.runs import RunRecord
from lease_clause_checker.scoreboard import Board, Method, ScoredSet
from lease_clause_checker.statute import Section

HAIKU = MODELS[0]
S14 = Section(
    "14",
    "“No pet” provisions void",
    "A provision in a tenancy agreement prohibiting the presence of animals in or about the "
    "residential complex is void.",
    "BK19",
)
S27 = Section(
    "27",
    "Entry with notice",
    "(1) A landlord may enter a rental unit in accordance with written notice given to the "
    "tenant’s door at least 24 hours before the time of entry,\n"
    "(a) to carry out a repair; or\n"
    "(b) to show the unit.\n"
    "(2) The written notice under subsection (1) shall specify the reasons for entry, the day "
    "of entry and a time of entry between the hours of 8 a.m. and 8 p.m.",
    "BK33",
)
S99 = Section("99", "Not cited", "(1) Words that no answer quotes or cites.", "BK99")
SECTIONS = {s.number: s for s in (S14, S27, S99)}
ACT = CachedAct(
    source_url="https://example.org/act",
    api_url="https://example.org/api",
    retrieved_at=datetime(2026, 11, 8, tzinfo=UTC),
    title="Residential Tenancies Act, 2006",
    consolidation="current",
    content_sha256="a" * 64,
    content_html="",
)
PROMPTS = {"baseline": "b-v1", "pipeline": "p-v1", "closedbook": "c-v1"}


def clause(clause_id: str, section: str = "14") -> Clause:
    return Clause(
        id=clause_id,
        text=f"Clause {clause_id}.",
        label="void",
        kind="clear",
        section=section,
        also_accept=[],
        rationale="Why.",
        quote="the decisive words",
        source="test",
        reviewed=True,
    )


def record(clause_id: str, text: str) -> RunRecord:
    return RunRecord(
        clause_id=clause_id,
        model_key=HAIKU.key,
        model_id=HAIKU.model_id,
        served_model=HAIKU.model_id,
        prompt_version="c-v1",
        prompt_sha256="0" * 64,
        clauses_sha256="0" * 64,
        request={},
        requested_at=datetime(2026, 11, 12, tzinfo=UTC),
        text=text,
        finish_reason="end_turn",
        input_tokens=10,
        output_tokens=10,
        raw={},
    )


def attempt(clause_id: str, section: str, quote: str) -> Attempt:
    answer = PipelineAnswer(verdict="void", section=section, quote=quote, reason="r")
    return assess(record(clause_id, answer.model_dump_json()), SECTIONS)


C01, C02 = clause("c01"), clause("c02", "27")
VERIFIED_14 = "prohibiting the presence of animals in or about the residential complex is void"
STOPS_EARLY_27 = "A landlord may enter a rental unit in accordance with written notice"
CHANGED_27 = "The written notice shall specify the reason for entry and the day of entry"


def closed_book(clause_: Clause, cited: str, quote: str) -> ClosedBookResult:
    return ClosedBookResult(
        clause_, attempt(clause_.id, cited, quote), classify(quote, cited, SECTIONS)
    )


def mini_board() -> Board:
    """Every model's pipeline: c01 verified from s. 14; c02 a failed quote, then one verified
    from s. 27(1) that stops before its list. Haiku closed book: c01 a changed quote from
    s. 27(2), hand-checked as misleading; c02 s. 14's words, citing s. 27, which shares no
    4 words in a row with them. No baseline answers."""
    pipeline = [
        Result(C01, (attempt("c01", "14", VERIFIED_14),)),
        Result(
            C02,
            (
                attempt("c02", "27", "a landlord may walk in whenever"),
                attempt("c02", "27", STOPS_EARLY_27),
            ),
        ),
    ]
    closed = [closed_book(C01, "27", CHANGED_27), closed_book(C02, "27", VERIFIED_14)]
    acts = set(SECTIONS)
    runs: dict[tuple[Method, str], ModelRun] = {}
    for spec in MODELS:
        runs["baseline", spec.key] = ModelRun(spec, [], [])
        runs["pipeline", spec.key] = ModelRun(
            spec,
            [a.record for r in pipeline for a in r.attempts],
            [r.score(acts) for r in pipeline],
            pipeline,
        )
    stress = ScoredSet([], {key: ModelRun(run.spec, [], []) for key, run in runs.items()})
    closed_runs = {
        spec.key: ModelRun(
            spec, [r.attempt.record for r in closed], [r.score(acts) for r in closed], [], closed
        )
        for spec in MODELS
    }
    checks = {}
    for spec in MODELS:
        misleads = "yes" if spec == HAIKU else "no"
        checks[spec.key, "c01"] = HandCheck("agree", "yes", misleads, "A narrower rule.")
        checks[spec.key, "c02"] = HandCheck("content_words_differ", "no", "", "")
    return Board(ScoredSet([C01, C02], runs), stress, ModelRun(HAIKU, [], []), closed_runs, checks)


EXHIBIT = exhibits.Exhibit(
    model=HAIKU.key,
    clause="c01",
    label="Narrowed it",
    punchline="Verdict: void, right. It is narrower.",
    note="Caught in round 2.",
)


def mini_data(board: Board | None = None) -> dict[str, Any]:
    return explorer.build(
        board or mini_board(), SECTIONS, ACT, PROMPTS, 18, "results/c-v1", (EXHIBIT,)
    )


def redline(data: dict[str, Any], alignment: dict[str, Any]) -> list[tuple[str, str]]:
    """The redline as the page builds it: (text, kind) for each token, "same" and "del" from the
    exported subsection, "ins" from the quote."""
    section = data["act"]["sections"][alignment["section"]]
    act = section["subsections"][alignment["subsection"]]
    tokens = []
    for kind, start, end in alignment["redline"]:
        source = alignment["quote"] if kind == "ins" else act
        tokens += [(source[i][0], kind) for i in range(start, end)]
    return tokens


def answer(
    data: dict[str, Any], method: str, clause_id: str, model: str = HAIKU.key
) -> dict[str, Any]:
    answers: list[dict[str, Any]] = data["answers"]
    [found] = [
        a for a in answers if (a["method"], a["clause"], a["model"]) == (method, clause_id, model)
    ]
    return found


EXPECTED = {
    **{("main", "pipeline", spec.key): (2, 1) for spec in MODELS},
    **{("main", "closedbook", spec.key): (2, 0) for spec in MODELS},
}


def test_the_export_holds_only_the_subsections_that_quotes_line_up_with() -> None:
    sections = mini_data()["act"]["sections"]

    assert {n: list(s["subsections"]) for n, s in sections.items()} == {
        "14": [""],
        "27": ["(1)", "(2)"],
    }
    assert sections["27"]["url"].endswith("#BK33")


def test_the_act_keeps_its_own_characters_and_line_breaks() -> None:
    part = mini_data()["act"]["sections"]["27"]["subsections"]["(1)"]

    assert ["’", ""] in part  # the curly apostrophe, joined to the word before it
    assert [text for text, before in part if before == "\n"] == ["(", "(", "("]  # line starts


def test_a_verified_quote_lines_up_in_full_and_says_whether_it_stops_early() -> None:
    data = mini_data()
    whole = answer(data, "pipeline", "c01")["attempts"][0]
    early = answer(data, "pipeline", "c02")["attempts"][1]

    assert (whole["check"], whole["stops_early"]) == ("verified", False)
    assert (early["check"], early["stops_early"]) == ("verified", True)
    alignment = early["alignment"]
    assert alignment["matched_words"] == alignment["quote_words"] == 12
    assert all(same for *_, same in alignment["quote"])


def test_a_changed_quote_marks_its_own_words() -> None:
    data = mini_data()
    [only] = answer(data, "closedbook", "c01")["attempts"]

    assert only["alignment"]["subsection"] == "(2)"
    assert [text for text, kind in redline(data, only["alignment"]) if kind == "ins"] == [
        "reason",
        "and",
    ]
    assert only["category"] == "content words differ"


def test_a_closed_book_quote_carries_its_hand_check() -> None:
    data = mini_data()
    [changed] = answer(data, "closedbook", "c01")["attempts"]
    [cited] = answer(data, "closedbook", "c02")["attempts"]

    assert changed["hand_check"] == {
        "code_category": "content words differ",
        "category": "content words differ",
        "states_a_rule": "yes",
        "meaning_changed": "yes",
        "misleads_here": "yes",
        "note": "A narrower rule.",
    }
    assert (cited["hand_check"]["code_category"], cited["hand_check"]["category"]) == (
        "wrong section cited",
        "content words differ",
    )


def test_a_failed_quote_names_the_other_sections_that_hold_it_word_for_word() -> None:
    data = mini_data()
    [cited] = answer(data, "closedbook", "c02")["attempts"]
    first, retry = answer(data, "pipeline", "c02")["attempts"]

    assert (cited["check"], cited["found_in"]) == ("not_found", ["14"])
    # A wrong citation is set against the section its words come from, not the one cited.
    assert (cited["section"], cited["alignment"]["section"]) == ("27", "14")
    assert "ins" not in {kind for _, kind in redline(data, cited["alignment"])}  # same words
    assert first["found_in"] == []
    assert "found_in" not in retry


def test_a_short_quote_is_not_looked_up_in_other_sections() -> None:
    board = mini_board()
    run = board.closed_book[HAIKU.key]
    short = closed_book(C02, "27", "residential complex is void")
    board = replace(
        board,
        closed_book=board.closed_book
        | {HAIKU.key: replace(run, closed_book=[run.closed_book[0], short])},
        quote_checks=board.quote_checks | {(HAIKU.key, "c02"): HandCheck("agree", "", "", "")},
    )

    [only] = answer(mini_data(board), "closedbook", "c02")["attempts"]

    assert only["found_in"] == []


def test_a_quote_found_but_too_short_is_not_looked_up_and_states_no_rule() -> None:
    board = mini_board()
    run = board.closed_book[HAIKU.key]
    short = closed_book(C02, "27", "the day of entry")  # in s. 27(2), but 4 words
    board = replace(
        board,
        closed_book=board.closed_book
        | {HAIKU.key: replace(run, closed_book=[run.closed_book[0], short])},
        quote_checks=board.quote_checks | {(HAIKU.key, "c02"): HandCheck("agree", "", "", "")},
    )

    [only] = answer(mini_data(board), "closedbook", "c02")["attempts"]

    assert only["check"] == "too_short"
    assert "found_in" not in only  # only a quote not found is looked up elsewhere
    assert only["hand_check"]["states_a_rule"] == "no"


def test_the_story_holds_the_short_versions_numbers_and_answer() -> None:
    story = mini_data()["story"]

    haiku = story["rows"][0]
    assert (haiku["model"], haiku["with_right"], haiku["with_unsure"]) == (HAIKU.name, 2, 0)
    assert (haiku["without_right"], haiku["without_verified"], haiku["without_with_verdict"]) == (
        2,
        0,
        2,
    )
    assert [row["model"] for row in story["rows"]] == [spec.name for spec in MODELS]
    assert story["table_title"] == "Four models, 2 synthetic lease clauses"
    assert story["column_groups"] == [
        ["With the statute in the prompt", ["Verdicts right", "Quotes word for word"]],
        ["Without it (closed book)", ["Verdicts right", "Quotes word for word"]],
    ]
    assert story["table_note"] == scoreboard.SHORT_TABLE_NOTE
    assert story["failed_from"] == scoreboard.failed_from(
        [scoreboard.ShortRow(**row) for row in story["rows"]]
    )
    first = mini_data()["pipeline_first_checks"]
    assert sum(row["with_verified"] for row in story["rows"]) == first["verified"]
    assert sum(row["with_checked"] for row in story["rows"]) == first["checked"]
    assert story["hand_check"] == {
        "quotes": 8,
        "rules": 8,
        "changes": 4,
        "misleads": 1,
        "unsure": 0,
    }
    # The one misleading quote (the exhibit's) came with a right closed-book verdict.
    assert story["base_rate"] == {
        "right": sum(row["without_right"] for row in story["rows"]),
        "misleading": 1,
        "judged": 8,
        "means": scoreboard.MISLEADS_MEANS,
    }
    [example] = story["exhibits"]
    keys = ("model", "clause", "label", "punchline", "note", "verdict", "reason")
    assert {k: example[k] for k in keys} == {
        "model": HAIKU.key,
        "clause": "c01",
        "label": "Narrowed it",
        "punchline": "Verdict: void, right. It is narrower.",
        "note": "Caught in round 2.",
        "verdict": "void",
        "reason": "r",  # the saved answer's own reason
    }
    assert example["alignment"]["section"] == "27"
    assert example["alignment"]["subsection"] == "(2)"
    tokens = redline(mini_data(), example["alignment"])
    assert tokens[0] == ("The", "same")
    assert [text for text, kind in tokens if kind == "ins"] == ["reason", "and"]
    last_kept = max(i for i, (_, kind) in enumerate(tokens) if kind != "del")
    assert " ".join(text for text, _ in tokens[last_kept + 1 :]) == (
        "and a time of entry between the hours of 8 a . m . and 8 p . m ."
    )


def played(data: dict[str, Any], exhibit: dict[str, Any], *, done: bool) -> list[str]:
    """The quote box as the page plays it: before any edit, or after every one."""
    alignment, replay = exhibit["alignment"], exhibit["replay"]
    act = data["act"]["sections"][alignment["section"]]["subsections"][alignment["subsection"]]
    shown: list[str] = []
    for kind, start, end in replay["box"]:
        if kind == "same":
            shown += [token[0] for token in act[start:end]]
            continue
        edit = replay["edits"][start]
        source, part = (alignment["quote"], edit["add"]) if done else (act, edit["drop"])
        if part:
            shown += [token[0] for token in source[part[0] : part[1]]]
    return shown


def test_an_exhibit_replays_from_the_act_to_the_saved_quote_token_for_token() -> None:
    data = mini_data()
    [exhibit] = data["story"]["exhibits"]
    act = data["act"]["sections"]["27"]["subsections"]["(2)"]
    start, end = exhibit["replay"]["act"]

    assert played(data, exhibit, done=False) == [token[0] for token in act[start:end]]
    assert played(data, exhibit, done=True) == [t.text for t in quotediff.tokens(CHANGED_27)]
    assert [edit["said"] for edit in exhibit["replay"]["edits"]] == [
        "dropped: \u2018under subsection (1)\u2019",
        "changed \u2018reasons\u2019 to \u2018reason\u2019",
        "changed \u2018,\u2019 to \u2018and\u2019",
        "dropped: \u2018and a time of entry between the hours of 8 a.m. and 8 p.m.\u2019",
    ]
    assert (exhibit["replay"]["tag"], exhibit["replay"]["tag_note"]) == (None, None)


def test_the_page_settings_and_the_pipeline_count_come_from_the_data() -> None:
    data = mini_data()

    assert data["settings"] == {
        "repository": explorer.REPOSITORY,
        "pages": explorer.PAGES,
        "rule_sections": 18,
        "min_run": 4,
        "min_quote_words": 5,
    }
    assert data["pipeline_first_checks"] == {"verified": 4, "checked": 8}
    assert data["hand_check"] is None  # no record on the board
    assert data["dropped_models"] == ["Gemini 4 Carbon"]


def test_an_export_needs_a_closed_book_run() -> None:
    with pytest.raises(explorer.SiteExportError, match="no closed-book run"):
        mini_data(replace(mini_board(), closed_book={}))


def test_a_verified_quote_that_does_not_line_up_stops_the_export() -> None:
    board = mini_board()
    # A two-word quote is in s. 14 but too short to align; pretend the check passed it.
    too_short = attempt("c01", "14", "is void")
    verified = Attempt(too_short.record, too_short.answer, "verified")
    run = board.main.runs["pipeline", HAIKU.key]
    run.results[0] = Result(C01, (verified,))

    with pytest.raises(explorer.SiteExportError, match="must line up in full"):
        mini_data(board)


def test_the_count_check_passes_when_every_saved_answer_and_retry_is_exported() -> None:
    explorer.check_counts(mini_data(), EXPECTED)


@pytest.mark.parametrize(
    ("change", "error"),
    [
        (
            lambda d: d.update(answers=d["answers"][1:]),
            "1 answers and 1 retries exported, 2 and 1 saved",
        ),
        (
            lambda d: d["answers"][1]["attempts"].pop(0),
            "2 answers and 0 retries exported, 2 and 1 saved",
        ),
        (
            lambda d: d["act"]["sections"].update({"99": {"subsections": {"(1)": [["W", ""]]}}}),
            r"s\. 99\(1\): exported but no quote",
        ),
        (
            lambda d: d["act"]["sections"]["14"].update(subsections={}),
            r"s\. 14: used but not exported",
        ),
    ],
)
def test_the_count_check_catches_a_missing_answer_or_statute_text_no_quote_uses(
    change: Any, error: str
) -> None:
    data = mini_data()
    change(data)

    with pytest.raises(explorer.SiteExportError, match=error):
        explorer.check_counts(data, EXPECTED)


def test_the_count_check_catches_an_answer_from_a_run_not_expected() -> None:
    with pytest.raises(explorer.SiteExportError, match="exported but not expected"):
        explorer.check_counts(mini_data(), {("main", "pipeline", HAIKU.key): (2, 1)})


def test_saved_counts_come_from_the_files_on_disk(tmp_path: Path) -> None:
    folder = tmp_path / "p-v1"
    (folder / HAIKU.key).mkdir(parents=True)
    for name in ("c01", "c01.retry", "c03"):
        (folder / HAIKU.key / f"{name}.json").write_text("{}")

    counts = explorer.saved_counts({("main", "pipeline"): folder}, {"main": [C01, C02]})

    assert counts["main", "pipeline", HAIKU.key] == (1, 1)
    assert counts["main", "pipeline", MODELS[1].key] == (0, 0)


def test_the_hand_check_links_to_its_record_and_csv() -> None:
    same = {"x": Judgement("cosmetic", "no", "")}
    record = Record({"round1": same, "round2": same, "final": same}, {1: same, 2: same})

    hand = mini_data(replace(mini_board(), hand_check_record=record))["hand_check"]

    assert hand["method"].startswith("I worked four rows by hand with Claude")
    assert hand["labels"] == handcheck.label_note(record)
    assert hand["recheck"] == handcheck.RECHECK_NOTE
    assert hand["record"] == f"{explorer.REPOSITORY}/tree/main/results/c-v1/hand-check"
    assert hand["csv"] == f"{explorer.REPOSITORY}/blob/main/results/c-v1/quote-hand-check.csv"


def test_the_hand_rows_must_agree_with_the_story() -> None:
    data = mini_data()
    counts = data["story"]["hand_check"] | {"misleads": 2}

    with pytest.raises(explorer.SiteExportError, match="1 misleading; the story counts 8 and 2"):
        explorer._check_hand_rows(data["answers"], counts)


def test_an_export_whose_hand_rows_disagree_with_the_story_stops() -> None:
    board = mini_board()
    run = board.closed_book[HAIKU.key]
    # The quote now passes, so the story no longer counts it, but its hand check is still there.
    passed = closed_book(C02, "14", VERIFIED_14)
    board = replace(
        board,
        closed_book=board.closed_book
        | {HAIKU.key: replace(run, closed_book=[run.closed_book[0], passed])},
    )

    with pytest.raises(explorer.SiteExportError, match="8 hand-checked rows"):
        mini_data(board)


def test_a_clause_id_in_both_sets_stops_the_export() -> None:
    board = mini_board()
    board = replace(board, stress=replace(board.stress, clauses=[C01]))

    with pytest.raises(explorer.SiteExportError, match="in both sets"):
        mini_data(board)


def test_write_replaces_an_earlier_export_and_names_the_repository(tmp_path: Path) -> None:
    site = tmp_path / "site"
    data: dict[str, Any] = {
        "settings": {"repository": "https://example.org/repo", "pages": "https://example.org/p/"},
        "answers": [1],
        "models": [{"key": "m", "name": "Model & Co"}],
        "story": {
            "exhibits": [{"model": "m", "alignment": {"section": "26", "subsection": "(1)"}}]
        },
    }
    explorer.write(data, site)
    (site / "old-asset.js").write_text("stale")
    explorer.write(data | {"answers": []}, site)

    names = sorted(p.name for p in site.iterdir())
    assert names == [
        ".lcc-site",
        "data.json",
        "explorer.css",
        "explorer.js",
        "fonts",
        "index.html",
        "og.png",
    ]
    assert '"answers":[]' in (site / "data.json").read_text()
    page = (site / "index.html").read_text()
    assert explorer.REPOSITORY_SLOT not in page
    assert explorer.PAGES_SLOT not in page
    assert 'href="https://example.org/repo"' in page
    assert 'content="https://example.org/p/og.png"' in page
    assert explorer.OG_ALT_SLOT not in page
    assert 'content="Model &amp; Co&#x27;s closed-book quote of s. 26(1) of the Residential' in page


class FetchedByPage(HTMLParser):
    """The URLs a page's markup makes the browser fetch."""

    def __init__(self) -> None:
        super().__init__()
        self.urls: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        wanted = {"link": "href", "script": "src", "img": "src"}.get(tag)
        found = dict(attrs).get(wanted) if wanted else None
        if found:
            self.urls.append(found)


def test_the_page_loads_nothing_from_another_site_and_every_file_it_loads_is_exported(
    tmp_path: Path,
) -> None:
    site = tmp_path / "site"
    data: dict[str, Any] = {
        "settings": {"repository": "https://example.org/repo", "pages": "https://example.org/p/"},
        "models": [{"key": "m", "name": "M"}],
        "story": {
            "exhibits": [{"model": "m", "alignment": {"section": "26", "subsection": "(1)"}}]
        },
    }
    explorer.write(data, site)
    # What the browser fetches: every <link>, <script>, <img> and the stylesheet's url()s and
    # @imports, and what the script fetch()es; not links a reader follows or the og: tags.
    fetched = FetchedByPage()
    fetched.feed((site / "index.html").read_text())
    css = (site / "explorer.css").read_text()
    fonts = re.findall(r"url\(\s*['\"]?([^'\")\s]+)", css)
    script = (site / "explorer.js").read_text()
    fetches = re.findall(r"fetch\(\s*['\"`]([^'\"`]+)", script)
    assert "@import" not in css
    assert len(fonts) == 8
    assert fetches == ["data.json"]
    assert "fonts/SourceSerif4-Latin.woff2" in fetched.urls
    for path in [*fetched.urls, *fonts]:
        if path.startswith("data:"):
            continue
        assert "//" not in path, path
        assert (site / path).is_file(), path
    for licence in ("OFL-SourceSerif4.txt", "OFL-IBMPlex.txt"):
        assert "SIL Open Font License" in (site / "fonts" / licence).read_text()


def test_write_refuses_a_folder_with_no_earlier_export(tmp_path: Path) -> None:
    (tmp_path / "notes.txt").write_text("mine")

    with pytest.raises(explorer.SiteExportError, match="holds no earlier export"):
        explorer.write({"settings": {"repository": "r", "pages": "p"}, "answers": []}, tmp_path)
    assert (tmp_path / "notes.txt").exists()


def test_every_redline_is_the_quote_against_its_whole_subsection() -> None:
    data = mini_data()
    alignments = [
        (attempt, attempt["alignment"])
        for a in data["answers"]
        for attempt in a.get("attempts", [])
        if attempt.get("alignment")
    ]

    assert alignments
    for attempt, exported in alignments:
        section = SECTIONS[exported["section"]]
        python = quotediff.align(attempt["quote"], section)
        assert python is not None
        expected = [(t.text, kind) for kind, tokens in python.pieces(whole=True) for t in tokens]
        assert redline(data, exported) == expected
