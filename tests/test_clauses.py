import json
from collections import Counter
from pathlib import Path
from typing import Any

import pytest

from lease_clause_checker.clauses import (
    FROZEN_CLAUSES_SHA256,
    FROZEN_SETS,
    FROZEN_STRESS_SHA256,
    Clause,
    ClausesFileError,
    check_clauses,
    file_sha256,
    load_clauses,
)
from lease_clause_checker.elaws import PUBLIC_URL
from lease_clause_checker.rules import Rule, load_rules
from lease_clause_checker.statute import Section

DATA = Path(__file__).parents[1] / "data"
RULES = [
    Rule(section="1", heading="x", paraphrase="x", source=f"{PUBLIC_URL}#BK1"),
    Rule(section="2.1", heading="x", paraphrase="x", source=f"{PUBLIC_URL}#BK2"),
]


def record(**changes: Any) -> dict[str, Any]:
    """A valid clause record for fixture section 2.1, with `changes` applied."""
    base = {
        "id": "c01",
        "text": "The Tenant shall pay a gadget deposit of two months' rent.",
        "label": "void",
        "kind": "clear",
        "section": "2.1",
        "also_accept": ["1"],
        "rationale": "Over the cap.",
        "quote": "The deposit shall not exceed one month’s rent.",
        "source": f"{PUBLIC_URL}#BK2",
        "reviewed": False,
    }
    return base | changes


def write(tmp_path: Path, *lines: str) -> Path:
    path = tmp_path / "clauses.jsonl"
    path.write_text("".join(f"{line}\n" for line in lines), encoding="utf-8")
    return path


def test_load_reads_clauses_in_file_order(tmp_path: Path) -> None:
    path = write(tmp_path, json.dumps(record()), json.dumps(record(id="s02", label="allowed")))

    clauses = load_clauses(path)

    assert [clause.id for clause in clauses] == ["c01", "s02"]
    assert clauses[1].label == "allowed"


@pytest.mark.parametrize(
    ("bad_line", "message"),
    [
        ("", "blank line"),
        ("{not json", "Expecting property name"),
        (json.dumps(record(label="unsure")), "label"),
        (json.dumps(record(section=2)), "section"),
        (json.dumps(record(reviewd=False)), "reviewd"),
        (json.dumps(record(reviewed="yes")), "reviewed"),
        (json.dumps(record(reviewed=1)), "reviewed"),
        (json.dumps(record(text=" padded")), "text"),
        (json.dumps(record(text="two\nlines")), "text"),
        (json.dumps(record(section="2A")), "section"),
        (json.dumps(record(id="clause-1")), "id"),
        (json.dumps(record(id="x01")), "id"),
        (json.dumps(record(also_accept=["x"])), "also_accept"),
        (json.dumps(record())[:-1] + ', "label": "allowed"}', "more than once: label"),
    ],
    ids=[
        "blank",
        "not-json",
        "unknown-label",
        "number-not-string",
        "misspelt-field",
        "reviewed-string",
        "reviewed-int",
        "padded-text",
        "newline-in-text",
        "bad-section-number",
        "bad-id",
        "unknown-id-prefix",
        "bad-also-accept",
        "duplicate-key",
    ],
)
def test_load_names_the_line_and_problem_of_a_bad_record(
    tmp_path: Path, bad_line: str, message: str
) -> None:
    path = write(tmp_path, json.dumps(record()), bad_line)

    with pytest.raises(ClausesFileError, match=rf"(?s)clauses\.jsonl:2: .*{message}"):
        load_clauses(path)


def test_load_rejects_duplicate_ids(tmp_path: Path) -> None:
    path = write(tmp_path, json.dumps(record()), json.dumps(record()))

    with pytest.raises(ClausesFileError, match="more than once: c01"):
        load_clauses(path)


@pytest.mark.parametrize(
    ("raw", "message"),
    [
        (b"", "no clauses"),
        (b"\xef\xbb\xbf" + json.dumps(record()).encode() + b"\n", "byte-order mark"),
        (json.dumps(record()).encode() + b"\r\n", "carriage return"),
        (json.dumps(record()).encode(), "must end with a newline"),
        (b'{"id": "\xff"}\n', "not UTF-8"),
    ],
    ids=["empty", "bom", "crlf", "no-final-newline", "not-utf8"],
)
def test_load_accepts_only_one_byte_form_of_the_file(
    tmp_path: Path, raw: bytes, message: str
) -> None:
    path = tmp_path / "clauses.jsonl"
    path.write_bytes(raw)

    with pytest.raises(ClausesFileError, match=message):
        load_clauses(path)


def test_load_keeps_a_line_separator_inside_a_string(tmp_path: Path) -> None:
    path = write(tmp_path, json.dumps(record(rationale="Over\u2028the cap.")))

    with pytest.raises(ClausesFileError, match=r"(?s)clauses\.jsonl:1: .*rationale"):
        load_clauses(path)  # rejected by the field type, not split into two bad lines


def test_check_passes_a_verbatim_quote(sections: dict[str, Section]) -> None:
    assert check_clauses([Clause(**record())], RULES, sections) == []


def test_check_accepts_a_quote_running_across_a_subsections_paragraphs(
    sections: dict[str, Section],
) -> None:
    quote = "require a gadget deposit if, (a) the tenant agrees in writing"

    assert check_clauses([Clause(**record(quote=quote))], RULES, sections) == []


@pytest.mark.parametrize(
    "quote",
    [
        "The deposit shall not exceed one month's rent.",  # straight apostrophe
        "The deposit shall not exceed one months rent.",
        "Gadget deposits",  # the heading, not operative text
        "Interest on the deposit is payable yearly.",  # not yet in force
        "eposit shall not exceed",  # starts mid-word
        "The deposit shall not exc",  # ends mid-word
        # Continuous only because the parser dropped repealed (1.1) between the subsections:
        "returned at the end of the tenancy. (2) The deposit shall not exceed",
    ],
)
def test_check_rejects_a_quote_not_in_the_operative_text(
    sections: dict[str, Section], quote: str
) -> None:
    problems = check_clauses([Clause(**record(quote=quote))], RULES, sections)

    assert problems == ["c01: quote is not whole words within one subsection of s. 2.1"]


def test_check_reports_a_section_without_a_rule_and_a_wrong_link(
    sections: dict[str, Section],
) -> None:
    clauses = [
        Clause(**record(id="c01", section="4")),
        Clause(**record(id="c02", source=f"{PUBLIC_URL}#BK1")),
    ]

    assert check_clauses(clauses, RULES, sections) == [
        "c01: s. 4 is not one of the rules",
        f"c02: source {PUBLIC_URL}#BK1, rule has {PUBLIC_URL}#BK2",
    ]


def test_check_reports_bad_also_accept_sections(sections: dict[str, Section]) -> None:
    clauses = [
        Clause(**record(id="c01", also_accept=["2.1"])),
        Clause(**record(id="c02", also_accept=["1", "1"])),
        Clause(**record(id="c03", also_accept=["99"])),
    ]

    assert check_clauses(clauses, RULES, sections) == [
        "c01: s. 2.1 repeated in also_accept",
        "c02: s. 1 repeated in also_accept",
        "c03: also_accept s. 99 is not in the Act",
    ]


def test_check_reports_a_rule_whose_section_is_missing_from_the_act(
    sections: dict[str, Section],
) -> None:
    rules = [*RULES, Rule(section="7", heading="x", paraphrase="x", source=f"{PUBLIC_URL}#BK7")]
    clause = Clause(**record(section="7", source=f"{PUBLIC_URL}#BK7"))

    assert check_clauses([clause], rules, sections) == ["c01: s. 7 is not in the Act"]


COMMITTED_FILES = pytest.mark.parametrize("clauses_file", ["clauses.jsonl", "stress.jsonl"])


@COMMITTED_FILES
def test_committed_review_table_was_generated_from_the_committed_clauses(
    clauses_file: str,
) -> None:
    review_file = clauses_file.replace(".jsonl", "_review.md")
    review = (DATA / review_file).read_text(encoding="utf-8")

    assert f"`data/{clauses_file}` (SHA-256 `{file_sha256(DATA / clauses_file)}`)" in review


@COMMITTED_FILES
def test_committed_clauses_cite_committed_rules_and_respect_the_mix(clauses_file: str) -> None:
    """Offline check of the committed test sets. Whether their quotes are in the real Act is
    checked by `lcc clauses review` and the live tests."""
    clauses = load_clauses(DATA / clauses_file)
    rules = {rule.section: rule for rule in load_rules(DATA / "rules.yaml")}

    assert {clause.section for clause in clauses} <= rules.keys()
    assert all(rules[clause.section].source == clause.source for clause in clauses)
    assert all("4" in clause.also_accept for clause in clauses if clause.label == "void")
    assert max(Counter(clause.section for clause in clauses).values()) <= 4
    assert {clause.label for clause in clauses} == {"void", "allowed"}
    assert {(clause.label, clause.kind) for clause in clauses if clause.kind == "near_miss"} == {
        ("void", "near_miss"),
        ("allowed", "near_miss"),
    }


@pytest.mark.parametrize(
    ("clauses_file", "frozen_sha256"),
    [("clauses.jsonl", FROZEN_CLAUSES_SHA256), ("stress.jsonl", FROZEN_STRESS_SHA256)],
    ids=["main", "stress"],
)
def test_committed_clauses_are_reviewed_and_frozen(clauses_file: str, frozen_sha256: str) -> None:
    frozen_record = (DATA / "README.md").read_text(encoding="utf-8")

    assert file_sha256(DATA / clauses_file) == frozen_sha256
    assert frozen_sha256 in FROZEN_SETS
    assert f"SHA-256  {frozen_sha256}  " in frozen_record
    assert all(clause.reviewed for clause in load_clauses(DATA / clauses_file))


def test_stress_clauses_are_their_candidates_unchanged_but_for_review() -> None:
    candidates = {c.id: c for c in load_clauses(DATA / "stress_candidates.jsonl")}
    stress = load_clauses(DATA / "stress.jsonl")

    assert all(c.reviewed for c in stress)
    assert not any(c.reviewed for c in candidates.values())
    assert [c.model_copy(update={"reviewed": False}) for c in stress] == [
        candidates[c.id] for c in stress
    ]


def test_selection_run_answers_are_tied_to_the_committed_candidates() -> None:
    """The selection run can only be re-scored if the candidates file is the one it used."""
    digest = file_sha256(DATA / "stress_candidates.jsonl")
    records = sorted((DATA.parent / "results" / "stress-candidates").rglob("s*.json"))
    frozen_record = (DATA / "README.md").read_text(encoding="utf-8")

    assert len(records) == len(load_clauses(DATA / "stress_candidates.jsonl"))
    assert {json.loads(r.read_text(encoding="utf-8"))["clauses_sha256"] for r in records} == {
        digest
    }
    assert f"SHA-256  {digest}  " in frozen_record
