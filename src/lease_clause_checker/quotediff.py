"""Line a quote up with the Act word by word, and sort a quote by how it differs from the Act.

The categories, each tried in this order:

- `no_quote`: the answer gave none.
- `verbatim`: word for word in the cited section (`verify.check_quote`).
- `drops_negation`: word for word, or but for cosmetic differences, in the cited section, but
  only right after a negating word that it leaves out ("landlord shall …" from "No landlord
  shall …"; `verify.negation_before`).
- `heading`: found only in a heading or headnote of the cited section (`verify.check_quote`).
- `too_short`: under `verify.MIN_QUOTE_WORDS` words, found or not: too short to show anything.
- `conventions`: word for word once the usual marks of a quotation are read as marks (an
  ellipsis for an omission, cosmetic changes in brackets, a changed first capital;
  `verify.check_quote_with_conventions`).
- `cosmetic`: lines up with a subsection of the cited section, and differs from it only in
  articles, punctuation, case, grammatical number or the Act's paragraph markers
  (`wording.same_but_cosmetic`).
- `wrong_cite`: not a version of the cited section, but of another one: word for word (with
  or without quoting conventions) or cosmetically different there, or lined up there with
  content words changed (`Classified.words_changed`). A quote that is word for word or
  cosmetic in another section is a wrong cite even if it also lines up, with changes, with the
  cited one.
- `content_words_differ`: lines up with a subsection of the cited section, but a content word
  is changed, added or dropped. Whether that changes what the provision means is for a reader
  (`quotereview` keeps that judgement as part of the hand check).
- `not_in_act`: lines up with no subsection of the Act: paraphrased or invented.

"Lines up" is `Alignment.lined_up`. The classification describes wording only: a cosmetic
difference is not a wrong statement of the law, and a word-for-word quote can still come from a
section that doesn't decide the clause.
"""

import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Literal

from lease_clause_checker import verify, wording
from lease_clause_checker.statute import Section

Category = Literal[
    "no_quote",
    "verbatim",
    "drops_negation",
    "heading",
    "too_short",
    "conventions",
    "cosmetic",
    "wrong_cite",
    "content_words_differ",
    "not_in_act",
]
LABELS: dict[Category, str] = {
    "verbatim": "verbatim",
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
"""Each category as a reader sees it."""
# A word or one punctuation mark, so "void." matches "void". An apostrophe is a token of its own,
# as it is a word boundary to `verify` ("tenant" is a whole word in "tenant’s").
TOKEN = re.compile(r"\w+|[^\w\s]")
# A subsection qualifies for an alignment only if the quote shares a run of this many tokens
# with it; shorter runs ("of the tenant") are common to many provisions.
MIN_RUN = 4
# A short match far from the rest (a lone "if" 100 words on) is chance, not part of the quote:
# an opening or closing match shorter than `MIN_RUN` is dropped when the Act's gap to the next
# match is more than this many tokens longer than the quote's.
STRAY_GAP = 10
# A quote is a version of a subsection only if at least this share of its words line up, in
# order, and they make up at least `SPAN_SHARE` of the Act's words from the first to the last;
# fewer, and the words are scattered over the subsection (see `Alignment.lined_up`).
LINED_UP_SHARE = 0.5
SPAN_SHARE = 0.25
# A paragraph marker starting a line of the Act, as tokens joined by spaces: "( a )", "( 1 . 1 )",
# or a numbered or roman paragraph "1 ." / "ii .".
PARAGRAPH_MARKER = re.compile(r"(?:\( \w+(?: \. \w+)? \)|(?:\d+|[ivx]+) \.)(?= |$)")


@dataclass(frozen=True)
class Token:
    text: str
    """As written, so the Act's tokens keep its curly apostrophes and quotation marks."""
    key: str
    """What is compared: `text` with its quotation marks made straight (`verify.normalise`)."""
    before: str
    """What comes before it: "\\n" at the start of the text or after a line break, " " after
    other whitespace, "" if it follows the previous token directly."""


@dataclass(frozen=True)
class Change:
    """One place where the quote and the Act differ: the Act's tokens there and the quote's."""

    act: tuple[str, ...]
    quote: tuple[str, ...]

    @property
    def cosmetic(self) -> bool:
        return wording.same_but_cosmetic(self.act, self.quote)

    @property
    def critical(self) -> tuple[str, ...]:
        """The `wording.CRITICAL_WORDS` on one side only (in any case)."""
        act = {word.lower() for word in self.act}
        quote = {word.lower() for word in self.quote}
        return tuple(sorted((act ^ quote) & wording.CRITICAL_WORDS))


@dataclass(frozen=True)
class Region:
    """A stretch of the alignment: matched tokens (`same`), or the Act's and the quote's tokens
    where they differ. The quote's unmatched opening and closing tokens are set against as many
    of the Act's just before or after the match, the words they most likely replace ("a" for
    "No" in "a landlord shall"); against none if the quote's are punctuation."""

    act: tuple[Token, ...]
    quote: tuple[Token, ...]
    same: bool

    @property
    def change(self) -> Change:
        """The difference, leaving out the Act's paragraph markers at the start of a line:
        they are layout, but a marker inside a line ("subsection (1)") is a word."""
        return Change(
            tuple(t.key for t in _without_paragraph_markers(self.act)),
            tuple(t.key for t in self.quote),
        )


@dataclass(frozen=True)
class Alignment:
    """A quote lined up with one subsection of one section."""

    section: str
    subsection: str
    """The subsection's marker, such as "(2)"; "" if the section has no numbered subsections."""
    act: tuple[Token, ...]
    """The subsection's tokens."""
    quote: tuple[Token, ...]
    blocks: tuple[tuple[int, int, int], ...]
    """Matching runs, in order: (start in `act`, start in `quote`, length)."""

    @property
    def regions(self) -> list[Region]:
        first_a, first_b, _ = self.blocks[0]
        last_a, last_b, last_size = self.blocks[-1]
        regions = []
        if first_b:
            lead = self.quote[:first_b]
            act = self.act[max(0, first_a - first_b) : first_a] if _has_word(lead) else ()
            regions.append(Region(act, lead, same=False))
        for i, (a, b, size) in enumerate(self.blocks):
            regions.append(Region(self.act[a : a + size], self.quote[b : b + size], same=True))
            if i + 1 < len(self.blocks):
                next_a, next_b, _ = self.blocks[i + 1]
                gap = Region(self.act[a + size : next_a], self.quote[b + size : next_b], False)
                if gap.act or gap.quote:
                    regions.append(gap)
        end_a, end_b = last_a + last_size, last_b + last_size
        if end_b < len(self.quote):
            tail = self.quote[end_b:]
            act = self.act[end_a : end_a + len(tail)] if _has_word(tail) else ()
            regions.append(Region(act, tail, same=False))
        return regions

    @property
    def changes(self) -> tuple[Change, ...]:
        """Every difference, from the quote's first token to its last."""
        return tuple(region.change for region in self.regions if not region.same)

    @property
    def matched_words(self) -> int:
        return sum(_is_word(t.key) for _, b, size in self.blocks for t in self.quote[b : b + size])

    @property
    def quote_words(self) -> int:
        return sum(_is_word(token.key) for token in self.quote)

    @property
    def span_words(self) -> int:
        """The Act's words from the first matched token to the last."""
        start, (end_a, _, end_size) = self.blocks[0][0], self.blocks[-1]
        return sum(_is_word(token.key) for token in self.act[start : end_a + end_size])

    @property
    def lined_up(self) -> bool:
        """Enough of the quote lines up, close enough together, to call it a version of this
        subsection."""
        return (
            self.matched_words >= LINED_UP_SHARE * self.quote_words
            and self.matched_words >= SPAN_SHARE * self.span_words
        )

    @property
    def cosmetic(self) -> bool:
        return all(change.cosmetic for change in self.changes)

    @property
    def dropped_negation(self) -> str | None:
        """The negating word right before where the quote starts in the Act, which the quote
        leaves out (`verify.negation_before`). The quote starts at its first matched token, or
        as many tokens before it as the quote has unmatched opening tokens, if those differ
        from the Act's only cosmetically ("Landlord" for "landlord"). None otherwise."""
        first_a, first_b, _ = self.blocks[0]
        start = first_a - first_b
        if start < 0:
            return None
        if first_b:
            lead = Change(
                tuple(t.key for t in self.act[start:first_a]),
                tuple(t.key for t in self.quote[:first_b]),
            )
            if not lead.cosmetic:
                return None
        before = " ".join(token.key for token in self.act[:start])
        return verify.negation_before(before, len(before))

    @property
    def outside(self) -> tuple[tuple[Token, ...], tuple[Token, ...]]:
        """The subsection's tokens before and after the stretch `regions` covers: what the
        quote leaves out at its two ends, if it quotes less than the whole subsection."""
        first_a, first_b, _ = self.blocks[0]
        lead = self.quote[:first_b]
        start = max(0, first_a - first_b) if first_b and _has_word(lead) else first_a
        last_a, last_b, last_size = self.blocks[-1]
        end_a, end_b = last_a + last_size, last_b + last_size
        tail = self.quote[end_b:]
        end = end_a + len(tail) if end_b < len(self.quote) and _has_word(tail) else end_a
        return self.act[:start], self.act[end:]

    def pieces(self, *, whole: bool = False) -> list[tuple[str, tuple[Token, ...]]]:
        """The aligned stretch as (kind, tokens) pieces: "same" for tokens in both (as the Act
        has them), "del" for the Act's tokens that the quote drops or changes, "ins" for the
        quote's own. With `whole`, the rest of the subsection too, as "del", so the words left
        out before and after the quote show as dropped."""
        pieces: list[tuple[str, tuple[Token, ...]]] = []
        before, after = self.outside if whole else ((), ())
        before = tuple(_without_paragraph_markers(before))
        if before:
            pieces.append(("del", before))
        for region in self.regions:
            if region.same:
                pieces.append(("same", region.act))
                continue
            if region.act:
                pieces.append(("del", region.act))
            if region.quote:
                pieces.append(("ins", region.quote))
        if after:
            pieces.append(("del", after))
        return pieces

    def segments(self, *, whole: bool = False) -> list[tuple[str, str]]:
        """`pieces` with each piece's tokens joined into text."""
        return [(kind, _join(tokens)) for kind, tokens in self.pieces(whole=whole)]

    def wdiff(self, *, whole: bool = False) -> str:
        """`segments` as text: the Act's dropped or changed words as `[-…-]`, the quote's own as
        `{+…+}`."""
        marks = {"same": "{}", "del": "[-{}-]", "ins": "{{+{}+}}"}
        return " ".join(marks[kind].format(text) for kind, text in self.segments(whole=whole))


@dataclass(frozen=True)
class Classified:
    category: Category
    strict: verify.QuoteCheck
    with_conventions: verify.QuoteCheck
    alignment: Alignment | None
    """What the category rests on: the cited section's best subsection, or for `wrong_cite` the
    other section's. None when nothing lines up (`not_in_act`, `no_quote`), and possibly for a
    quote that passes only with conventions, whose pieces may each be shorter than `MIN_RUN`."""
    found_in: tuple[str, ...] = ()
    """For `wrong_cite`: the other sections that hold the words (the closest one, if their
    content words differ there too)."""
    words_changed: bool = False
    """For `wrong_cite`: content words also differ from the other section's."""
    dropped_negation: str | None = None
    """For `drops_negation`: the negating word the quote leaves out, as the Act has it."""

    @property
    def critical(self) -> tuple[str, ...]:
        if self.alignment is None:
            return ()
        return tuple(sorted({w for c in self.alignment.changes for w in c.critical}))


def tokens(text: str) -> list[Token]:
    found: list[Token] = []
    end = 0
    for match in TOKEN.finditer(text):
        gap = text[end : match.start()]
        before = "\n" if "\n" in gap or not found else " " if gap else ""
        found.append(Token(match.group(), verify.normalise(match.group()), before))
        end = match.end()
    return found


def align(quote: str, section: Section) -> Alignment | None:
    """The quote lined up with one subsection: one it lines up with (`Alignment.lined_up`) if
    any, then the one where most of its tokens match in order, ties going to the first. None if
    no subsection shares a run of `MIN_RUN` tokens with it."""
    quote_tokens = tuple(tokens(verify.normalise(quote).strip('"')))
    wanted = [token.key for token in quote_tokens]
    best: Alignment | None = None
    for part in verify.subsections(section.text):
        act = tokens(part)
        matcher = SequenceMatcher(None, [t.key for t in act], wanted, autojunk=False)
        # A match of punctuation alone (a closing ".") is no evidence of where the quote is.
        blocks = _trim_strays(
            [
                (a, b, size)
                for a, b, size in matcher.get_matching_blocks()
                if any(_is_word(key) for key in wanted[b : b + size])
            ]
        )
        if not blocks or max(size for *_, size in blocks) < MIN_RUN:
            continue
        here = Alignment(
            section.number, verify.subsection_marker(part), tuple(act), quote_tokens, blocks
        )
        if best is None or (here.lined_up, here.matched_words) > (
            best.lined_up,
            best.matched_words,
        ):
            best = here
    return best


def classify(quote: str, cited: str, sections: dict[str, Section]) -> Classified:
    """Sort a quote by how it differs from the Act; see the module docstring."""
    strict = verify.check_quote(quote, cited, sections)
    marked = verify.check_quote_with_conventions(quote, cited, sections)
    here = align(quote, sections[cited]) if cited in sections and strict != "no_quote" else None
    if strict == "no_quote":
        return Classified("no_quote", strict, marked, None)
    if strict == "verified":
        return Classified("verbatim", strict, marked, here)
    if strict == "drops_negation":
        word = verify.dropped_negation(verify.normalise(quote).strip('"'), sections[cited].text)
        return Classified("drops_negation", strict, marked, here, dropped_negation=word)
    if strict == "heading":
        return Classified("heading", strict, marked, here)
    if strict == "too_short" or verify.word_count(quote) < verify.MIN_QUOTE_WORDS:
        return Classified("too_short", strict, marked, here)
    if marked == "verified":
        return Classified("conventions", strict, marked, here)
    if here is not None and here.lined_up and here.cosmetic:
        if here.dropped_negation is not None:
            return Classified(
                "drops_negation", strict, marked, here, dropped_negation=here.dropped_negation
            )
        return Classified("cosmetic", strict, marked, here)
    elsewhere = {
        number: align(quote, section) for number, section in sections.items() if number != cited
    }
    same_words = tuple(
        number
        for number, alignment in elsewhere.items()
        if verify.check_quote_with_conventions(quote, number, sections) == "verified"
        or (
            alignment is not None
            and alignment.lined_up
            and alignment.cosmetic
            and alignment.dropped_negation is None
        )
    )
    if same_words:
        return Classified("wrong_cite", strict, marked, elsewhere[same_words[0]], same_words)
    if here is not None and here.lined_up:
        return Classified("content_words_differ", strict, marked, here)
    others = [a for a in elsewhere.values() if a is not None and a.lined_up]
    if others:
        closest = max(others, key=lambda a: a.matched_words)
        return Classified("wrong_cite", strict, marked, closest, (closest.section,), True)
    return Classified("not_in_act", strict, marked, None)


def _trim_strays(blocks: list[tuple[int, int, int]]) -> tuple[tuple[int, int, int], ...]:
    """`blocks` without opening or closing matches shorter than `MIN_RUN` that lie far from the
    next match in the Act (`STRAY_GAP`)."""
    kept = list(blocks)
    while len(kept) > 1 and kept[0][2] < MIN_RUN and _far(kept[0], kept[1]):
        kept.pop(0)
    while len(kept) > 1 and kept[-1][2] < MIN_RUN and _far(kept[-2], kept[-1]):
        kept.pop()
    return tuple(kept)


def _far(first: tuple[int, int, int], second: tuple[int, int, int]) -> bool:
    act_gap = second[0] - (first[0] + first[2])
    quote_gap = second[1] - (first[1] + first[2])
    return act_gap > quote_gap + STRAY_GAP


def _join(tokens: tuple[Token, ...]) -> str:
    return "".join((token.before and " ") + token.text for token in tokens).strip()


def _has_word(tokens: tuple[Token, ...]) -> bool:
    return any(_is_word(token.key) for token in tokens)


def _without_paragraph_markers(tokens: tuple[Token, ...]) -> list[Token]:
    """`tokens` without each `PARAGRAPH_MARKER` that starts a line of the Act."""
    kept, i = [], 0
    while i < len(tokens):
        if tokens[i].before == "\n":
            marker = PARAGRAPH_MARKER.match(" ".join(t.key for t in tokens[i:]))
            if marker:
                i += len(marker.group().split())
                continue
        kept.append(tokens[i])
        i += 1
    return kept


def _is_word(text: str) -> bool:
    return text[0].isalnum()
