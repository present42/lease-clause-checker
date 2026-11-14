"""Split the e-Laws HTML of the Act into sections: number, heading and in-force text.

e-Laws marks each paragraph with a CSS class. A section starts at a `section` paragraph,
whose number is in `<b>`, and its heading is the `headnote` paragraph just before it. The
section's text is the following provision paragraphs, up to the next section or structural
heading (Part titles and cross-headings within a Part).

Left out of a section's text, without changing any remaining wording:
- citations and amendment histories (e.g. "2006, c. 17, s. 14.");
- headnotes of subsections (kept apart in `headnotes`; the section's own is its heading);
- provisions not yet in force (classes starting with "Y") and the notes introducing them;
- repealed provisions, which e-Laws shows as e.g. "(2) Repealed: 2017, c. 13, s. 3 (2).";
- editorial "Omitted (...)" placeholder sections, such as 247-263.

Anything this parser does not recognise raises `StatuteParseError` instead of being dropped,
so a change in e-Laws markup can't silently lose statute text.
"""

import re
from collections.abc import Iterable
from dataclasses import dataclass, field

from bs4 import BeautifulSoup, Tag
from bs4.element import NavigableString

SECTION = "section"
HEADNOTE = "headnote"
PROVISION_CLASSES = frozenset(
    {
        "subsection",
        "paragraph",
        "subpara",
        "subsubpara",
        "subclause",
        "subsubclause",
        "definition",
        "parawindt2",
    }
)
STRUCTURAL_HEADING_CLASSES = frozenset({"partnum", "heading1"})
# Table-of-contents entries; e-Laws puts them inside a <table>. "table" is the TOC entry title.
TOC_CLASSES = frozenset({"TOCid", "TOCheadCenter", "TOCpartCenter", "table"})
# Paragraphs dropped wholesale, each allowed only with the text e-Laws gives it.
FIXED_TEXT_CLASSES = {
    "toc": re.compile(r"CONTENTS"),
    "MsoNormal": re.compile(r""),  # empty spacer
    "line": re.compile(r"_*"),  # horizontal rule
}
EDITORIAL_CLASSES = frozenset({"footnoteLeft", "Pnote"})  # amendment history; not-in-force note
NOT_IN_FORCE_PREFIX = "Y"
NOT_IN_FORCE_SECTION = NOT_IN_FORCE_PREFIX + SECTION
CITATION_SEPARATOR = ";"
REPEALED = re.compile(r"Repealed:$")
OMITTED = re.compile(r"Omitted \(.*\)\.")


class StatuteParseError(ValueError):
    """The e-Laws HTML did not have the structure this parser expects."""


class SectionNotFoundError(LookupError):
    """One or more requested sections are not in the parsed Act."""


@dataclass(frozen=True)
class Section:
    number: str
    heading: str
    text: str
    anchor: str
    """e-Laws' HTML anchor for the section (e.g. "BK19"), for building deep links."""
    headnotes: tuple[str, ...] = ()
    """The headnotes of its in-force subsections, such as "Same, amount"; not operative text."""


@dataclass
class _SectionDraft:
    number: str
    heading: str
    anchor: str | None
    lines: list[str] = field(default_factory=list)
    headnotes: list[str] = field(default_factory=list)

    def add(self, line: str, headnote: str = "") -> None:
        if REPEALED.search(line):
            return
        self.lines.append(line)
        if headnote:
            self.headnotes.append(headnote)

    def is_placeholder(self) -> bool:
        if not self.lines:
            return True
        return len(self.lines) == 1 and OMITTED.fullmatch(self.lines[0]) is not None

    def build(self) -> Section:
        if self.anchor is None:
            raise StatuteParseError(f"Section {self.number} has no anchor to link to")
        return Section(
            self.number, self.heading, "\n".join(self.lines), self.anchor, tuple(self.headnotes)
        )


def parse_sections(content_html: str) -> dict[str, Section]:
    """Return the Act's in-force sections, keyed by section number, in statute order."""
    soup = BeautifulSoup(content_html, "html.parser")
    _remove_citations(soup)

    drafts: dict[str, _SectionDraft] = {}
    current: _SectionDraft | None = None
    pending_heading = ""
    for paragraph in soup.find_all("p"):
        kind = _kind(paragraph)
        if kind == HEADNOTE:
            pending_heading = _text(paragraph)
            continue
        if kind == SECTION:
            current = _start_section(paragraph, pending_heading)
            if current.number in drafts:
                raise StatuteParseError(f"Section {current.number} appears twice")
            drafts[current.number] = current
        elif kind in PROVISION_CLASSES:
            if current is None:
                raise StatuteParseError(f"Provision outside any section: {_text(paragraph)[:80]}")
            current.add(_text(paragraph), headnote=pending_heading)
        elif kind in STRUCTURAL_HEADING_CLASSES or kind == NOT_IN_FORCE_SECTION:
            current = None
        elif not _is_ignorable(paragraph, kind):
            raise StatuteParseError(f"Unrecognised paragraph: {kind!r} {_text(paragraph)[:80]}")
        pending_heading = ""
    return {number: draft.build() for number, draft in drafts.items() if not draft.is_placeholder()}


def select_sections(sections: dict[str, Section], numbers: Iterable[str]) -> list[Section]:
    """Return the requested sections in the order given; fail if any are missing."""
    wanted = list(numbers)
    missing = [number for number in wanted if number not in sections]
    if missing:
        raise SectionNotFoundError(f"Not in the Act: section(s) {', '.join(missing)}")
    return [sections[number] for number in wanted]


def _remove_citations(soup: BeautifulSoup) -> None:
    """Delete citation spans and the "; " e-Laws puts between consecutive citations."""
    for citation in soup.select("span.citation"):
        separator = citation.previous_sibling
        if isinstance(separator, NavigableString) and separator.strip() == CITATION_SEPARATOR:
            separator.extract()
        citation.decompose()


def _is_ignorable(paragraph: Tag, kind: str) -> bool:
    if kind in EDITORIAL_CLASSES or kind.startswith(NOT_IN_FORCE_PREFIX):
        return True
    if kind in TOC_CLASSES:
        return paragraph.find_parent("table") is not None
    allowed_text = FIXED_TEXT_CLASSES.get(kind)
    return allowed_text is not None and allowed_text.fullmatch(_text(paragraph)) is not None


def _kind(paragraph: Tag) -> str:
    classes = paragraph.get("class")
    if not classes:
        raise StatuteParseError(f"Paragraph without a class: {_text(paragraph)[:80]}")
    return str(classes[0])


def _start_section(paragraph: Tag, heading: str) -> _SectionDraft:
    number_tag = paragraph.find("b")
    if not isinstance(number_tag, Tag):
        raise StatuteParseError(f"Section without a number: {_text(paragraph)[:80]}")
    number = number_tag.get_text(strip=True)
    number_tag.decompose()
    anchor_tag = paragraph.find("a", attrs={"name": True})
    anchor = str(anchor_tag["name"]) if isinstance(anchor_tag, Tag) else None
    draft = _SectionDraft(number=number, heading=heading, anchor=anchor)
    draft.add(_text(paragraph))
    return draft


def _text(tag: Tag) -> str:
    """Visible text with runs of whitespace (including non-breaking spaces) collapsed."""
    return " ".join(tag.get_text().split())
