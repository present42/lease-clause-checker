"""Find the section citations and the quotations in a draft (a memo, an answer, a note) and pair
each quotation with the citation it goes with, for `statute_tools.Statutes.check_text`.

This module only finds and pairs. Checking a quote against the Act is `verify` and `quotediff`,
as for a single quote. The rules are plain and deterministic, so a reader can predict them:

- A **citation** is "s.", "section" or "subsection" (or "§") followed by a section number and
  any subsection or paragraph markers, such as "s. 27(2)(a)". A plural ("ss.", "sections",
  "§§") may list numbers: "ss. 14 and 15" gives two citations. A range ("ss. 14-16") is read as
  its first section only. "subsection (2)", with no section number, is not a citation.
- A citation **names a statute** when one is written right before it ("Legislation Act, 2006,
  s. 67") or right after it ("s. 67 of the Legislation Act, 2006"). A statute is an Act or a
  Code, with any year and "S.O."/"R.S.O." chapter ("Human Rights Code, R.S.O. 1990, c. H.19"),
  a regulation ("O. Reg. 516/06") or an acronym in capitals ("RTA", "HRC"). A title is the
  capitalised words before "Act" or "Code", stopping at a word such as "See" or "Under"
  (`_NOT_IN_A_TITLE`). Which names are the Act being checked is for the caller to say.
- A **quotation** is text between two double quotation marks, curly or straight (either kind
  closes), within one paragraph. A mark with no partner in its paragraph is reported, not
  paired across the paragraph break. A citation inside a quotation is part of the quotation.
- A quotation **goes with** the citation right after it in the same sentence, with nothing
  between but punctuation, a statute's name and the words "of", "under", "in" or "the"
  ('"… is void" (RTA, s. 14)'; not '"… is void", see s. 14'); else with the nearest citation
  before it in the same sentence; else with none. Every quotation that goes with a citation is
  taken to quote the statute, so a quoted lease clause beside a citation is checked like a
  quote of the Act.
- A **sentence** ends at ".", "!" or "?" (inside a closing quotation mark or not) followed by a
  capital or an opening quotation mark, at a blank line, at a line break before a list item or
  a heading, and at the end of a heading. A full stop inside a statute's name ("O. Reg.",
  "c. H.19") ends none, nor does one inside a quotation, except right before its closing mark.
"""

import re
from bisect import bisect_right
from collections.abc import Callable
from dataclasses import dataclass

# "s." "ss." "section(s)" "subsection(s)" "§" "§§", not inside a word ("U.S. 14", "tenant's 14").
_SINGULAR = r"(?:[Ss]\.|[Ss]ection|[Ss]ubsection|§)"
_PLURAL = r"(?:[Ss]s\.|[Ss]ections|[Ss]ubsections|§§)"
# A section number and its markers, as `pipeline.CITED_SECTION` reads them: "27(2)(a)", "3(1.1)".
_NUMBER = r"\d+(?:\.\d+)?(?:\s?\((?:\d+(?:\.\d+)?|[a-z]{1,5}(?:\.\d+)?)\))*"
_LIST_SEPARATOR = r"\s*(?:,\s*(?:and\s+|or\s+)?|and\s+|or\s+)"
CITATION = re.compile(
    rf"(?<![\w'’.])(?:{_PLURAL}\s*{_NUMBER}(?:{_LIST_SEPARATOR}{_NUMBER})*|{_SINGULAR}\s*{_NUMBER})"
)
NUMBER = re.compile(_NUMBER)
# Capitalised words that start a sentence or a phrase rather than a statute's title.
_NOT_IN_A_TITLE = (
    r"(?:See|Under|Compare|Cf|The|In|Also|And|But|Per|By|As|Of|Unlike|Like|Following|Pursuant)"
)
# A statute's name: "the Act", "Legislation Act, 2006", "Human Rights Code, R.S.O. 1990,
# c. H.19", "Residential Tenancies Act, 2006, S.O. 2006, c. 17", "O. Reg. 516/06", "RTA".
_STATUTE = (
    rf"(?:(?:(?!{_NOT_IN_A_TITLE}\b)[A-Z][\w'’-]*\s+)*(?:Act|Code)(?:,\s*\d{{4}})?"
    r"(?:,\s*(?:R\.)?S\.O\.\s*\d{4},\s*c\.\s*(?:[A-Z]+\.)?\d+(?:\.\d+)?)?"
    r"|O\.\s*Reg\.\s*\d+/\d+|\b[A-Z]{2,6}\b)"
)
STATUTE = re.compile(_STATUTE)
STATUTE_BEFORE = re.compile(rf"(?P<name>{_STATUTE}),?\s*$")
STATUTE_AFTER = re.compile(rf",?\s*(?:of|under|in)\s+(?:the\s+)?(?P<name>{_STATUTE})")
BETWEEN = re.compile(
    rf"[\s,;:(\[\u2013\u2014-]*(?:(?:of|under|in)\s+)?(?:the\s+)?(?:{_STATUTE})?[\s,]*"
)
PARAGRAPH_BREAK = re.compile(r"\n[ \t]*\n")
QUOTATION = re.compile(r"[“\"](?P<words>[^“”\"]+)[”\"]")
QUOTATION_MARK = re.compile(r"[“”\"]")
# A full stop and a capital; a blank line; the line break before a list item or heading; the
# end of a heading.
SENTENCE_END = re.compile(
    r"[.!?][”\"’)\]]*(?=\s+[A-Z“\"])|\n[ \t]*\n|\n(?=[ \t]*(?:[-*•]\s|#|\d+[.)]\s))"
    r"|^[ \t]*#[^\n]*$",
    re.MULTILINE,
)
# How far back from a citation a statute's name is looked for.
_NAME_WINDOW = 160


@dataclass(frozen=True)
class Citation:
    text: str
    """The section number and markers, as written: "27(2)(a)"."""
    start: int
    """Where the citation starts in the draft (its "s." for the first of a list)."""
    end: int
    statute: str | None
    """The statute named right before or after it, as written ("Legislation Act, 2006", "the
    Act"); None if none is named."""

    @property
    def cited(self) -> str:
        """The citation in one form: "s. 27(2)(a)"."""
        return "s. " + re.sub(r"\s", "", self.text)


@dataclass(frozen=True)
class Quotation:
    text: str
    """The quoted words, without the quotation marks, a line break read as a space."""
    start: int
    """Where its opening quotation mark is in the draft."""
    end: int
    """Just after its closing quotation mark."""
    citation: Citation | None
    """The citation it goes with; None if none does."""


@dataclass(frozen=True)
class Draft:
    text: str
    quotations: tuple[Quotation, ...]
    citations: tuple[Citation, ...]
    """Every citation outside a quotation, in order, whether or not a quotation goes with it."""
    unpaired_marks: tuple[int, ...]
    """Where each quotation mark with no partner in its paragraph is."""

    def line(self, position: int) -> int:
        """The line, from 1, that `position` is on."""
        return self.text.count("\n", 0, position) + 1


def read_draft(text: str) -> Draft:
    spans = [
        span for paragraph in _paragraphs(text) for span in QUOTATION.finditer(text, *paragraph)
    ]
    unpaired = tuple(
        mark.start()
        for mark in QUOTATION_MARK.finditer(text)
        if not any(span.start() <= mark.start() < span.end() for span in spans)
    )
    citations = [
        citation
        for match in CITATION.finditer(text)
        if not any(span.start() <= match.start() < span.end() for span in spans)
        for citation in _citations(match, text)
    ]
    sentence = _sentences(text, spans)
    quotations = tuple(
        Quotation(
            " ".join(span.group("words").split()),
            span.start(),
            span.end(),
            _citation_for(span, citations, text, sentence),
        )
        for span in spans
    )
    return Draft(text, quotations, tuple(citations), unpaired)


def _paragraphs(text: str) -> list[tuple[int, int]]:
    """The (start, end) of each paragraph of `text`."""
    breaks = list(PARAGRAPH_BREAK.finditer(text))
    starts = [0] + [b.end() for b in breaks]
    ends = [b.start() for b in breaks] + [len(text)]
    return list(zip(starts, ends, strict=True))


def _citations(match: re.Match[str], text: str) -> list[Citation]:
    """One citation per number in `match`, each with the statute named beside the list."""
    before = STATUTE_BEFORE.search(text[max(0, match.start() - _NAME_WINDOW) : match.start()])
    after = STATUTE_AFTER.match(text, match.end())
    named = before or after
    statute = named.group("name") if named else None
    written = match.group(0)
    first_digit = next(i for i, char in enumerate(written) if char.isdigit())
    numbers = list(NUMBER.finditer(written, first_digit))
    return [
        Citation(
            number.group(0),
            match.start() if i == 0 else match.start() + number.start(),
            match.start() + number.end(),
            statute,
        )
        for i, number in enumerate(numbers)
    ]


def _sentences(text: str, quotations: list[re.Match[str]]) -> Callable[[int], int]:
    """A function giving the sentence number of a position in `text` (see the module
    docstring for where a sentence ends)."""
    names = [match.span() for match in STATUTE.finditer(text)]
    ends = [
        match.end()
        for match in SENTENCE_END.finditer(text)
        if not any(q.start() < match.start() < q.end() - 2 for q in quotations)
        and not any(start <= match.start() < end for start, end in names)
    ]
    return lambda position: bisect_right(ends, position)


def _citation_for(
    quotation: re.Match[str],
    citations: list[Citation],
    text: str,
    sentence: Callable[[int], int],
) -> Citation | None:
    here = sentence(quotation.start())
    after = next((c for c in citations if c.start >= quotation.end()), None)
    if (
        after is not None
        and sentence(after.start) == here
        and BETWEEN.fullmatch(text, quotation.end(), after.start)
    ):
        return after
    before = [c for c in citations if c.end <= quotation.start() and sentence(c.start) == here]
    return before[-1] if before else None
