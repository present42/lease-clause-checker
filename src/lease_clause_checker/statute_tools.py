"""The statute tools the MCP server exposes (`mcp_server.py`), as plain functions on the cached
Act: look a section up, check a quote from it, and check every citation and quote in a draft.

Everything here reads the e-Laws cache that `lcc statute fetch` writes, a copy that is not an
official version of the Act; nothing calls a network or a model. The check is
`verify.check_quote` (and its quoting-conventions variant), and the sorting and word-level diff
are `quotediff.classify`, so the tools give the answers the evaluation scores; finding the
citations and quotes in a draft is `drafts.read_draft`. They check wording, not legal meaning,
and give no verdict on any clause.
"""

import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, assert_never

from pydantic import BaseModel, ConfigDict

from lease_clause_checker import drafts, elaws, pipeline, quotediff, verify
from lease_clause_checker.statute import Section, parse_sections

SCOPE = (
    "Checks wording against a cached e-Laws copy of the Act (not an official version), not "
    "legal meaning. Not legal advice."
)
# The first subsection marker in a citation such as "s. 27(2)(a)": "(2)".
_GIVEN_SUBSECTION = re.compile(r"\(\d+(?:\.\d+)?\)")
_SECTION_NUMBER = re.compile(r"\d+(?:\.\d+)?")
# Categories whose quote is in the Act but for marks or cosmetics.
_IN_THE_ACT = frozenset({"verbatim", "drops_negation", "conventions", "cosmetic", "too_short"})
# Short names a caller may use for the one Act this server has; its own title also works.
RTA_NAMES = frozenset(
    {
        "rta",
        "residential tenancies act",
        "residential tenancies act, 2006",
        "residential tenancies act 2006",
        "s.o. 2006, c. 17",
        elaws.ACT_CODE,
    }
)
# What a draft may call the Act beside a citation: "s. 14 of the Act", "Ontario's Residential
# Tenancies Act".
_IN_A_DRAFT = frozenset({"act"})
_TITLE_ENDINGS = (" residential tenancies act", " residential tenancies act, 2006")
# How many other sections to name when a quote with no citation is in several.
_LISTED = 3
# The statute citation after an Act's title: ", S.O. 2006, c. 17".
_STATUTE_CITATION = re.compile(r",\s*(?:R\.)?S\.O\.\s*\d{4},\s*c\.\s*(?:[A-Z]+\.)?\d+(?:\.\d+)?$")


class UnknownActError(LookupError):
    """The tool was asked about an Act this server doesn't have."""


class UnknownSectionError(LookupError):
    """The citation names no in-force section or subsection of the Act."""


class _Documented(BaseModel):
    """Field docstrings become the descriptions in the tools' output schemas."""

    model_config = ConfigDict(use_attribute_docstrings=True)


class Source(_Documented):
    """Where the text came from, as every result reports it."""

    act: str
    """The Act's title and citation, as e-Laws gives it."""
    consolidation: str
    """e-Laws' consolidation period for the cached text."""
    retrieved: str
    """The date (UTC) the text was downloaded from e-Laws."""
    sha256: str
    """SHA-256 of the cached text, naming the exact version."""
    notice: str = elaws.NOTICE
    """The notice that must go with any reproduction of the text."""


class SectionText(_Documented):
    section: str
    """The section number."""
    heading: str
    """The section's heading on e-Laws."""
    url: str
    """The section on e-Laws."""
    text: str
    """The section's in-force operative text, one provision per line; repealed provisions,
    subsection headnotes and citations are left out."""
    subsection: str | None
    """The subsection the citation named, such as "(2)"; None if it named the whole section."""
    subsection_text: str | None
    """That subsection's text, with its paragraphs."""
    scope: str = SCOPE
    source: Source


class QuoteResult(_Documented):
    section: str
    """The section the quote was checked against, as parsed from the citation."""
    cited_subsection: str | None
    """The subsection the citation named, if any; the whole section is searched."""
    heading: str | None
    """The section's heading; None if the Act has no such section."""
    url: str | None
    """The section on e-Laws; None if the Act has no such section."""
    verbatim: bool
    """Whether the quote is word for word in the section (the strict check)."""
    strict: verify.QuoteCheck
    """The strict check: verified, drops_negation, too_short, heading, not_found, no_quote or
    no_such_section."""
    with_conventions: verify.QuoteCheck
    """The same check, with an ellipsis, cosmetic changes in brackets and a changed first
    capital read as the marks of a quotation."""
    category: quotediff.Category
    """How the quote differs from the Act, the first that fits: verbatim, drops_negation,
    heading, too_short, conventions, cosmetic, wrong_cite, content_words_differ, not_in_act or
    no_quote."""
    label: str
    """The category as a reader sees it."""
    summary: str
    """What was found, in a sentence or two."""
    aligned_with: str | None
    """The subsection whose wording the quote is closest to, such as "s. 27(1)"; another
    section's for a wrong citation; None if no subsection is close."""
    matched_words: int | None
    """How many of the quote's words match that subsection, in order."""
    quote_words: int | None
    """How many words the quote has."""
    diff: str | None
    """The quote against that subsection: the Act's words the quote drops or replaces as
    `[-…-]`, the quote's own words as `{+…+}`, the rest in both."""
    critical_words: list[str]
    """Words such as "not", "written" or "unless" that differ between the quote and the Act."""
    found_in: list[str]
    """For a wrong citation: the sections that hold the words."""
    dropped_negation: str | None
    """For drops_negation: the negating word the quote leaves out."""
    scope: str = SCOPE
    source: Source


Status = Literal[
    "checked", "uncited", "citation_only", "no_such_section", "other_statute", "unpaired_mark"
]


class Finding(_Documented):
    """One quote or citation found in a draft."""

    line: int
    """The line of the draft it starts on, from 1."""
    citation: str | None
    """The citation as read, such as "s. 27(2)"; None for a quote with no citation."""
    statute: str | None
    """The statute named beside the citation, as written; None if none is named."""
    quote: str | None
    """The quoted words, without the quotation marks; None for a citation with no quote."""
    status: Status
    """checked: a quote checked against the section cited with it. uncited: a quote with no
    citation in its sentence, checked against the section whose wording is closest, if any.
    citation_only: a citation of an in-force section, with no quote. no_such_section: a
    citation, with no quote, of a section or subsection the Act doesn't have in force.
    other_statute: a citation of another statute, and any quote with it; not checked.
    unpaired_mark: a quotation mark with no partner in its paragraph; not read."""
    summary: str
    """What was found, in a sentence or two."""
    result: QuoteResult | None
    """The quote's check, as verify_quote gives it; None if nothing was checked."""


class TextCheck(_Documented):
    findings: list[Finding]
    """Every quote and every citation outside a quote, in the order they appear; a citation
    that a quote goes with is reported with the quote."""
    summary: str
    """Counts of what was found."""
    scope: str = SCOPE
    source: Source


@dataclass(frozen=True)
class Statutes:
    """The cached Act, parsed once."""

    act: elaws.CachedAct
    sections: dict[str, Section]

    @classmethod
    def load(cls, cache_dir: Path) -> "Statutes":
        act = elaws.load_act(elaws.cache_path(cache_dir))
        return cls(act, parse_sections(act.content_html))

    @property
    def source(self) -> Source:
        return Source(
            act=self.act.title,
            consolidation=self.act.consolidation,
            retrieved=self.act.retrieved_at.date().isoformat(),
            sha256=self.act.content_sha256,
        )

    def get_section(self, act: str, section: str) -> SectionText:
        self._require(act)
        number, marker = split_citation(section)
        found = self.sections.get(number)
        if found is None:
            raise UnknownSectionError(_no_section(number))
        part = _subsection(found, marker) if marker is not None else None
        if marker is not None and part is None:
            raise UnknownSectionError(f"Section {number} has no in-force subsection {marker}.")
        return SectionText(
            section=number,
            heading=found.heading,
            url=elaws.section_url(found.anchor),
            text=found.text,
            subsection=marker,
            subsection_text=part.strip() if part is not None else None,
            source=self.source,
        )

    def verify_quote(self, act: str, section: str, quote: str) -> QuoteResult:
        self._require(act)
        number, marker = split_citation(section)
        found = self.sections.get(number)
        result = quotediff.classify(quote, number, self.sections)
        alignment = result.alignment
        aligned_with = (
            f"s. {alignment.section}{alignment.subsection}" if alignment is not None else None
        )
        return QuoteResult(
            section=number,
            cited_subsection=marker,
            heading=found.heading if found else None,
            url=elaws.section_url(found.anchor) if found else None,
            verbatim=result.strict == "verified",
            strict=result.strict,
            with_conventions=result.with_conventions,
            category=result.category,
            label=quotediff.LABELS[result.category],
            summary=summary(result, number) + _subsection_note(found, marker, result),
            aligned_with=aligned_with,
            matched_words=alignment.matched_words if alignment is not None else None,
            quote_words=alignment.quote_words if alignment is not None else None,
            diff=alignment.wdiff() if alignment is not None else None,
            critical_words=list(result.critical),
            found_in=list(result.found_in),
            dropped_negation=result.dropped_negation,
            source=self.source,
        )

    def check_text(self, text: str) -> TextCheck:
        """Every quote and section citation in `text` (see `drafts` for how they are found and
        paired), each quote checked as `verify_quote` checks it."""
        draft = drafts.read_draft(text)
        paired = {q.citation for q in draft.quotations if q.citation is not None}
        found = [(q.start, self._check_quotation(draft, q)) for q in draft.quotations]
        found += [
            (c.start, self._check_citation(draft, c)) for c in draft.citations if c not in paired
        ]
        found += [(mark, _unpaired(draft, mark)) for mark in draft.unpaired_marks]
        findings = [finding for _, finding in sorted(found, key=lambda pair: pair[0])]
        return TextCheck(findings=findings, summary=_tally(findings), source=self.source)

    def _check_quotation(self, draft: drafts.Draft, quotation: drafts.Quotation) -> Finding:
        citation = quotation.citation
        status: Status
        if citation is None:
            status = "uncited"
            said, result = self._locate(quotation.text)
        elif not self._is_this_act(citation.statute):
            status, said, result = "other_statute", _not_checked(citation), None
        else:
            result = self.verify_quote(self.act.title, citation.cited, quotation.text)
            status, said = "checked", result.summary
        return Finding(
            line=draft.line(quotation.start),
            citation=citation.cited if citation else None,
            statute=citation.statute if citation else None,
            quote=quotation.text,
            status=status,
            summary=said,
            result=result,
        )

    def _locate(self, quote: str) -> tuple[str, QuoteResult | None]:
        """A quote with no citation, checked against the section whose wording is closest: the
        one the classifier finds when the cited section is none."""
        located = quotediff.classify(quote, "", self.sections)
        if located.category == "too_short":
            return (
                "No citation goes with it, and it is too short to look up (under "
                f"{verify.MIN_QUOTE_WORDS} words).",
                None,
            )
        if not located.found_in:
            return (
                "No citation goes with it, and its wording is close to no subsection of the Act, "
                "so it may not quote the Act.",
                None,
            )
        closest, *others = located.found_in
        result = self.verify_quote(self.act.title, closest, quote)
        where = f"s. {closest} has the closest wording"
        if others:
            listed = ", ".join(f"s. {n}" for n in others[:_LISTED]) + (
                f" and {len(others) - _LISTED} more" if len(others) > _LISTED else ""
            )
            where = f"its words are in s. {closest} (checked here) and also {listed}"
        return f"No citation goes with it; {where}. {result.summary}", result

    def _check_citation(self, draft: drafts.Draft, citation: drafts.Citation) -> Finding:
        status: Status
        if not self._is_this_act(citation.statute):
            status, said = "other_statute", _not_checked(citation)
        else:
            number, marker = split_citation(citation.cited)
            section = self.sections.get(number)
            if section is None:
                status, said = "no_such_section", _no_section(number)
            else:
                status = "citation_only"
                heading = f" (e-Laws heading: {section.heading})" if section.heading else ""
                missing = (
                    f" It has no subsection {marker}; the citation was read as s. {number}."
                    if marker is not None and _subsection(section, marker) is None
                    else ""
                )
                said = (
                    f"s. {number}{heading} is in force; no quote goes with this citation, so "
                    f"there is nothing to check.{missing}"
                )
        return Finding(
            line=draft.line(citation.start),
            citation=citation.cited,
            statute=citation.statute,
            quote=None,
            status=status,
            summary=said,
            result=None,
        )

    def _is_this_act(self, statute: str | None) -> bool:
        """Whether a statute named beside a citation in a draft is this Act; no name means it
        is."""
        if statute is None:
            return True
        name = _STATUTE_CITATION.sub("", statute).lower()
        return name in RTA_NAMES | _IN_A_DRAFT | {self.act.title.lower()} or name.endswith(
            _TITLE_ENDINGS
        )

    def _require(self, act: str) -> None:
        if act.strip().lower() not in RTA_NAMES | {self.act.title.lower()}:
            raise UnknownActError(
                f"Unknown act {act!r}: this server has only the Residential Tenancies Act, 2006 "
                '(Ontario); call it "RTA".'
            )


def split_citation(citation: str) -> tuple[str, str | None]:
    """The section number and first subsection marker of a citation: "s. 27(2)(a)" gives
    ("27", "(2)"), "14" gives ("14", None). Raises `UnknownSectionError` if the citation names
    no section number."""
    number = pipeline.cited_section(citation)
    if not _SECTION_NUMBER.fullmatch(number):
        raise UnknownSectionError(f"{citation!r} doesn't name a section, such as 14 or s. 27(2).")
    marker = _GIVEN_SUBSECTION.search(citation)
    return number, marker.group(0) if marker else None


def summary(result: quotediff.Classified, section: str) -> str:
    """What was found, for a reader: whether the quote is in the section, and if not, how it
    differs."""
    where = f"s. {section}"
    alignment = result.alignment
    other = ", ".join(f"s. {n}" for n in result.found_in)
    match result.category:
        case "verbatim":
            return f"Word for word in {where}."
        case "no_quote":
            return "There is no quote to check."
        case "too_short":
            return f"Too short to check: under {verify.MIN_QUOTE_WORDS} words."
        case "heading":
            return f"Found only in a heading of {where}, not in its operative text."
        case "drops_negation":
            found = (
                f"Word for word in {where}"
                if result.strict == "drops_negation"
                else f"Matches {where} but for cosmetic differences"
            )
            return f"{found}, but it leaves out the “{result.dropped_negation}” right before it."
        case "conventions":
            return (
                f"In {where} if its ellipses, brackets or changed first capital are read as "
                "marking changes; not word for word as written."
            )
        case "cosmetic":
            return (
                f"Not word for word in {where}, but it differs only in articles, punctuation, "
                "case, singular and plural, or paragraph markers."
            )
        case "wrong_cite":
            if result.words_changed:
                return (
                    f"Not in {where}; it is closest to {other} instead, but content words differ."
                )
            return f"Not in {where}; the words are from {other}."
        case "content_words_differ":
            closest = (
                f" its wording is closest to s. {alignment.section}{alignment.subsection}, but"
                if alignment is not None and alignment.subsection
                else ""
            )
            critical = ", ".join(f"“{word}”" for word in result.critical)
            differ = f" Words that differ include {critical}." if critical else ""
            return f"Not word for word in {where}:{closest} content words differ.{differ}"
        case "not_in_act":
            if result.strict == "no_such_section":
                return (
                    f"{where} is not an in-force section of the Act, and the quote is close to "
                    "no other."
                )
            return f"Not in {where}, and close to no subsection of the Act."
        case _:
            assert_never(result.category)


_UNCHECKED: tuple[tuple[Status, str], ...] = (
    ("uncited", "Quotes with no citation"),
    ("citation_only", "Citations with no quote"),
    ("no_such_section", "Citations of no in-force section"),
    ("other_statute", "Citations of another statute, not checked"),
    ("unpaired_mark", "Quotation marks with no partner, not read"),
)


def _tally(findings: list[Finding]) -> str:
    """How many quotes there are, by category, and how many quotes and citations had no check
    against a cited section."""
    quotes = [f for f in findings if f.quote is not None]
    labels = Counter(f.result.label for f in quotes if f.result is not None)
    by_label = ", ".join(f"{label} {n}" for label, n in labels.most_common())
    statuses = Counter(f.status for f in findings)
    parts = [f"Quotes: {len(quotes)}" + (f" ({by_label})." if by_label else ".")]
    parts += [f"{label}: {statuses[status]}." for status, label in _UNCHECKED if statuses[status]]
    return " ".join(parts) + " Wording only: no verdict on any clause."


def _no_section(number: str) -> str:
    return f"The Act has no in-force section {number}."


def _unpaired(draft: drafts.Draft, mark: int) -> Finding:
    return Finding(
        line=draft.line(mark),
        citation=None,
        statute=None,
        quote=None,
        status="unpaired_mark",
        summary=(
            f"A quotation mark ({draft.text[mark]}) with no partner in its paragraph; any quote "
            "it opens or closes was not read."
        ),
        result=None,
    )


def _not_checked(citation: drafts.Citation) -> str:
    return f"Cites {citation.statute}, which this server doesn't have; not checked."


def _subsection_note(
    section: Section | None, marker: str | None, result: quotediff.Classified
) -> str:
    """A sentence when the citation names a subsection that doesn't exist, or the quote is
    found in another subsection of the same section than the one cited; else ""."""
    if section is None or marker is None:
        return ""
    where = f"s. {section.number}"
    if _subsection(section, marker) is None:
        return f" ({where} has no subsection {marker}; the whole section was searched.)"
    alignment = result.alignment
    if alignment is None or alignment.section != section.number:
        return ""
    if alignment.subsection == marker:
        return ""
    found = "is in" if result.category in _IN_THE_ACT else "is closest to"
    return f" (The quote {found} {where}{alignment.subsection}, not the cited {where}{marker}.)"


def _subsection(section: Section, marker: str) -> str | None:
    return next(
        (p for p in verify.subsections(section.text) if verify.subsection_marker(p) == marker),
        None,
    )
