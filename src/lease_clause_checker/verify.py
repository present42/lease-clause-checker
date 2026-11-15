"""Check that a quote really appears in the section of the Act it is said to come from.

A quote verifies when, after normalising whitespace and quotation marks, it appears as whole
words within one subsection of the section's operative text and has at least `MIN_QUOTE_WORDS`
words (markers such as "(2)" or "(a)" don't count). Nothing else is forgiven: a changed,
added or dropped word, a changed letter case or an ellipsis means the quote is not verbatim.

Quoting a section's heading or a subsection's headnote is reported separately from a quote
that appears nowhere: headings are e-Laws' labels, not the operative text that decides a case.

A verified quote can still stop before the words that decide a case, such as the opening of a
list without the item that applies. `ending` flags that beside the check, as a warning; it
never changes what "verified" means.

The parser drops repealed provisions. Splitting by subsection keeps a quote from bridging a
dropped subsection, but not a dropped paragraph inside one: if (b) were repealed, a quote
running from (a) into (c) would verify. No rule section has such a gap today.
"""

import re
from dataclasses import dataclass
from itertools import pairwise
from typing import Literal

from lease_clause_checker import wording
from lease_clause_checker.statute import Section

QuoteCheck = Literal[
    "verified",
    "drops_negation",
    "too_short",
    "heading",
    "not_found",
    "no_quote",
    "no_such_section",
]
"""`verified`: whole words in one subsection's operative text, long enough. `drops_negation`:
found, but each time just after a negating word (`NEGATIONS`) that the quote leaves out, as
"landlord shall …" quoted from "No landlord shall …". `too_short`: found, but shorter than
`MIN_QUOTE_WORDS`. `heading`: found only in a heading or headnote. `not_found`: not in the
section. `no_quote`: empty. `no_such_section`: the cited number is not an in-force section of
the Act."""

# Shorter quotes, such as "is void", appear in many sections and prove nothing about the clause.
MIN_QUOTE_WORDS = 5
# A quote that starts right after one of these and leaves it out may reverse what it quotes
# (`negation_before`). Chosen to cover the negations that open the rule sections' provisions;
# "nothing" and "cannot" are in the Act but not in those sections.
NEGATIONS = frozenset({"no", "not", "neither", "nor"})
# A subsection, clause or paragraph marker such as "(2)", "(1.1)", "(a)" or "(iv)": not a word.
MARKER = re.compile(r"\(\w+(?:\.\w+)?\)")
# A subsection starts a line of section text: "(1) ", "(1.1) ".
SUBSECTION_START = re.compile(r"^\(\d+(?:\.\d+)?\) ", re.MULTILINE)
# An ellipsis marking an omission in a quote: "…", "...", ". . ." or either in brackets.
ELLIPSIS = re.compile(r"\[\s*(?:…|\.\s?\.\s?\.)\s*\]|…|\.\s?\.\s?\.")
# A change marked in brackets, such as "[t]he" or "[A]".
BRACKETS = re.compile(r"\[([^\[\]]*)\]")
WORD = re.compile(r"\w+")
# Curly single quotation marks and apostrophes (U+2018-U+201B) become ', double ones
# (U+201C-U+201F) become ".
QUOTATION_MARKS = str.maketrans("\u2018\u2019\u201a\u201b\u201c\u201d\u201e\u201f", "''''\"\"\"\"")


def check_quote(quote: str, section_number: str, sections: dict[str, Section]) -> QuoteCheck:
    wanted = normalise(quote).strip('"')
    if not wanted:
        return "no_quote"
    section = sections.get(section_number)
    if section is None:
        return "no_such_section"
    if quote_in_text(wanted, section.text):
        if dropped_negation(wanted, section.text) is not None:
            return "drops_negation"
        return "verified" if word_count(wanted) >= MIN_QUOTE_WORDS else "too_short"
    if any(_contains(heading, wanted) for heading in (section.heading, *section.headnotes)):
        return "heading"
    return "not_found"


Completeness = Literal["ends_cleanly", "stops_early"]
"""`stops_early`: the quote is word for word, but a list item (a line of its subsection that
starts "(a)", "(i)", "1." and so on) begins after it, or it ends on a comma or on a word that
needs what follows (`OPEN_ENDINGS`) with more of the subsection after it. `ends_cleanly`:
neither. Every word-for-word quote that stops before a list item is flagged, but so is one that
ends inside a list after the item that applies, so the flag over-counts; and a quote that stops
at a full statement before a condition that is not a list item is not flagged."""
OPEN_ENDINGS = frozenset(
    {"a", "an", "and", "are", "by", "for", "if", "in", "into", "is", "may", "of", "on", "or"}
    | {"shall", "that", "the", "to", "which", "who", "with"}
)
# A clause or paragraph at the start of a line: "(a) ", "(iv) ", "1. ". Not a subsection.
LIST_ITEM = re.compile(r"(?:\([a-z]+(?:\.\d+)?\)|\d+(?:\.\d+)?\.)\s")
# How many of the Act's words after a quote that stops early are shown.
GOES_ON_WORDS = 20


@dataclass(frozen=True)
class Ending:
    completeness: Completeness
    subsection: str
    """The marker of the subsection the quote was found in, such as "(2)"; "" if the section
    has no numbered subsections."""
    goes_on: str
    """The Act's words after the quote in its subsection, without a leading comma, colon or
    semicolon, up to `GOES_ON_WORDS` with "…" if more follow; "" if no word follows."""

    @property
    def warning(self) -> str | None:
        """For stops_early, the warning a reader sees, with what the Act goes on to say; else
        None. The MCP tools, `lcc quote check` and `lcc check` all give it in these words."""
        if self.completeness != "stops_early":
            return None
        return (
            "Warning: word for word, but it may stop before the words that decide it. The Act "
            f"goes on: \u201c{self.goes_on}\u201d"
        )


def ending(
    quote: str, section_number: str, sections: dict[str, Section], subsection: str | None = None
) -> Ending | None:
    """Whether a word-for-word quote stops before the end of what it quotes (see
    `Completeness`), where it passes the check: the first place it appears that does not follow
    a negating word it leaves out (`negation_before`), in the cited `subsection` (such as "(2)")
    if it is there, else in the first subsection that holds it. None if no subsection holds it
    that way."""
    section = sections.get(section_number)
    wanted = normalise(quote).strip('"')
    if section is None or not wanted:
        return None
    parts = subsections(section.text)
    parts.sort(key=lambda part: subsection is None or subsection_marker(part) != subsection)
    for part in parts:
        lines = [normalise(line) for line in part.split("\n") if line.strip()]
        text = " ".join(lines)
        starts = [sum(len(line) + 1 for line in lines[:i]) for i in range(len(lines))]
        items = [start for start, line in zip(starts, lines, strict=True) if LIST_ITEM.match(line)]
        for found in re.finditer(rf"(?<!\w){re.escape(wanted)}(?!\w)", text):
            if negation_before(text, found.start()) is not None:
                continue
            rest = text[found.end() :]
            marker = subsection_marker(part)
            if not WORD.search(rest):
                return Ending("ends_cleanly", marker, "")
            words = rest.lstrip(" ,:;").split()
            goes_on = " ".join(words[:GOES_ON_WORDS]) + (" …" if len(words) > GOES_ON_WORDS else "")
            last = WORD.findall(wanted)[-1].lower()
            open_end = wanted.endswith(",") or last in OPEN_ENDINGS
            before_item = any(start >= found.end() for start in items)
            completeness: Completeness = (
                "stops_early" if open_end or before_item else "ends_cleanly"
            )
            return Ending(completeness, marker, goes_on)
    return None


def word_count(quote: str) -> int:
    """The quote's words, not counting markers such as "(2)" or ellipses."""
    tokens = ELLIPSIS.sub(" ", normalise(quote).strip('"')).split()
    return sum(not MARKER.fullmatch(token) for token in tokens)


def check_quote_with_conventions(
    quote: str, section_number: str, sections: dict[str, Section]
) -> QuoteCheck:
    """`check_quote`, but forgiving the marks a careful quotation uses for a change it makes.

    - An ellipsis ("…", "...", "[…]") marks an omission: it splits the quote into pieces that
      must appear in order, as whole words, within one subsection.
    - A bracketed change passes only if it is cosmetic (`wording.same_but_cosmetic`). Standing
      alone ("[A] landlord"), the brackets stand for as many Act words as they hold, or one,
      and must differ from them only in case, number or articles. Inside a word ("[T]he",
      "tenant[s]"), the bracketed letters must be the Act's, but for case; a plural ending
      ("[s]", "[es]") may also be absent from the Act.
    - The quote's first letter may differ in case from the Act's.
    - No piece may start right after a negating word it leaves out (`negation_before`), unless
      the piece before it ends with that word ("shall not … change").

    An unmarked omission or change still fails, and so do empty brackets. Strict `check_quote`
    is what the pipeline uses; this is for reporting a quote both ways.
    """
    strict = check_quote(quote, section_number, sections)
    if strict in ("verified", "drops_negation", "no_quote", "no_such_section"):
        return strict
    wanted = normalise(quote).strip('"')
    pieces = [piece.strip() for piece in ELLIPSIS.split(wanted) if piece.strip()]
    if not pieces or any(not m.group(1).strip() for m in BRACKETS.finditer(wanted)):
        return strict
    patterns = [_Piece.of(piece, first=i == 0) for i, piece in enumerate(pieces)]
    section_text = sections[section_number].text
    if not any(_in_order(patterns, normalise(part)) for part in subsections(section_text)):
        return strict
    words = [
        token
        for piece in pieces
        for token in BRACKETS.sub(lambda m: m.group(1), piece).split()
        if not MARKER.fullmatch(token)
    ]
    return "verified" if len(words) >= MIN_QUOTE_WORDS else "too_short"


@dataclass(frozen=True)
class _Piece:
    """One piece of a quote between ellipses, as a regular expression, with the text of each
    standalone bracket, whose match is checked after the expression finds a candidate."""

    pattern: re.Pattern[str]
    brackets: tuple[str, ...]
    ends_negated: bool
    """Whether the piece ends with a word in `NEGATIONS`, which then covers the next piece."""

    @classmethod
    def of(cls, piece: str, *, first: bool) -> "_Piece":
        parts: list[str] = []
        brackets: list[str] = []
        end = 0
        if first and piece[0].isalpha():
            parts.append(f"[{piece[0].upper()}{piece[0].lower()}]")
            end = 1
        for bracket in BRACKETS.finditer(piece, end):
            parts.append(re.escape(piece[end : bracket.start()]))
            inside = bracket.group(1)
            before = piece[bracket.start() - 1] if bracket.start() else " "
            after = piece[bracket.end()] if bracket.end() < len(piece) else " "
            if before.isalnum() or after.isalnum():
                letters = f"(?i:{re.escape(inside)})"
                plural = inside.lower() in ("s", "es") and not after.isalnum()
                parts.append(f"(?:{letters})?" if plural else letters)
            else:
                most = max(1, len(inside.split()))
                parts.append(rf"(?P<b{len(brackets)}>\S+(?: \S+){{0,{most - 1}}})")
                brackets.append(inside)
            end = bracket.end()
        parts.append(re.escape(piece[end:]))
        last = WORD.findall(piece)[-1:]
        ends_negated = bool(last) and last[0].lower() in NEGATIONS
        return cls(re.compile(rf"(?<!\w){''.join(parts)}(?!\w)"), tuple(brackets), ends_negated)

    def find(self, text: str, start: int, *, negated_ok: bool) -> re.Match[str] | None:
        """The first match from `start` whose standalone brackets differ from the Act's words
        only cosmetically and, unless `negated_ok`, that doesn't start right after a negating
        word."""
        found = self.pattern.search(text, start)
        while found is not None:
            negated = negation_before(text, found.start()) is not None
            if (negated_ok or not negated) and all(
                wording.same_but_cosmetic(WORD.findall(found.group(f"b{i}")), WORD.findall(inside))
                for i, inside in enumerate(self.brackets)
            ):
                return found
            found = self.pattern.search(text, found.start() + 1)
        return None


def _in_order(pieces: list[_Piece], text: str) -> bool:
    start, negated_ok = 0, False
    for piece in pieces:
        found = piece.find(text, start, negated_ok=negated_ok)
        if found is None:
            return False
        start, negated_ok = found.end(), piece.ends_negated
    return True


def quote_in_text(quote: str, section_text: str, *, fold_quotation_marks: bool = True) -> bool:
    """True if `quote` appears, starting and ending on word boundaries, inside one subsection.

    The parser drops repealed and not-yet-in-force provisions, so text on either side of a
    subsection boundary may not be continuous in the Act. Section text keeps one provision per
    line, and a quote may run across a subsection's clauses or paragraphs, so whitespace is
    always collapsed. With `fold_quotation_marks=False`, a straight apostrophe does not match
    the Act's curly one.
    """
    return any(_contains(part, quote, fold_quotation_marks) for part in subsections(section_text))


def subsection_of(quote: str, section_text: str) -> str | None:
    """The marker, such as "(2)", of the first subsection that contains `quote` ("" if the
    section has no numbered subsections); None if none does."""
    for part in subsections(section_text):
        if _contains(part, quote):
            return subsection_marker(part)
    return None


def subsections(section_text: str) -> list[str]:
    """The section's text split before each subsection marker; one part if it has none."""
    starts = [match.start() for match in SUBSECTION_START.finditer(section_text)]
    bounds = [0, *starts, len(section_text)]
    return [section_text[start:end] for start, end in pairwise(bounds) if end > start]


def subsection_marker(part: str) -> str:
    """The marker, such as "(2)", that starts a part from `subsections`; "" if none does."""
    marker = SUBSECTION_START.match(part)
    return marker.group(0).strip() if marker else ""


@dataclass(frozen=True)
class Difference:
    """Where a quote first leaves the Act's text."""

    matched: int
    """How many of the quote's opening words appear, in order, in one subsection."""
    quote_word: str
    """The quote's next word."""
    act_word: str | None
    """The word that follows the matched words in the Act; None if they end the subsection or
    no words matched."""
    subsection: str = ""
    """The marker of the subsection the matched words were found in, such as "(3)"; "" if the
    section has none or no words matched."""


def first_difference(quote: str, section_text: str) -> Difference | None:
    """Where `quote` stops matching the section, word by word; None if it all matches.

    The matched words are the longest opening run of the quote that appears within one
    subsection (as `quote_in_text` finds it); the first place they appear is the one reported.
    """
    words = normalise(quote).strip('"').split()
    matched = 0
    while matched < len(words) and quote_in_text(" ".join(words[: matched + 1]), section_text):
        matched += 1
    if matched == len(words):
        return None
    act_word, marker = None, ""
    if matched:
        prefix = " ".join(words[:matched])
        for part in subsections(section_text):
            found = re.search(rf"(?<!\w){re.escape(prefix)}(?!\w)\s*(\S+)?", normalise(part))
            if found:
                act_word, marker = found.group(1), subsection_marker(part)
                break
    return Difference(matched, words[matched], act_word, marker)


def normalise(text: str, *, fold_quotation_marks: bool = True) -> str:
    """Collapse whitespace and, by default, make curly quotation marks and apostrophes straight."""
    if fold_quotation_marks:
        text = text.translate(QUOTATION_MARKS)
    return " ".join(text.split())


def _contains(text: str, quote: str, fold_quotation_marks: bool = True) -> bool:
    quote = normalise(quote, fold_quotation_marks=fold_quotation_marks)
    text = normalise(text, fold_quotation_marks=fold_quotation_marks)
    return re.search(rf"(?<!\w){re.escape(quote)}(?!\w)", text) is not None


def dropped_negation(quote: str, section_text: str) -> str | None:
    """The negating word the quote leaves out, as the Act has it, if every place the quote
    appears is right after one (`negation_before`); None if the quote appears somewhere else
    too, or nowhere."""
    wanted = normalise(quote)
    words = []
    for part in subsections(section_text):
        text = normalise(part)
        for found in re.finditer(rf"(?<!\w){re.escape(wanted)}(?!\w)", text):
            word = negation_before(text, found.start())
            if word is None:
                return None
            words.append(word)
    return words[0] if words else None


def negation_before(text: str, start: int) -> str | None:
    """The word in `NEGATIONS` right before `start`, with only whitespace between; None if
    there is none. "not" in "whether or not" doesn't count: it negates nothing that follows.

    This is a narrow check, not a reading of a negation's scope. A quote starting after a
    punctuation mark passes even when a negation governs it: "without legal process, seize a
    tenant’s property" is still governed by "No landlord shall," in s. 40, and so is a
    paragraph quoted from under "No landlord shall, (a) …"."""
    before = re.search(r"(?:(\w+)\s+)?(\w+)\s*$", text[:start])
    if before is None or before.group(2).lower() not in NEGATIONS:
        return None
    if before.group(2).lower() == "not" and (before.group(1) or "").lower() == "or":
        return None
    return before.group(2)
