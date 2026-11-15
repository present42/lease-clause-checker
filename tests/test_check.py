import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from lease_clause_checker import check, cli
from lease_clause_checker.models import ModelSpec, model_by_key
from lease_clause_checker.pipeline import Attempt, assess, load_pipeline_prompt
from lease_clause_checker.providers import Complete, Completion, MissingKeyError
from lease_clause_checker.runs import RunRecord
from lease_clause_checker.statute import Section

ROOT = Path(__file__).parents[1]
HAIKU = model_by_key("claude-haiku-5-5")
# Section 1 of the sample Act (tests/fixtures/elaws_sample.html):
S1_QUOTE = "A provision in a tenancy agreement prohibiting widgets is void."
NOT_IN_ACT = "A provision prohibiting gadgets is void and of no effect."
LEASE = """SAMPLE LEASE

Rent is $1,000 a month.

1. The Tenant shall keep no widgets.

2) The Landlord may inspect the
   widget cupboard.

3. Rent is due on the first.
"""


def answer(verdict: str = "void", quote: str = S1_QUOTE) -> str:
    return json.dumps({"verdict": verdict, "section": "1", "quote": quote, "reason": "Because."})


def attempt(clause: check.LeaseClause, text: str, sections: dict[str, Section]) -> Attempt:
    record = RunRecord(
        clause_id=clause.id,
        model_key=HAIKU.key,
        model_id=HAIKU.model_id,
        served_model=HAIKU.model_id,
        prompt_version="p",
        prompt_sha256="0" * 64,
        clauses_sha256="0" * 64,
        request={},
        requested_at=datetime(2026, 11, 13, tzinfo=UTC),
        text=text,
        finish_reason="end_turn",
        input_tokens=1,
        output_tokens=1,
        raw={},
    )
    return assess(record, sections)


def clauses(text: str = LEASE) -> tuple[check.LeaseClause, ...]:
    return check.split_lease(text).clauses


def test_a_lease_splits_into_numbered_clauses_and_reports_the_rest() -> None:
    lease = check.split_lease(LEASE)

    assert [(c.number, c.text) for c in lease.clauses] == [
        ("1", "The Tenant shall keep no widgets."),
        ("2", "The Landlord may inspect the widget cupboard."),
        ("3", "Rent is due on the first."),
    ]
    assert lease.skipped == ("SAMPLE LEASE", "Rent is $1,000 a month.")
    assert lease.clauses[0].id == "clause-1"


def test_clauses_on_consecutive_lines_are_kept_apart() -> None:
    assert [c.text for c in clauses("1. a b\n2. c d\n3) e f")] == ["a b", "c d", "e f"]


def test_sub_items_stay_with_their_clause_and_a_signature_block_does_not() -> None:
    lease = check.split_lease(
        "1. The Tenant shall:\n\n(a) pay rent;\n(b) keep it clean.\n\nSigned: ____\n\n(c) stray"
    )

    assert [c.text for c in lease.clauses] == ["The Tenant shall: (a) pay rent; (b) keep it clean."]
    assert lease.skipped == ("Signed: ____", "(c) stray")


def test_dotted_numbers_a_byte_order_mark_and_windows_line_endings() -> None:
    lease = check.split_lease("﻿1.1 First\r\n1.2. Second\r\n\r\n10. Tenth\r\n")

    assert [(c.number, c.text) for c in lease.clauses] == [
        ("1.1", "First"),
        ("1.2", "Second"),
        ("10", "Tenth"),
    ]


@pytest.mark.parametrize(
    ("text", "error"),
    [
        ("A title only.\n\nNo clauses here.", "no numbered clauses"),
        ("1. a\n\n1) b", "repeat"),
        ("1. a\n\n01. b", "repeat"),  # the same id, so the same record file
    ],
)
def test_a_lease_without_clean_numbering_is_refused(text: str, error: str) -> None:
    with pytest.raises(check.LeaseFormatError, match=error):
        check.split_lease(text)


def test_each_verdict_shows_its_section_quote_and_link_and_unsure_shows_why(
    sections: dict[str, Section],
) -> None:
    one, two, three = clauses()
    findings = [
        check.Finding(one, (attempt(one, answer(), sections),)),
        check.Finding(two, (attempt(two, answer("unsure"), sections),)),
    ]

    text = check.render(findings, [one, two, three], sections, color=False)

    blocks = text.split("\n\n")
    assert blocks[0].startswith("  1  VOID     The Tenant shall keep no widgets.")
    assert f"“{S1_QUOTE}”" in blocks[0]
    assert f"https://www.ontario.ca/laws/statute/06r17#{sections['1'].anchor}" in blocks[0]
    assert "Because." not in blocks[0]  # an unchecked reason isn't shown beside a verdict
    assert blocks[1].startswith("  2  UNSURE   ")
    assert "Unsure because: Because." in blocks[1]
    assert blocks[2].startswith("  3  FAILED   ")
    assert blocks[-1] == "3 clauses: 1 void, 0 allowed, 1 unsure, 1 not answered."


def test_long_text_wraps_inside_the_width_under_its_clause(sections: dict[str, Section]) -> None:
    [one] = clauses("1. " + "The Tenant shall keep no widgets in the unit. " * 4)
    findings = [check.Finding(one, (attempt(one, answer(), sections),))]

    lines = check.render(findings, [one], sections, color=False, width=40).splitlines()[:-2]

    assert all(len(line) <= 40 for line in lines if "https://" not in line)  # links can't wrap
    assert all(line.startswith(" " * 14) for line in lines[1:])
    quote_lines = [line for line in lines if "widgets is void" in line or "“A" in line]
    assert len(quote_lines) >= 2  # the quote is wrapped too


def test_numbers_wider_than_three_keep_the_clause_text_aligned(
    sections: dict[str, Section],
) -> None:
    long, short = clauses("12.10 Long number.\n\n2. Short one.")
    findings = [check.Finding(c, (attempt(c, answer(), sections),)) for c in (long, short)]

    first, second = check.render(findings, [long, short], sections, color=False).split("\n\n")[:2]

    assert first.index("Long") == second.index("Short") == 5 + 2 + check.LABEL_WIDTH


def test_a_retry_is_marked_with_what_failed_and_two_failures_give_no_verdict(
    sections: dict[str, Section],
) -> None:
    one, two, three = clauses()
    bad = answer(quote=NOT_IN_ACT)
    findings = [
        check.Finding(one, (attempt(one, bad, sections), attempt(one, answer(), sections))),
        check.Finding(two, (attempt(two, bad, sections), attempt(two, bad, sections))),
        check.Finding(three, (attempt(three, "{}", sections), attempt(three, "{}", sections))),
    ]

    blocks = check.render(findings, [one, two, three], sections, color=False).split("\n\n")

    assert "(Asked again; the first answer failed: The quote does not appear" in blocks[0]
    assert blocks[1].startswith("  2  UNSURE")
    assert "No verdict: both answers failed. The quote does not appear" in blocks[1]
    assert "Asked again" not in blocks[1]
    assert "not a JSON object" in blocks[2]  # a malformed answer isn't called a failed quote


def test_colour_marks_the_verdict_only_when_asked(sections: dict[str, Section]) -> None:
    [one, *_] = clauses()
    findings = [check.Finding(one, (attempt(one, answer(), sections),))]

    plain = check.render(findings, [one], sections, color=False)
    coloured = check.render(findings, [one], sections, color=True)

    assert "\033[" not in plain
    assert f"\033[{check.COLOURS['void']}mVOID     \033[0m" in coloured


@pytest.mark.parametrize(
    ("choice", "tty", "environ", "expected"),
    [
        ("auto", True, {}, True),
        ("auto", False, {}, False),
        ("auto", True, {"NO_COLOR": "1"}, False),
        ("auto", True, {"NO_COLOR": ""}, True),  # an empty NO_COLOR doesn't count
        ("always", False, {"NO_COLOR": "1"}, True),
        ("never", True, {}, False),
    ],
)
def test_colour_choice(choice: str, tty: bool, environ: dict[str, str], expected: bool) -> None:
    assert check.use_color(choice, tty, environ) is expected


@pytest.mark.parametrize(("columns", "expected"), [(200, check.WIDTH), (60, 60)])
def test_output_width_is_the_terminal_s_up_to_the_cap(
    monkeypatch: pytest.MonkeyPatch, columns: int, expected: int
) -> None:
    monkeypatch.setattr("shutil.get_terminal_size", lambda: os.terminal_size((columns, 24)))

    assert check.terminal_width() == expected


def test_a_lease_s_answers_are_kept_by_its_text_and_committed_ones_come_first(
    tmp_path: Path,
) -> None:
    saved = tmp_path / "results" / "check"
    a = check.store_for("1. a", tmp_path, "p", saved)
    b = check.store_for("1. b", tmp_path, "p", saved)
    assert a.results_dir != b.results_dir
    assert a.results_dir.parent == tmp_path / "check"

    (saved / a.results_dir.name).mkdir(parents=True)

    assert check.store_for("1. a", tmp_path, "p", saved).results_dir.parent == saved


def write_inputs(tmp_path: Path) -> tuple[Path, Path]:
    lease = tmp_path / "lease.txt"
    lease.write_text(LEASE, encoding="utf-8")
    rules = tmp_path / "rules.yaml"
    rules.write_text(
        'rules:\n  - section: "1"\n    heading: x\n    paraphrase: x\n    source: x\n',
        encoding="utf-8",
    )
    return lease, rules


def test_cli_check_runs_the_pipeline_then_reuses_the_saved_answers(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    cache_dir: Path,
) -> None:
    lease, rules = write_inputs(tmp_path)
    calls: list[str] = []

    def always_void(spec: ModelSpec, request: dict[str, Any]) -> Completion:
        calls.append(request["messages"][0]["content"])
        return Completion(answer(), spec.model_id, "end_turn", 1000, 100, {})

    monkeypatch.setattr(cli, "live_models", lambda specs: always_void)
    argv = ["--cache-dir", str(cache_dir), "check", str(lease), "--rules", str(rules)]

    first = cli.main([*argv, "--color", "never"])
    second = cli.main([*argv, "--color", "never"])

    out = capsys.readouterr().out
    assert (first, second) == (0, 0)
    assert len(calls) == 3  # one per clause, and none the second time
    assert "3 new calls, 0 saved answers reused" in out
    assert "0 new calls, 3 saved answers reused" in out
    assert "3 clauses: 3 void, 0 allowed, 0 unsure." in out
    assert "Not legal advice" in out
    assert "Not checked (no clause number): “SAMPLE LEASE”, “Rent is $1,000 a month.”" in out


def test_cli_check_replays_saved_answers_without_making_the_models(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    cache_dir: Path,
) -> None:
    lease, rules = write_inputs(tmp_path)
    argv = ["--cache-dir", str(cache_dir), "check", str(lease), "--rules", str(rules)]
    monkeypatch.setattr(
        cli,
        "live_models",
        lambda specs: (
            lambda spec, request: Completion(answer(), spec.model_id, "end_turn", 1000, 100, {})
        ),
    )
    assert cli.main(argv) == 0

    def no_key(specs: list[ModelSpec]) -> Complete:
        raise MissingKeyError("ANTHROPIC_API_KEY is not set")

    monkeypatch.setattr(cli, "live_models", no_key)

    assert cli.main(argv) == 0  # every answer is saved, so no key is needed
    assert "0 new calls, 3 saved answers reused" in capsys.readouterr().out


def test_cli_check_exits_1_and_says_which_clauses_went_unanswered(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    cache_dir: Path,
) -> None:
    lease, rules = write_inputs(tmp_path)

    def fails(spec: ModelSpec, request: dict[str, Any]) -> Completion:
        raise RuntimeError("503 overloaded")

    monkeypatch.setattr(cli, "live_models", lambda specs: fails)

    exit_code = cli.main(
        ["--cache-dir", str(cache_dir), "check", str(lease), "--rules", str(rules)]
    )

    out, err = capsys.readouterr()
    assert exit_code == 1
    assert "3 clauses: 0 void, 0 allowed, 0 unsure, 3 not answered." in out
    assert "503 overloaded" in err


def test_the_sample_lease_splits_into_eight_clauses_and_nothing_numbered_is_lost() -> None:
    lease = check.split_lease((ROOT / "samples" / "lease_short.txt").read_text("utf-8-sig"))

    assert [c.number for c in lease.clauses] == [str(n) for n in range(1, 9)]
    assert len(lease.skipped) == 2  # the title and the address line


def test_check_gives_no_finding_for_a_failed_call(
    sections: dict[str, Section], tmp_path: Path
) -> None:
    prompt = load_pipeline_prompt(ROOT / "prompts", [sections["1"]])
    store = check.store_for(LEASE, tmp_path, prompt.version, tmp_path / "none")

    def fails(spec: ModelSpec, request: dict[str, Any]) -> Completion:
        raise RuntimeError("503 overloaded")

    findings, summary = check.check(clauses(), HAIKU, prompt, sections, store, fails, workers=1)

    assert findings == []
    assert len(summary.failures) == 3


def test_a_verbatim_quote_that_stops_early_is_shown_with_the_warning(
    sections: dict[str, Section],
) -> None:
    one, two, _ = clauses()
    early = json.dumps(
        {
            "verdict": "allowed",
            "section": "2.1",
            "quote": "A landlord may require a gadget deposit if,",
            "reason": "Because.",
        }
    )
    findings = [
        check.Finding(one, (attempt(one, early, sections),)),
        check.Finding(two, (attempt(two, answer(), sections),)),
    ]

    blocks = check.render(findings, [one, two], sections, color=False).split("\n\n")
    flat = " ".join(blocks[0].split())

    assert findings[0].attempts[0].passed
    assert (
        "Warning: word for word, but it may stop before the words that decide it. The Act goes "
        "on: “(a) the tenant agrees in writing; and (b) the deposit is returned at the end of "
        "the tenancy.”"
    ) in flat
    assert flat.index("Warning:") < flat.index("https://")
    assert "Warning" not in blocks[1]  # s. 1 is quoted whole
    coloured = check.render(findings, [one, two], sections, color=True)
    assert f"\033[{check.WARNING}m" in coloured
