"""The closed-book answers the explorer's opening view replays and the README leads with,
configured in `exhibits.yaml` (`load`), and each one's quote as a replay of edits to the Act.

`replay` turns a quote's alignment with its subsection (`quotediff.Alignment.runs`, the same
redline every view shows) into edits in reading order: each drops a stretch of the Act's words,
adds the quote's own, or changes the one into the other. Played on a copy of the subsection,
they end at the quote, token for token. Which answers may be shown is checked against the
scores and the hand check by `scoreboard.shown_exhibits`.
"""

from dataclasses import dataclass
from itertools import pairwise
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, TypeAdapter, ValidationError

from lease_clause_checker import quotediff
from lease_clause_checker.quotediff import Alignment, Token

FILE = Path(__file__).with_name("exhibits.yaml")
MIN_PIECE_WORDS = 2
"""A dropped stretch is split before each list item it holds; a piece before the first item
with fewer words than this (", " or "; or") joins that item."""


class ExhibitError(ValueError):
    """The configuration is malformed, or a replay can't be built from the alignment."""


class Tag(BaseModel):
    """A note on one list item of the Act's subsection, such as the item the clause is about."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    item: str
    """The item's marker as the Act has it, such as "(b)"."""
    note: str


class Exhibit(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    model: str
    """A model key, as in `models.MODELS`."""
    clause: str
    label: str
    """A few words on what the quote does, shown above it."""
    punchline: str
    """What the quote does, in the owner's words, starting "Verdict: <verdict>, right. "."""
    tag: Tag | None = None
    note: str = ""
    """One line about the exhibit, shown under it; none if empty."""


def load(path: Path = FILE) -> tuple[Exhibit, ...]:
    """The exhibits in `path`, in order; `ExhibitError` if there are none, one is malformed, or
    one answer is listed twice."""
    try:
        exhibits = TypeAdapter(list[Exhibit]).validate_python(
            yaml.safe_load(path.read_text(encoding="utf-8"))
        )
    except (OSError, yaml.YAMLError, ValidationError) as error:
        raise ExhibitError(f"{path}: {error}") from error
    if not exhibits:
        raise ExhibitError(f"{path}: no exhibits")
    keys = [(e.model, e.clause) for e in exhibits]
    if len(keys) != len(set(keys)):
        raise ExhibitError(f"{path}: an answer is listed twice")
    return tuple(exhibits)


@dataclass(frozen=True)
class Edit:
    """One step of a replay: the Act's tokens it drops and the quote's tokens it adds, each a
    [start, end) range or None, and what a screen reader says about it."""

    drop: tuple[int, int] | None
    add: tuple[int, int] | None
    said: str


@dataclass(frozen=True)
class Replay:
    act: tuple[int, int]
    """The range of the subsection's tokens both boxes start from: all of it but its own
    marker ("(1)"), as the redline shows it."""
    box: tuple[tuple[str, int, int], ...]
    """The quote box in reading order: ("same", start, end) for the Act's tokens it keeps, and
    ("edit", k, k + 1) where edit k happens."""
    edits: tuple[Edit, ...]
    tag: tuple[int, int] | None
    """The tagged item's range of the subsection's tokens, marker included."""

    def start(self, alignment: Alignment) -> list[Token]:
        """The quote box before any edit: the Act's tokens in `act`."""
        return self._tokens(alignment, done=False)

    def end(self, alignment: Alignment) -> list[Token]:
        """The quote box after every edit."""
        return self._tokens(alignment, done=True)

    def _tokens(self, alignment: Alignment, *, done: bool) -> list[Token]:
        tokens: list[Token] = []
        for kind, start, end in self.box:
            if kind == "same":
                tokens += alignment.act[start:end]
                continue
            edit = self.edits[start]
            source, part = (alignment.quote, edit.add) if done else (alignment.act, edit.drop)
            if part is not None:
                tokens += source[part[0] : part[1]]
        return tokens


def replay(alignment: Alignment, tag: Tag | None = None) -> Replay:
    """The edits that turn the subsection (`Replay.act`) into the quote, from the alignment's
    whole-subsection runs. `ExhibitError` unless the runs cover the subsection without a gap,
    the kept words are written as the quote writes them, the result is the quote token for
    token, and a `tag` names exactly one list item."""
    runs = alignment.runs(whole=True)
    kept = [(start, end) for kind, start, end in runs if kind != "ins"]
    if not kept or any(a[1] != b[0] for a, b in pairwise(kept)):
        raise ExhibitError("the redline does not cover the subsection in one stretch")
    box: list[tuple[str, int, int]] = []
    edits: list[Edit] = []

    def edit(drop: tuple[int, int] | None, add: tuple[int, int] | None) -> None:
        box.append(("edit", len(edits), len(edits) + 1))
        edits.append(Edit(drop, add, _said(alignment, drop, add)))

    i = 0
    while i < len(runs):
        kind, start, end = runs[i]
        if kind == "same":
            box.append(runs[i])
        elif kind == "ins":
            edit(None, (start, end))
        else:
            pieces = _pieces(alignment.act, start, end)
            followed = i + 1 < len(runs) and runs[i + 1][0] == "ins"
            for piece in pieces[:-1] if followed else pieces:
                edit(piece, None)
            if followed:
                edit(pieces[-1], runs[i + 1][1:])
                i += 1
        i += 1
    result = Replay((kept[0][0], kept[-1][1]), tuple(box), tuple(edits), _tagged(alignment, tag))
    act = alignment.act[result.act[0] : result.act[1]]
    if result.start(alignment) != list(act):
        raise ExhibitError("the replay does not start from the Act's text")
    if [t.text for t in result.end(alignment)] != [t.text for t in alignment.quote]:
        raise ExhibitError("the replay does not end at the quote: a kept word is written otherwise")
    return result


def _pieces(act: tuple[Token, ...], start: int, end: int) -> list[tuple[int, int]]:
    """The dropped stretch act[start:end], split before each list item it holds. Only the
    first piece can start no item; if it is short (", " before "(a)"), it joins the next."""
    bounds = [start, *(j for j in range(start + 1, end) if quotediff.item_marker_size(act, j))]
    pieces = list(pairwise([*bounds, end]))
    first = act[pieces[0][0] : pieces[0][1]]
    if len(pieces) > 1 and _words(first) < MIN_PIECE_WORDS:
        pieces[:2] = [(pieces[0][0], pieces[1][1])]
    return pieces


def _tagged(alignment: Alignment, tag: Tag | None) -> tuple[int, int] | None:
    """The range of the list item `tag` names, from its marker to the next item's marker or
    the subsection's end."""
    if tag is None:
        return None
    act = alignment.act
    starts = [j for j in range(len(act)) if quotediff.item_marker_size(act, j)]
    wanted = tag.item.replace(" ", "")
    found = [
        j
        for j in starts
        if "".join(t.text for t in act[j : j + quotediff.item_marker_size(act, j)]) == wanted
    ]
    if len(found) != 1:
        raise ExhibitError(
            f"s. {alignment.section}{alignment.subsection} has {len(found)} items {tag.item}"
        )
    later = [j for j in starts if j > found[0]]
    return found[0], later[0] if later else len(act)


def _said(alignment: Alignment, drop: tuple[int, int] | None, add: tuple[int, int] | None) -> str:
    old = _quoted(alignment.act, drop)
    new = _quoted(alignment.quote, add)
    if drop and add:
        return f"changed {old} to {new}"
    return f"dropped: {old}" if drop else f"added: {new}"


def _quoted(tokens: tuple[Token, ...], part: tuple[int, int] | None) -> str:
    """A stretch's words in single quotation marks, without punctuation at either end."""
    if part is None:
        return ""
    words = tokens[part[0] : part[1]]
    edges = {",", ";", ":"}
    while len(words) > 1 and words[0].key in edges:
        words = words[1:]
    while len(words) > 1 and words[-1].key in edges:
        words = words[:-1]
    return f"\u2018{quotediff.join(words)}\u2019"


def _words(tokens: tuple[Token, ...]) -> int:
    return sum(token.key[0].isalnum() for token in tokens)
