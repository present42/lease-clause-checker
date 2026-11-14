"""Check that a quote really appears in the section of the Act it is said to come from.

A quote verifies when, after normalising whitespace and quotation marks, it appears as whole
words within one subsection of the section's operative text and has at least `MIN_QUOTE_WORDS`
words (markers such as "(2)" or "(a)" don't count). Nothing else is forgiven: a changed,
added or dropped word, a changed letter case or an ellipsis means the quote is not verbatim.

Quoting a section's heading or a subsection's headnote is reported separately from a quote
that appears nowhere: headings are e-Laws' labels, not the operative text that decides a case.

The parser drops repealed provisions. Splitting by subsection keeps a quote from bridging a
dropped subsection, but not a dropped paragraph inside one: if (b) were repealed, a quote
running from (a) into (c) would verify. No rule section has such a gap today.
"""

import re
from dataclasses import dataclass
from itertools import pairwise
from typing import Literal

from lease_clause_checker.statute import Section

QuoteCheck = Literal["verified", "too_short", "heading", "not_found", "no_quote", "no_such_section"]
"""`verified`: whole words in one subsection's operative text, long enough. `too_short`: found,
but shorter than `MIN_QUOTE_WORDS`. `heading`: found only in a heading or headnote.
`not_found`: not in the section. `no_quote`: empty. `no_such_section`: the cited number is not
an in-force section of the Act."""

# Shorter quotes, such as "is void", appear in many sections and prove nothing about the clause.
MIN_QUOTE_WORDS = 5
# A subsection, clause or paragraph marker such as "(2)", "(1.1)", "(a)" or "(iv)": not a word.
MARKER = re.compile(r"\(\w+(?:\.\w+)?\)")
# A subsection starts a line of section text: "(1) ", "(1.1) ".
SUBSECTION_START = re.compile(r"^\(\d+(?:\.\d+)?\) ", re.MULTILINE)
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
        words = [token for token in wanted.split() if not MARKER.fullmatch(token)]
        return "verified" if len(words) >= MIN_QUOTE_WORDS else "too_short"
    if any(_contains(heading, wanted) for heading in (section.heading, *section.headnotes)):
        return "heading"
    return "not_found"


def quote_in_text(quote: str, section_text: str, *, fold_quotation_marks: bool = True) -> bool:
    """True if `quote` appears, starting and ending on word boundaries, inside one subsection.

    The parser drops repealed and not-yet-in-force provisions, so text on either side of a
    subsection boundary may not be continuous in the Act. Section text keeps one provision per
    line, and a quote may run across a subsection's clauses or paragraphs, so whitespace is
    always collapsed. With `fold_quotation_marks=False`, a straight apostrophe does not match
    the Act's curly one.
    """
    return any(_contains(part, quote, fold_quotation_marks) for part in _subsections(section_text))


def subsection_of(quote: str, section_text: str) -> str | None:
    """The marker, such as "(2)", of the first subsection that contains `quote` ("" if the
    section has no numbered subsections); None if none does."""
    for part in _subsections(section_text):
        if _contains(part, quote):
            marker = SUBSECTION_START.match(part)
            return marker.group(0).strip() if marker else ""
    return None


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
    act_word = None
    if matched:
        prefix = " ".join(words[:matched])
        for part in _subsections(section_text):
            found = re.search(rf"(?<!\w){re.escape(prefix)}(?!\w)\s*(\S+)?", normalise(part))
            if found:
                act_word = found.group(1)
                break
    return Difference(matched, words[matched], act_word)


def normalise(text: str, *, fold_quotation_marks: bool = True) -> str:
    """Collapse whitespace and, by default, make curly quotation marks and apostrophes straight."""
    if fold_quotation_marks:
        text = text.translate(QUOTATION_MARKS)
    return " ".join(text.split())


def _contains(text: str, quote: str, fold_quotation_marks: bool = True) -> bool:
    quote = normalise(quote, fold_quotation_marks=fold_quotation_marks)
    text = normalise(text, fold_quotation_marks=fold_quotation_marks)
    return re.search(rf"(?<!\w){re.escape(quote)}(?!\w)", text) is not None


def _subsections(section_text: str) -> list[str]:
    starts = [match.start() for match in SUBSECTION_START.finditer(section_text)]
    bounds = [0, *starts, len(section_text)]
    return [section_text[start:end] for start, end in pairwise(bounds)]
