from datetime import UTC, datetime
from pathlib import Path, PureWindowsPath
from typing import Any

from lease_clause_checker import elaws
from lease_clause_checker.clauses import Clause
from lease_clause_checker.review import render_review

ACT = elaws.CachedAct(
    source_url=elaws.PUBLIC_URL,
    api_url=elaws.API_URL,
    retrieved_at=datetime(2026, 11, 8, 20, 0, tzinfo=UTC),
    title="Sample Act",
    consolidation="Consolidation Period: From September 21, 2026.",
    content_sha256="ab" * 32,
    content_html="",
)
CLAUSES_SHA256 = "cd" * 32
CLAUSES_PATH = Path("data/clauses.jsonl")


def clause(clause_id: str, section: str, label: str, kind: str, **changes: Any) -> Clause:
    fields = {
        "id": clause_id,
        "text": "x",
        "label": label,
        "kind": kind,
        "section": section,
        "also_accept": [],
        "rationale": "Because.",
        "quote": "Decisive words.",
        "source": f"{elaws.PUBLIC_URL}#BK{section}",
        "reviewed": False,
    }
    return Clause.model_validate(fields | changes)


def rows(markdown: str) -> list[str]:
    return [line for line in markdown.splitlines() if line.startswith("| c")]


def test_review_has_one_row_per_clause_with_link_and_quote() -> None:
    markdown = render_review(
        [clause("c01", "14", "void", "near_miss", also_accept=["4", "134"])],
        CLAUSES_PATH,
        CLAUSES_SHA256,
        ACT,
    )

    assert rows(markdown) == [
        "| c01 | near-miss | **void** | [s. 14](https://www.ontario.ca/laws/statute/06r17#BK14)"
        " (also s. 4, s. 134) | x | Because. | “Decisive words.” |"
    ]


def test_review_escapes_markdown_so_cells_show_the_clause_as_written() -> None:
    text = "Pay $5 | *now* or `later` <b>"

    markdown = render_review(
        [clause("c01", "14", "void", "clear", text=text)], CLAUSES_PATH, CLAUSES_SHA256, ACT
    )

    assert "| Pay \\$5 \\| \\*now\\* or \\`later\\` \\<b\\> |" in rows(markdown)[0]


def test_review_header_names_both_file_versions_and_the_counts() -> None:
    clauses = [
        clause("c01", "106", "void", "clear"),
        clause("c02", "14", "allowed", "near_miss"),
        clause("c03", "14", "void", "clear"),
    ]

    markdown = render_review(clauses, PureWindowsPath(r"data\stress.jsonl"), CLAUSES_SHA256, ACT)

    assert f"`data/stress.jsonl` (SHA-256 `{CLAUSES_SHA256}`)" in markdown
    assert f"content SHA-256 `{'ab' * 32}`" in markdown
    assert "Not an official version" in markdown
    assert "3 clauses: 2 void, 1 allowed; 2 clear, 1 near-miss. Reviewed: 0 of 3." in markdown
    assert "By section: s. 14 (2), s. 106 (1)." in markdown
