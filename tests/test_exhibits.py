from pathlib import Path

import pytest

from lease_clause_checker import exhibits, quotediff
from lease_clause_checker.exhibits import ExhibitError, Tag
from lease_clause_checker.statute import Section

# Synthetic wording, laid out as the Act lays out a subsection with a list.
VISITS = Section(
    "26",
    "Visits",
    "(1) A landlord may visit a unit at any hour without notice,\n"
    "(a) in an emergency; or\n"
    "(b) if the tenant agrees at the time.\n"
    "(2) A tenant\u2019s guest may stay a week.",
    "BK32",
)
STOPS_EARLY = "A landlord may visit the unit at any hour without notice"


def replayed(quote: str, tag: Tag | None = None) -> tuple[quotediff.Alignment, exhibits.Replay]:
    alignment = quotediff.align(quote, VISITS)
    assert alignment is not None
    return alignment, exhibits.replay(alignment, tag)


def texts(tokens: list[quotediff.Token] | tuple[quotediff.Token, ...]) -> list[str]:
    return [token.text for token in tokens]


def test_a_replay_starts_from_the_act_and_ends_at_the_quote_token_for_token() -> None:
    alignment, replay = replayed(STOPS_EARLY)

    assert alignment.subsection == "(1)"
    assert texts(replay.start(alignment)) == texts(alignment.act[3:])  # all but "(1)"
    assert replay.act == (3, len(alignment.act))
    assert texts(replay.end(alignment)) == texts(quotediff.tokens(STOPS_EARLY))


def test_a_dropped_list_is_dropped_one_item_at_a_time_in_reading_order() -> None:
    alignment, replay = replayed(STOPS_EARLY)

    assert [edit.said for edit in replay.edits] == [
        "changed \u2018a\u2019 to \u2018the\u2019",
        "dropped: \u2018(a) in an emergency; or\u2019",
        "dropped: \u2018(b) if the tenant agrees at the time.\u2019",
    ]
    # The comma before "(a)" is too short to drop on its own, so it goes with "(a)".
    first_drop = replay.edits[1].drop
    assert first_drop is not None
    assert alignment.act[first_drop[0]].text == ","
    assert [part[0] for part in replay.box] == ["same", "edit", "same", "edit", "edit"]


def test_words_the_quote_adds_are_an_edit_of_their_own() -> None:
    quote = "A landlord may only visit a unit at any hour without notice, (a) in an emergency"
    _, replay = replayed(quote)

    added = replay.edits[0]
    assert (added.drop, added.said) == (None, "added: \u2018only\u2019")
    assert added.add is not None


def test_a_tag_marks_one_list_item_from_its_marker_to_the_end() -> None:
    alignment, replay = replayed(STOPS_EARLY, Tag(item="(b)", note="the item that applies"))

    assert replay.tag is not None
    assert quotediff.join(alignment.act[replay.tag[0] : replay.tag[1]]) == (
        "(b) if the tenant agrees at the time."
    )


def test_a_tag_must_name_an_item_of_the_subsection() -> None:
    with pytest.raises(ExhibitError, match=r"s\. 26\(1\) has 0 items \(c\)"):
        replayed(STOPS_EARLY, Tag(item="(c)", note="none"))


def test_a_kept_word_written_otherwise_than_in_the_quote_stops_the_replay() -> None:
    # The quote's straight apostrophe matches the Act's curly one, but would not be the quote.
    with pytest.raises(ExhibitError, match="written otherwise"):
        replayed("A tenant's guest may stay a week.")


def write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "exhibits.yaml"
    path.write_text(text, encoding="utf-8")
    return path


ONE = "- model: m\n  clause: c01\n  label: L\n  punchline: 'Verdict: void, right. P.'\n"


def test_exhibits_load_in_order(tmp_path: Path) -> None:
    second = ONE.replace("c01", "c02") + "  tag:\n    item: (b)\n    note: N\n"

    loaded = exhibits.load(write(tmp_path, ONE + second))

    assert [(e.clause, e.tag) for e in loaded] == [
        ("c01", None),
        ("c02", Tag(item="(b)", note="N")),
    ]


@pytest.mark.parametrize(
    ("text", "error"),
    [
        ("[]\n", "no exhibits"),
        (ONE + ONE, "listed twice"),
        (ONE + "  colour: red\n", "colour"),
        (ONE.replace("  label: L\n", ""), "label"),
        ("- [\n", "exhibits.yaml"),
    ],
)
def test_a_malformed_exhibit_file_is_refused(tmp_path: Path, text: str, error: str) -> None:
    with pytest.raises(ExhibitError, match=error):
        exhibits.load(write(tmp_path, text))


def test_the_committed_exhibits_are_the_three_chosen_answers() -> None:
    loaded = exhibits.load()

    assert [(e.model, e.clause, e.label) for e in loaded] == [
        ("gemini-3.8-flash", "c09", "Dropped the limits"),
        ("claude-opus-5-5", "c26", "Quoted the wrong rule"),
        ("claude-haiku-5-5", "c15", "Left out who's covered"),
    ]
