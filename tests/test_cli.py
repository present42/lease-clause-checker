import json
from pathlib import Path

import httpx
import pytest

from lease_clause_checker import cli, elaws
from lease_clause_checker.cli import main
from lease_clause_checker.models import MODELS
from lease_clause_checker.verify import Difference


def mock_elaws(monkeypatch: pytest.MonkeyPatch, content_html: str) -> list[httpx.Request]:
    """Route the CLI's HTTP client to a mock e-Laws serving `content_html`; returns the
    list of requests it receives."""
    body = {
        "title": "Sample Act",
        "description": "Consolidation Period: From September 21, 2026.",
        "content": content_html,
    }
    requests: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=body)

    def client() -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(handle))

    monkeypatch.setattr(cli, "http_client", client)
    return requests


def test_fetch_downloads_once_then_reuses_the_cache(
    tmp_path: Path, sample_html: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    requests = mock_elaws(monkeypatch, sample_html)

    assert main(["--cache-dir", str(tmp_path), "statute", "fetch"]) == 0
    assert main(["--cache-dir", str(tmp_path), "statute", "fetch"]) == 0

    assert len(requests) == 1
    assert elaws.load_act(elaws.cache_path(tmp_path)).content_html == sample_html


def test_fetch_refresh_downloads_again(
    tmp_path: Path, sample_html: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    requests = mock_elaws(monkeypatch, sample_html)

    main(["--cache-dir", str(tmp_path), "statute", "fetch"])
    main(["--cache-dir", str(tmp_path), "statute", "fetch", "--refresh"])

    assert len(requests) == 2


def test_fetch_does_not_cache_text_the_parser_rejects(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    mock_elaws(monkeypatch, '<p class="newKind">Surprise</p>')

    exit_code = main(["--cache-dir", str(tmp_path), "statute", "fetch"])

    assert exit_code == 1
    assert "newKind" in capsys.readouterr().err
    assert not elaws.cache_path(tmp_path).exists()


def test_sections_prints_number_and_heading_table(
    cache_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    exit_code = main(["--cache-dir", str(cache_dir), "statute", "sections", "2.1", "1"])

    out = capsys.readouterr().out
    assert exit_code == 0
    assert "Retrieved 2026-11-08 from https://www.ontario.ca/laws/statute/06r17" in out
    assert "Not an official version" in out
    rows = out.splitlines()[-2:]
    assert rows == ["2.1      Gadget deposits", "1        “No widget” provisions void"]


def test_missing_section_is_an_error_not_a_traceback(
    cache_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    exit_code = main(["--cache-dir", str(cache_dir), "statute", "sections", "99"])

    assert exit_code == 1
    assert "section(s) 99" in capsys.readouterr().err


def test_sections_without_cache_is_an_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    exit_code = main(["--cache-dir", str(tmp_path), "statute", "sections", "1"])

    assert exit_code == 1
    assert "lcc statute fetch" in capsys.readouterr().err


def run_check_rules(cache_dir: Path, tmp_path: Path, heading: str) -> int:
    """Run `lcc statute check-rules` with a one-rule file for section 2.1 of the fixture."""
    rules = tmp_path / "rules.yaml"
    rules.write_text(
        f'rules:\n  - section: "2.1"\n    heading: "{heading}"\n'
        '    paraphrase: "x"\n    source: "https://www.ontario.ca/laws/statute/06r17#BK2"\n',
        encoding="utf-8",
    )
    return main(["--cache-dir", str(cache_dir), "statute", "check-rules", "--rules", str(rules)])


def test_check_rules_lists_matching_rules(
    cache_dir: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    exit_code = run_check_rules(cache_dir, tmp_path, heading="Gadget deposits")

    out = capsys.readouterr().out
    assert exit_code == 0
    assert "2.1      Gadget deposits" in out
    assert "All 1 rules" in out


def test_check_rules_fails_on_a_mismatch(
    cache_dir: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    exit_code = run_check_rules(cache_dir, tmp_path, heading="Gizmo deposits")

    assert exit_code == 1
    assert "e-Laws has 'Gadget deposits'" in capsys.readouterr().err


def run_review(cache_dir: Path, tmp_path: Path, quote: str) -> tuple[int, Path]:
    """Run `lcc clauses review` with one clause citing fixture section 2.1."""
    rules = tmp_path / "rules.yaml"
    rules.write_text(
        'rules:\n  - section: "2.1"\n    heading: "Gadget deposits"\n'
        '    paraphrase: "x"\n    source: "https://www.ontario.ca/laws/statute/06r17#BK2"\n',
        encoding="utf-8",
    )
    clauses = tmp_path / "clauses.jsonl"
    record = {
        "id": "c01",
        "text": "A gadget deposit of two months' rent.",
        "label": "void",
        "kind": "clear",
        "section": "2.1",
        "also_accept": [],
        "rationale": "Over the cap.",
        "quote": quote,
        "source": "https://www.ontario.ca/laws/statute/06r17#BK2",
        "reviewed": False,
    }
    clauses.write_text(json.dumps(record, ensure_ascii=False) + "\n", encoding="utf-8")
    out = tmp_path / "review.md"
    paths = ["--rules", str(rules), "--clauses", str(clauses), "--out", str(out)]
    exit_code = main(["--cache-dir", str(cache_dir), "clauses", "review", *paths])
    return exit_code, out


def test_review_writes_the_table_when_every_quote_verifies(
    cache_dir: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    quote = "The deposit shall not exceed one month’s rent."

    exit_code, out = run_review(cache_dir, tmp_path, quote)

    assert exit_code == 0
    assert "| c01 | clear | **void** |" in out.read_text(encoding="utf-8")
    assert "not legal advice" in capsys.readouterr().out


def test_review_writes_nothing_when_a_quote_fails(
    cache_dir: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    exit_code, out = run_review(cache_dir, tmp_path, "The deposit is capped.")

    assert exit_code == 1
    assert "c01: quote is not whole words within one subsection" in capsys.readouterr().err
    assert not out.exists()


def test_run_takes_only_current_models_and_report_also_takes_dropped_ones() -> None:
    parser = cli._parser()
    current = [spec.key for spec in MODELS]

    assert parser.parse_args(["baseline", "run"]).models == current
    assert parser.parse_args(["pipeline", "report"]).models == current
    assert parser.parse_args(["baseline", "report", "--models", "gemini-4-carbon"]).models == [
        "gemini-4-carbon"
    ]
    with pytest.raises(SystemExit):
        parser.parse_args(["baseline", "run", "--models", "gemini-4-carbon"])


def test_quote_check_verifies_a_verbatim_quote(
    cache_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    quote = "The deposit shall not exceed one month’s rent."

    exit_code = main(["--cache-dir", str(cache_dir), "quote", "check", "s. 2.1(2)", quote])

    out = capsys.readouterr().out
    assert exit_code == 0
    assert out.startswith("VERIFIED: the quote appears word for word in s. 2.1(2).\nSource:")
    assert "Not an official version" in out
    assert "Not legal advice" in out


def test_quote_check_names_the_first_tampered_word(
    cache_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    quote = "A landlord may require a deposit if, the tenant agrees in writing"

    exit_code = main(["--cache-dir", str(cache_dir), "quote", "check", "2.1", quote])

    out = capsys.readouterr().out
    assert exit_code == 1
    assert out.splitlines()[:2] == [
        "NOT VERIFIED: The quote does not appear word for word in section 2.1.",
        "The first 5 words match s. 2.1(1); then the quote has “deposit” where the Act has "
        "“gadget”.",
    ]
    assert "Not legal advice" in out


@pytest.mark.parametrize(
    ("section", "quote", "says"),
    [
        ("9", "This Act binds the Crown.", "Section 9 is not a section of the Act."),
        ("4", "binds the Crown", "The quote has fewer than 5 words."),
        ("4", "", "NOT VERIFIED: The quote is empty."),
        (
            "2.1",
            "exceed one month’s rent.",
            "The Act has “not” right before the quote, and the quote leaves it out.",
        ),
        ("1", "Widgets are void in a tenancy", "is not in s. 1 with that capitalisation"),
        (
            "2.1",
            "Gizmo deposits are allowed here",
            "The quote's first word, “Gizmo”, is not in s. 2.1.\n",
        ),
        (
            "2.1",
            "the tenancy. (2) The deposit shall",
            "then the quote has “(2)” where the subsection ends.",
        ),
    ],
)
def test_quote_check_explains_each_failure(
    cache_dir: Path, capsys: pytest.CaptureFixture[str], section: str, quote: str, says: str
) -> None:
    exit_code = main(["--cache-dir", str(cache_dir), "quote", "check", section, quote])

    assert exit_code == 1
    assert says in capsys.readouterr().out


@pytest.mark.parametrize(
    ("section", "quote", "out"),
    [
        (
            "1",
            "A provision in a tenancy agreement prohibiting widgets",
            "in s. 1.\nWords of its sentence in s. 1 that it leaves out: after it, “is void”."
            "\nSource:",
        ),
        (
            "s. 2.1(2)",
            "the tenant agrees in writing; and",
            "in s. 2.1(1).\nNote: you gave s. 2.1(2); the check searches all of s. 2.1.\n",
        ),
    ],
)
def test_quote_check_names_the_subsection_it_found(
    cache_dir: Path, capsys: pytest.CaptureFixture[str], section: str, quote: str, out: str
) -> None:
    exit_code = main(["--cache-dir", str(cache_dir), "quote", "check", section, quote])

    assert exit_code == 0
    assert f"VERIFIED: the quote appears word for word {out}" in capsys.readouterr().out


def test_quote_check_warns_when_a_verbatim_quote_may_stop_early(
    cache_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    quote = "A landlord may require a gadget deposit if,"
    exit_code = main(["--cache-dir", str(cache_dir), "quote", "check", "2.1", quote])

    out = " ".join(capsys.readouterr().out.split())
    assert exit_code == 0  # a warning, not a failure
    assert "VERIFIED" in out
    assert (
        "Warning: word for word, but it may stop before the words that decide it. The Act goes "
        "on: “(a) the tenant agrees in writing;"
    ) in out
    assert "after it" not in out  # the warning already says how the Act goes on


def test_quote_check_names_the_words_a_failed_quote_leaves_out_of_its_sentence(
    cache_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    quote = "the tenant agrees in writing, and"  # "," for the Act's ";"
    exit_code = main(["--cache-dir", str(cache_dir), "quote", "check", "2.1", quote])

    out = " ".join(capsys.readouterr().out.split())
    assert exit_code == 1
    assert out.startswith("NOT VERIFIED")
    assert (  # the list's lead-in, without the marker "(a)" or the next item
        "Words of its sentence in s. 2.1(1) that it leaves out: before it, “A landlord may "
        "require a gadget deposit if”."
    ) in out


# Synthetic wording: the quote's opening words run longer in (2) than in (1), which it lines up
# with.
VISITS = (
    "(1) A landlord may visit a unit at any hour without notice,\n(a) in an emergency.\n"
    "(2) A landlord may visit the unit to show it."
)


@pytest.mark.parametrize(
    ("lined_up", "difference"),
    [
        ("(1)", Difference(4, "the", "a", "(1)")),
        ("", Difference(6, "at", "to", "(2)")),  # lines up with no subsection
        ("(9)", Difference(6, "at", "to", "(2)")),  # no such subsection
    ],
)
def test_a_failed_quote_is_compared_with_the_subsection_it_lines_up_with(
    lined_up: str, difference: Difference
) -> None:
    quote = "A landlord may visit the unit at any hour without notice"

    assert cli._first_difference_in(quote, VISITS, lined_up) == difference


def test_a_quote_whose_first_word_is_not_in_its_subsection_is_compared_with_the_section() -> None:
    quote = "the unit to show it. (1) A landlord"

    assert cli._first_difference_in(quote, VISITS, "(1)") == Difference(5, "(1)", None, "(2)")
