import pytest

from lease_clause_checker.clauses import Clause
from lease_clause_checker.scoring import Answer, parse_answer, score, summarize

ACT_SECTIONS = {"4", "14", "26", "27", "106"}


def clause(
    label: str = "void", section: str = "14", also_accept: tuple[str, ...] = ("4",)
) -> Clause:
    fields = {
        "id": "c01",
        "text": "No pets.",
        "label": label,
        "kind": "clear",
        "section": section,
        "also_accept": list(also_accept),
        "rationale": "x",
        "quote": "x",
        "source": "x",
        "reviewed": True,
    }
    return Clause.model_validate(fields)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Reasoning.\n\nVerdict: not enforceable\nSection: 14", Answer("void", "14")),
        ("**Verdict:** Enforceable\n**Section:** s. 106(2)", Answer("allowed", "106")),
        ("- Verdict: unenforceable\n- Section: 4 (see also s. 14)", Answer("void", "4")),
        ("Verdict: enforceable\nSection: Tenancies Act, 2006, s. 27", Answer("allowed", "27")),
        ("Verdict: enforceable\nSection: RTA, S.O. 2006, c. 17, s. 8.1", Answer("allowed", "8.1")),
        # The final lines count, not a verdict quoted earlier in the reasoning.
        (
            "Verdict: enforceable.\nSection: 4\nVerdict: not enforceable\nSection: 14",
            Answer("void", "14"),
        ),
        ("Verdict: enforceable\nSection: RTA 2006, s. 14", Answer("allowed", "14")),
        ("Verdict: not enforceable.\nSections: 14 and 15", Answer("void", "14")),
        ("Verdict: not enforceable\nSection: N/A", Answer("void", None)),
        ("Verdict: enforceable (or: Verdict: not enforceable)", Answer(None, None)),
        ("> Verdict: enforceable", Answer(None, None)),
        ("The clause is void under section 14.", Answer(None, None)),
        ("Verdict: probably fine\nSection: none", Answer(None, None)),
    ],
    ids=[
        "plain", "markdown-and-subsection", "unenforceable-and-aside", "act-year",
        "act-chapter", "last-line-wins", "bare-year", "plural-and-full-stop", "no-number",
        "echoed-instruction", "quoted-not-final", "no-format", "unreadable",
    ],
)  # fmt: skip
def test_parse_answer_reads_only_the_final_answer_lines(text: str, expected: Answer) -> None:
    assert parse_answer(text) == expected


@pytest.mark.parametrize(
    ("label", "text", "outcome"),
    [
        ("void", "Verdict: not enforceable", "correct"),
        ("allowed", "Verdict: enforceable", "correct"),
        ("allowed", "Verdict: not enforceable", "false_alarm"),
        ("void", "Verdict: enforceable", "missed_void"),
        ("void", "It depends.", "no_verdict"),
    ],
)
def test_score_classifies_the_verdict(label: str, text: str, outcome: str) -> None:
    assert score(clause(label=label), text, ACT_SECTIONS).outcome == outcome


@pytest.mark.parametrize(
    ("cited", "result"),
    [
        ("14", "deciding"),
        ("4", "also_accepted"),
        ("26", "other_section"),
        ("99", "not_in_act"),
    ],
)
def test_score_classifies_the_cited_section(cited: str, result: str) -> None:
    text = f"Verdict: not enforceable\nSection: {cited}"

    assert score(clause(), text, ACT_SECTIONS).section_result == result


def test_score_without_a_section_line_cites_none() -> None:
    assert score(clause(), "Verdict: not enforceable", ACT_SECTIONS).section_result == "none"


def test_summarize_counts_lenient_and_exact_sections_separately() -> None:
    scores = [
        score(clause(), "Verdict: not enforceable\nSection: 14", ACT_SECTIONS),
        score(clause(), "Verdict: not enforceable\nSection: 4", ACT_SECTIONS),
        score(clause(label="allowed"), "Verdict: not enforceable\nSection: 99", ACT_SECTIONS),
    ]

    summary = summarize(scores)

    assert (summary.total, summary.void_total, summary.allowed_total) == (3, 2, 1)
    assert (summary.correct, summary.outcomes["false_alarm"]) == (2, 1)
    assert (summary.section_ok, summary.sections["deciding"]) == (2, 1)
    assert summary.sections["not_in_act"] == 1
