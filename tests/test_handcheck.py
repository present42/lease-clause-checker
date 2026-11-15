import csv
import json
from pathlib import Path

import pytest

from lease_clause_checker import handcheck, quotereview
from lease_clause_checker.handcheck import Judgement, RecordError, agrees, load_record
from lease_clause_checker.quotediff import Category

RECORD = (
    Path(__file__).parents[1] / "results" / "closedbook-v1+conventions-v1" / handcheck.RECORD_DIR
)


@pytest.mark.parametrize(
    ("first", "second", "agree"),
    [
        (Judgement("cosmetic", "yes", "no"), Judgement("cosmetic", "yes", "no"), True),
        (Judgement("cosmetic", "yes", "no"), Judgement("wrong_cite", "yes", "no"), False),
        (Judgement("cosmetic", "yes", "no"), Judgement("cosmetic", "no", ""), False),
        (Judgement("cosmetic", "yes", "no"), Judgement("cosmetic", "yes", "unsure"), False),
        (Judgement("cosmetic", "no", ""), Judgement("cosmetic", "no", "yes"), True),
    ],
)
def test_two_answers_agree_on_category_meaning_and_where_both_change_it_misleading(
    first: Judgement, second: Judgement, agree: bool
) -> None:
    assert agrees(first, second) is agree


def entry(row: str, category: str, meaning: str = "", misleads: str = "") -> dict[str, str]:
    return {
        "id": row,
        "category": category,
        handcheck.MEANING: meaning,
        handcheck.MISLEADS: misleads,
        "reason": "r",
    }


def write_record(folder: Path, rounds: dict[str, tuple[int, list[dict[str, str]]]]) -> Path:
    """Three rows; round 2's suggestions differ from its reviewer on b and c, and the final
    takes the reviewer's answer on b only."""
    folder.mkdir()
    (folder / handcheck.MANIFEST).write_text("record")
    stages = {
        "round1": [
            entry("m__a", "cosmetic", "no"),
            entry("m__b", "too_short"),
            entry("m__c", "cosmetic"),
        ],
        "round2": [
            entry("m__a", "cosmetic", "no"),
            entry("m__b", "too_short"),
            entry("m__c", "cosmetic"),
        ],
        "final": [
            entry("m__a", "cosmetic", "no"),
            entry("m__b", "cosmetic", "yes", "yes"),
            entry("m__c", "cosmetic"),
        ],
    }
    for stage, entries in stages.items():
        path = folder / f"claude_suggestions_{stage}.json"
        path.write_text(json.dumps({"suggestions": entries}))
    for name, (number, answers) in rounds.items():
        rows = f"rows_{name}.json"
        (folder / rows).write_text(json.dumps([{"id": answer["id"]} for answer in answers]))
        data = {
            "round": f"round{number}",
            "rows": len(answers),
            "input_file": rows,
            "answers": answers,
        }
        (folder / f"round{number}_{name}.json").write_text(json.dumps(data))
    return folder


ROUNDS = {
    # "yes" for a too_short quote reads as blank: it states no rule.
    "reviewer-1a": (1, [entry("m__a", "cosmetic", "no"), entry("m__b", "too_short", "yes")]),
    "reviewer-1b": (1, [entry("m__c", "wrong_cite", "no")]),
    "reviewer-2a": (2, [entry("m__a", "cosmetic", "no"), entry("m__b", "cosmetic", "yes", "yes")]),
    "reviewer-2b": (2, [entry("m__c", "wrong_cite", "no")]),
}


def test_agreement_and_adopted_answers_are_counted_per_round(tmp_path: Path) -> None:
    record = load_record(write_record(tmp_path / "r", ROUNDS))

    assert record is not None
    assert (record.rows, record.agreed(1), record.agreed(2), record.adopted(2)) == (3, 2, 1, 1)


def test_no_manifest_is_no_record(tmp_path: Path) -> None:
    assert load_record(tmp_path) is None


@pytest.mark.parametrize(
    ("rounds", "error"),
    [
        (ROUNDS | {"reviewer-1b": (1, [entry("m__a", "cosmetic", "no")])}, "judged twice"),
        (ROUNDS | {"reviewer-2b": (2, [])}, "round 2 does not cover the same rows"),
        (ROUNDS | {"reviewer-1b": (1, [entry("m__c", "x"), entry("m__c", "x")])}, "appears twice"),
    ],
)
def test_an_incomplete_record_is_an_error(
    tmp_path: Path, rounds: dict[str, tuple[int, list[dict[str, str]]]], error: str
) -> None:
    folder = write_record(tmp_path / "r", rounds)

    with pytest.raises(RecordError, match=error):
        load_record(folder)


def test_a_reviewer_file_must_hold_as_many_answers_as_it_says(tmp_path: Path) -> None:
    folder = write_record(tmp_path / "r", ROUNDS)
    path = folder / "round2_reviewer-2b.json"
    path.write_text(json.dumps(json.loads(path.read_text()) | {"rows": 5}))

    with pytest.raises(RecordError, match="1 answers, but rows says 5"):
        load_record(folder)


CATEGORIES: dict[tuple[str, str], Category] = {
    ("m", "a"): "cosmetic",
    ("m", "b"): "too_short",
    ("m", "c"): "cosmetic",
}
FINAL: quotereview.HandChecks = {
    ("m", "a"): quotereview.HandCheck("agree", "no", "", ""),
    ("m", "b"): quotereview.HandCheck("cosmetic", "yes", "yes", "note"),
    ("m", "c"): quotereview.HandCheck("agree", "", "", ""),
}


def test_a_reviewer_file_must_name_its_round_and_answer_the_rows_it_was_given(
    tmp_path: Path,
) -> None:
    folder = write_record(tmp_path / "r", ROUNDS)
    path = folder / "round2_reviewer-2b.json"
    path.rename(folder / "round1_reviewer-2b.json")
    with pytest.raises(RecordError, match="its round is 'round2'"):
        load_record(folder)

    folder = write_record(tmp_path / "s", ROUNDS)
    (folder / "rows_reviewer-2b.json").write_text(json.dumps([{"id": "m__d"}]))
    with pytest.raises(RecordError, match=r"not the rows in rows_reviewer-2b\.json"):
        load_record(folder)


def test_a_missing_file_is_a_record_error(tmp_path: Path) -> None:
    folder = write_record(tmp_path / "r", ROUNDS)
    (folder / "claude_suggestions_round2.json").unlink()

    with pytest.raises(RecordError, match="FileNotFoundError"):
        load_record(folder)


def test_the_final_suggestions_must_be_the_hand_checks(tmp_path: Path) -> None:
    record = load_record(write_record(tmp_path / "r", ROUNDS))
    assert record is not None
    record.check_against(FINAL, CATEGORIES)

    changed = FINAL | {("m", "b"): quotereview.HandCheck("cosmetic", "yes", "no", "")}
    with pytest.raises(RecordError, match="m b: the hand check is not the final suggestion"):
        record.check_against(changed, CATEGORIES)
    with pytest.raises(RecordError, match="not the quotes that failed"):
        record.check_against(FINAL, CATEGORIES | {("m", "d"): "cosmetic"})
    missing = {key: check for key, check in FINAL.items() if key != ("m", "c")}
    with pytest.raises(RecordError, match="m c: the hand check is not the final suggestion"):
        record.check_against(missing, CATEGORIES)


def test_the_saved_record_gives_the_published_agreement() -> None:
    record = load_record(RECORD)
    assert record is not None
    assert (record.rows, record.agreed(1), record.agreed(2), record.adopted(2)) == (59, 35, 46, 12)

    path = RECORD.parent / quotereview.CHECKS_FILE
    with path.open(encoding="utf-8", newline="") as file:
        categories = {
            (row["model"], row["clause"]): row["category"] for row in csv.DictReader(file)
        }
    record.check_against(quotereview.load_checks(path), categories)  # type: ignore[arg-type]
    assert [a.row for a in record.amendments] == [
        "claude-opus-5-5__c26",
        "claude-opus-5-5__c23",
    ]


def test_a_meaning_written_for_a_quote_that_states_no_rule_is_read_as_blank() -> None:
    record = handcheck.Record({"final": {"m__a": Judgement("too_short", "", "")}}, {})

    record.check_against(
        {("m", "a"): quotereview.HandCheck("agree", "yes", "", "")}, {("m", "a"): "too_short"}
    )


def amend(row: str, before: str, after: str) -> dict[str, str]:
    return {"row": row, "before": before, "after": after, "by": "a", "date": "d", "why": "w"}


def test_an_amended_note_must_read_as_its_last_amendment_left_it(tmp_path: Path) -> None:
    folder = write_record(tmp_path / "r", ROUNDS)
    amendments = [amend("m__b", "old", "draft"), amend("m__b", "draft", "note")]
    (folder / handcheck.AMENDMENTS).write_text(json.dumps({"amendments": amendments}))
    record = load_record(folder)
    assert record is not None
    record.check_against(FINAL, CATEGORIES)

    reverted = FINAL | {("m", "b"): quotereview.HandCheck("cosmetic", "yes", "yes", "draft")}
    with pytest.raises(RecordError, match="m b: the note is not as its last amendment left it"):
        record.check_against(reverted, CATEGORIES)


@pytest.mark.parametrize(
    ("amendments", "error"),
    [
        ([amend("m__x", "a", "b")], r"rows not in the record: \['m__x'\]"),
        ([amend("m__b", "same", "same")], "m__b: the amendment changes nothing"),
        (
            [amend("m__b", "old", "draft"), amend("m__b", "old", "note")],
            "m__b: does not start from the last amendment's note",
        ),
    ],
)
def test_a_malformed_amendment_is_a_record_error(
    tmp_path: Path, amendments: list[dict[str, str]], error: str
) -> None:
    folder = write_record(tmp_path / "r", ROUNDS)
    (folder / handcheck.AMENDMENTS).write_text(json.dumps({"amendments": amendments}))

    with pytest.raises(RecordError, match=error):
        load_record(folder)
