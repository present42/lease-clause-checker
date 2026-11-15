"""Command-line entry point: `lcc`."""

import argparse
import os
import sys
import textwrap
import time
from collections.abc import Sequence
from pathlib import Path

import httpx

from lease_clause_checker import (
    baseline,
    check,
    closedbook,
    elaws,
    explorer,
    handcheck,
    pipeline,
    quotereview,
    scoreboard,
)
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
from lease_clause_checker.config import Settings
from lease_clause_checker.evaluation import baseline_run, closedbook_run, pipeline_run
from lease_clause_checker.models import DROPPED_MODELS, MODELS, ModelSpec, model_by_key
from lease_clause_checker.providers import Complete, LiveModels, MissingKeyError, OnFirstCall
from lease_clause_checker.report import (
    ModelRun,
    closedbook_table,
    error_lines,
    pipeline_table,
    summary_table,
)
from lease_clause_checker.review import render_review
from lease_clause_checker.rules import RulesFileError, check_rules, load_rules
from lease_clause_checker.runs import RunSummary, StaleRecordError, Store
from lease_clause_checker.statute import (
    Section,
    SectionNotFoundError,
    StatuteParseError,
    parse_sections,
    select_sections,
)
from lease_clause_checker.statute_tools import (
    Statutes,
    UnknownSectionError,
    left_out_note,
    split_citation,
)
from lease_clause_checker.verify import (
    Difference,
    check_quote,
    dropped_negation,
    first_difference,
    normalise,
    subsection_of,
)

DEFAULT_CACHE_DIR = Path(".cache")
DEFAULT_RULES_PATH = Path("data/rules.yaml")
DEFAULT_CLAUSES_PATH = Path("data/clauses.jsonl")
STRESS_PATH = Path("data/stress.jsonl")
STRESS_CANDIDATES_PATH = Path("data/stress_candidates.jsonl")
STRESS_CANDIDATES_RESULTS = Path("results/stress-candidates")
SELECTION_MODEL = "claude-haiku-5-5"
DEFAULT_README = Path("README.md")
DEFAULT_DETAILS = Path(scoreboard.DETAILS)
DEFAULT_CHART = Path("results/scoreboard.svg")
DEFAULT_PROMPTS_DIR = Path("prompts")
DEFAULT_SITE_DIR = Path("site")
# Each frozen set's saved answers have their own directory, so the sets never mix.
RESULTS_DIRS = {
    FROZEN_CLAUSES_SHA256: Path("results"),
    FROZEN_STRESS_SHA256: Path("results/stress"),
}
NOT_LEGAL_ADVICE = "Not legal advice: these are model outputs scored against test labels."
CHECK_NOT_LEGAL_ADVICE = (
    "Not legal advice: a model's reading of these clauses, checked only for quoting the Act "
    "correctly. Ask a lawyer or a legal clinic about a real lease."
)
DEFAULT_CHECK_MODEL = "claude-haiku-5-5"
# `pipeline.PROBLEMS` is worded for the model; this is for a person running `lcc quote check`.
QUOTE_PROBLEMS = pipeline.PROBLEMS | {"no_quote": "The quote is empty."}


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "closedbook" and args.method_command == "quotes":
            return _write_quote_review(args)
        if args.command in ("baseline", "pipeline", "closedbook"):
            specs = [model_by_key(key) for key in args.models]
            if args.command == "closedbook":
                if args.method_command == "run":
                    return _ask_once(args, specs, closedbook.load_prompt(args.prompts))
                return _report_closedbook(args, specs)
            if args.command == "baseline":
                if args.method_command == "run":
                    return _ask_once(args, specs, baseline.load_prompt(args.prompts))
                return _report_baseline(args, specs)
            if args.method_command == "run":
                return _run_pipeline(args, specs)
            return _report_pipeline(args, specs)
        if args.command == "report":
            return _write_report(args)
        if args.command == "site":
            return _write_site(args)
        if args.command == "mcp":
            # Imported here: the MCP SDK takes a moment to load, and only this command needs it.
            from lease_clause_checker.mcp_server import build_server

            build_server(Statutes.load(args.cache_dir)).run("stdio")
            return 0
        if args.command == "check":
            return _check_lease(args)
        if args.command == "quote":
            return _check_quote(args.cache_dir, args.section, args.quote)
        if args.command == "clauses":
            return _review_clauses(args.cache_dir, args.rules, args.clauses, args.out)
        if args.statute_command == "fetch":
            return _fetch(args.cache_dir, refresh=args.refresh)
        if args.statute_command == "check-rules":
            return _check_rules(args.cache_dir, args.rules)
        return _show_sections(args.cache_dir, args.numbers)
    except (
        FileNotFoundError,
        SectionNotFoundError,
        StatuteParseError,
        RulesFileError,
        ClausesFileError,
        elaws.ElawsError,
        httpx.HTTPError,
        MissingKeyError,
        StaleRecordError,
        UnfrozenClausesError,
        scoreboard.ReportDataError,
        scoreboard.MissingMarkersError,
        explorer.SiteExportError,
        quotereview.HandCheckError,
        handcheck.RecordError,
        check.LeaseFormatError,
        UnknownSectionError,
    ) as error:
        print(f"lcc: error: {error}", file=sys.stderr)
        return 1


class UnfrozenClausesError(ValueError):
    """The clauses file is not the frozen, reviewed test set."""


def http_client() -> httpx.Client:
    return httpx.Client()


def live_models(specs: list[ModelSpec]) -> Complete:
    """The real APIs; tests replace this so the default test run never calls them."""
    return LiveModels(Settings(), specs)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="lcc",
        description="Check Ontario lease clauses against the Residential Tenancies Act, 2006. "
        "Not legal advice.",
    )
    parser.add_argument("--cache-dir", type=Path, default=DEFAULT_CACHE_DIR)
    commands = parser.add_subparsers(dest="command", required=True)

    statute = commands.add_parser("statute", help="Residential Tenancies Act, 2006 text")
    statute_commands = statute.add_subparsers(dest="statute_command", required=True)
    fetch = statute_commands.add_parser("fetch", help="download the Act from e-Laws into the cache")
    fetch.add_argument("--refresh", action="store_true", help="re-download even if cached")
    sections = statute_commands.add_parser("sections", help="list cached sections and headings")
    sections.add_argument("numbers", nargs="+", metavar="SECTION", help='e.g. 14 15 "8.1"')
    check_rules = statute_commands.add_parser(
        "check-rules", help="check rules.yaml headings and links against the cached Act"
    )
    check_rules.add_argument("--rules", type=Path, default=DEFAULT_RULES_PATH)

    clauses = commands.add_parser("clauses", help="the synthetic test clauses")
    clauses_commands = clauses.add_subparsers(dest="clauses_command", required=True)
    review = clauses_commands.add_parser(
        "review", help="check a clauses file against the rules and Act; write its review table"
    )
    review.add_argument("--rules", type=Path, default=DEFAULT_RULES_PATH)
    review.add_argument("--clauses", type=Path, default=DEFAULT_CLAUSES_PATH)
    review.add_argument(
        "--out", type=Path, help="default: <clauses file stem>_review.md beside the clauses file"
    )

    lease = commands.add_parser(
        "check", help="run the grounded pipeline on each numbered clause of a lease (paid API)"
    )
    lease.add_argument("lease", type=Path, help="a plain-text lease, e.g. samples/lease_short.txt")
    lease.add_argument(
        "--model",
        choices=[spec.key for spec in MODELS],
        default=DEFAULT_CHECK_MODEL,
        help=f"default: {DEFAULT_CHECK_MODEL}",
    )
    lease.add_argument("--workers", type=_positive_int, default=8)
    lease.add_argument("--color", choices=["auto", "always", "never"], default="auto")
    lease.add_argument("--prompts", type=Path, default=DEFAULT_PROMPTS_DIR)
    lease.add_argument("--rules", type=Path, default=DEFAULT_RULES_PATH)

    quote = commands.add_parser("quote", help="the quote check the pipeline uses (offline)")
    quote_commands = quote.add_subparsers(dest="quote_command", required=True)
    quote_check = quote_commands.add_parser(
        "check",
        help="check that a quote appears word for word in a section of the cached Act; "
        "exits 1 if it doesn't",
    )
    quote_check.add_argument(
        "section", help='a section number, e.g. 14 or "s. 27(2)" (the whole section is searched)'
    )
    quote_check.add_argument("quote", help="the words to look for, in quotation marks")

    report_all = commands.add_parser(
        "report",
        help="regenerate the README's short version, the results in docs/details.md and the chart "
        "from saved answers (offline)",
    )
    report_all.add_argument("--readme", type=Path, default=DEFAULT_README)
    report_all.add_argument("--details", type=Path, default=DEFAULT_DETAILS)
    report_all.add_argument("--chart", type=Path, default=DEFAULT_CHART)
    report_all.add_argument("--prompts", type=Path, default=DEFAULT_PROMPTS_DIR)
    report_all.add_argument("--rules", type=Path, default=DEFAULT_RULES_PATH)

    commands.add_parser(
        "mcp",
        help="run the MCP server (stdio): get_section and verify_quote on the cached Act (offline)",
    )

    site = commands.add_parser(
        "site", help="export the saved answers as the static explorer's page and data (offline)"
    )
    site.add_argument("--out", type=Path, default=DEFAULT_SITE_DIR)
    site.add_argument("--prompts", type=Path, default=DEFAULT_PROMPTS_DIR)
    site.add_argument("--rules", type=Path, default=DEFAULT_RULES_PATH)

    for name, description in (
        ("baseline", "the plain-prompt baseline on the test set"),
        ("pipeline", "the grounded pipeline (statute in context, checked quote) on the test set"),
        ("closedbook", "the pipeline's question with no statute text, the quote checked, no retry"),
    ):
        method = commands.add_parser(name, help=description)
        method_commands = method.add_subparsers(dest="method_command", required=True)
        run = method_commands.add_parser(
            "run", help="ask each model about each clause; saved answers are reused (paid APIs)"
        )
        run.add_argument("--workers", type=_positive_int, default=8)
        report = method_commands.add_parser("report", help="score the saved answers (offline)")
        report.add_argument(
            "--details", action="store_true", help="list every wrong verdict or section"
        )
        for command, choices, help_text in (
            (run, MODELS, "default: all four"),
            (
                report,
                (*MODELS, *DROPPED_MODELS),
                "default: all four; dropped models' saved "
                "answers (gemini-4-carbon) can be scored too",
            ),
        ):
            command.add_argument(
                "--models",
                nargs="+",
                choices=[spec.key for spec in choices],
                default=[spec.key for spec in MODELS],
                metavar="MODEL",
                help=help_text,
            )
            command.add_argument("--clauses", type=Path, default=DEFAULT_CLAUSES_PATH)
            command.add_argument("--prompts", type=Path, default=DEFAULT_PROMPTS_DIR)
            command.add_argument(
                "--results",
                type=Path,
                help="default: results/ for the main set, results/stress/ for the stress set; "
                "required with --draft",
            )
            command.add_argument(
                "--draft",
                action="store_true",
                help="accept a clauses file that is not frozen (only for choosing stress clauses)",
            )
            if name == "pipeline":  # the rule sections are the statute text it is given
                command.add_argument("--rules", type=Path, default=DEFAULT_RULES_PATH)
        if name == "closedbook":
            quotes = method_commands.add_parser(
                "quotes",
                help="write the table of failed quotes, sorted by how they differ from the Act, "
                "for a hand check (offline; main set)",
            )
            quotes.add_argument("--prompts", type=Path, default=DEFAULT_PROMPTS_DIR)
    return parser


def _positive_int(text: str) -> int:
    value = int(text)
    if value < 1:
        raise argparse.ArgumentTypeError(f"must be at least 1, not {value}")
    return value


def _load_clauses(args: argparse.Namespace) -> tuple[list[Clause], str, Path]:
    """The clauses, their file's SHA-256 and their results directory.

    Refused unless the file is frozen or `--draft` is given; a draft's answers must go in a
    directory named with `--results`, apart from every scored set.
    """
    digest = file_sha256(args.clauses)
    if digest not in FROZEN_SETS and not args.draft:
        raise UnfrozenClausesError(
            f"{args.clauses} has SHA-256 {digest}, which is not a frozen test set "
            "(--draft accepts it, for choosing stress clauses)"
        )
    results = args.results or RESULTS_DIRS.get(digest)
    if results is None:
        raise UnfrozenClausesError("--draft needs --results, to keep draft answers apart")
    return load_clauses(args.clauses), digest, results


def _ask_once(args: argparse.Namespace, specs: list[ModelSpec], prompt: baseline.Prompt) -> int:
    """Ask each model about each clause once: the baseline, or the closed-book run."""
    clauses, clauses_sha256, results = _load_clauses(args)
    complete = live_models(specs)
    print(f"Prompt {prompt.version}; {len(clauses)} clauses x {len(specs)} models.")
    summary = baseline.run(
        clauses,
        specs,
        prompt,
        clauses_sha256,
        results,
        complete,
        workers=args.workers,
    )
    return _print_run_summary(summary, results, args.command)


def _report_baseline(args: argparse.Namespace, specs: list[ModelSpec]) -> int:
    clauses, clauses_sha256, results = _load_clauses(args)
    prompt = baseline.load_prompt(args.prompts)
    act_sections = parse_sections(elaws.load_act(elaws.cache_path(args.cache_dir)).content_html)
    runs = [
        baseline_run(results, prompt, spec, clauses, clauses_sha256, act_sections) for spec in specs
    ]
    _print_report_header(prompt.version, args.clauses, clauses, clauses_sha256)
    print()
    print(summary_table(runs, len(clauses)))
    _print_details(args, runs)
    return 0


def _report_closedbook(args: argparse.Namespace, specs: list[ModelSpec]) -> int:
    clauses, clauses_sha256, results = _load_clauses(args)
    prompt = closedbook.load_prompt(args.prompts)
    sections = parse_sections(elaws.load_act(elaws.cache_path(args.cache_dir)).content_html)
    runs = [
        closedbook_run(results, prompt, spec, clauses, clauses_sha256, sections) for spec in specs
    ]
    _print_report_header(prompt.version, args.clauses, clauses, clauses_sha256)
    print("Each quote is checked against the section it cites; the verdict counts either way.\n")
    print(closedbook_table(runs, len(clauses)))
    _print_details(args, runs)
    return 0


def _write_quote_review(args: argparse.Namespace) -> int:
    """The closed-book quotes that are not word for word, sorted and diffed, with their hand
    checks."""
    prompt = closedbook.load_prompt(args.prompts)
    sections = parse_sections(elaws.load_act(elaws.cache_path(args.cache_dir)).content_html)
    if file_sha256(DEFAULT_CLAUSES_PATH) != FROZEN_CLAUSES_SHA256:
        raise UnfrozenClausesError(f"{DEFAULT_CLAUSES_PATH} is not the frozen main set")
    clauses = load_clauses(DEFAULT_CLAUSES_PATH)
    runs = [
        closedbook_run(
            RESULTS_DIRS[FROZEN_CLAUSES_SHA256],
            prompt,
            spec,
            clauses,
            FROZEN_CLAUSES_SHA256,
            sections,
        )
        for spec in MODELS
    ]
    folder = RESULTS_DIRS[FROZEN_CLAUSES_SHA256] / prompt.version
    checks = quotereview.write_review(runs, folder, elaws.NOTICE, prompt.version)
    print(quotereview.summary_table(runs))
    print(f"\n{quotereview.hand_check_line(runs, checks)}")
    print(f"Wrote {folder / quotereview.REVIEW_FILE}; hand checks in {quotereview.CHECKS_FILE}.")
    print(NOT_LEGAL_ADVICE)
    return 0


def _run_pipeline(args: argparse.Namespace, specs: list[ModelSpec]) -> int:
    clauses, clauses_sha256, results = _load_clauses(args)
    prompt, sections = _pipeline_prompt(args)
    complete = live_models(specs)
    print(f"Prompt {prompt.version}; {len(clauses)} clauses x {len(specs)} models.")
    store = Store(results, prompt.version, clauses_sha256)
    summary = pipeline.run(clauses, specs, prompt, sections, store, complete, workers=args.workers)
    return _print_run_summary(summary, results, "pipeline")


def _report_pipeline(args: argparse.Namespace, specs: list[ModelSpec]) -> int:
    clauses, clauses_sha256, results = _load_clauses(args)
    prompt, sections = _pipeline_prompt(args)
    store = Store(results, prompt.version, clauses_sha256)
    runs = [pipeline_run(store, prompt, spec, clauses, sections) for spec in specs]
    _print_report_header(prompt.version, args.clauses, clauses, clauses_sha256)
    print("unsure includes gave up: answers whose quote failed the check twice.\n")
    print(pipeline_table(runs, len(clauses)))
    _print_details(args, runs)
    return 0


def _write_report(args: argparse.Namespace) -> int:
    """Score every saved answer and rewrite the README's short version, the results and TL;DR
    blocks in the details page, and the chart."""
    board, _, _ = _load_board(args)
    readme = args.readme.read_text(encoding="utf-8")
    readme = scoreboard.replace_block(
        readme,
        scoreboard.render_short(board, elaws.NOTICE),
        scoreboard.SHORT_START,
        scoreboard.SHORT_END,
    )
    args.readme.write_text(readme, encoding="utf-8")
    # The details page is one folder down, so its links to repo files need a prefix.
    prefix = Path(os.path.relpath(".", args.details.parent)).as_posix() + "/"
    chart_link = Path(os.path.relpath(args.chart, args.details.parent)).as_posix()
    details = args.details.read_text(encoding="utf-8")
    details = scoreboard.replace_block(
        details, scoreboard.relink(scoreboard.render(board, chart_link), prefix)
    )
    details = scoreboard.replace_block(
        details,
        scoreboard.relink(scoreboard.render_tldr(board), prefix),
        scoreboard.TLDR_START,
        scoreboard.TLDR_END,
    )
    args.details.write_text(details, encoding="utf-8")
    args.chart.write_text(scoreboard.render_chart(board.main), encoding="utf-8")
    print(f"Wrote {args.readme}, the results in {args.details} and the chart {args.chart}.")
    print(NOT_LEGAL_ADVICE)
    return 0


def _write_site(args: argparse.Namespace) -> int:
    """Export the scored answers and the subsections they quote for the static explorer."""
    board, sections, versions = _load_board(args)
    act = elaws.load_act(elaws.cache_path(args.cache_dir))
    closed_book = RESULTS_DIRS[FROZEN_CLAUSES_SHA256] / versions["closedbook"]
    data = explorer.build(
        board, sections, act, versions, len(load_rules(args.rules)), closed_book.as_posix()
    )
    sets = {"main": board.main.clauses, "stress": board.stress.clauses}
    folders = {
        ("main", "baseline"): RESULTS_DIRS[FROZEN_CLAUSES_SHA256] / versions["baseline"],
        ("main", "pipeline"): RESULTS_DIRS[FROZEN_CLAUSES_SHA256] / versions["pipeline"],
        ("main", "closedbook"): RESULTS_DIRS[FROZEN_CLAUSES_SHA256] / versions["closedbook"],
        ("stress", "baseline"): RESULTS_DIRS[FROZEN_STRESS_SHA256] / versions["baseline"],
        ("stress", "pipeline"): RESULTS_DIRS[FROZEN_STRESS_SHA256] / versions["pipeline"],
    }
    explorer.check_counts(data, explorer.saved_counts(folders, sets))
    explorer.write(data, args.out)
    quoted = sum(len(s["subsections"]) for s in data["act"]["sections"].values())
    print(
        f"Wrote the explorer to {args.out}: {len(data['clauses'])} clauses, "
        f"{len(data['answers'])} answers ({len(data['models'])} models' saved answers on the main "
        f"and stress sets) and {quoted} quoted subsections of the Act."
    )
    print(NOT_LEGAL_ADVICE)
    return 0


def _load_board(
    args: argparse.Namespace,
) -> tuple[scoreboard.Board, dict[str, Section], dict[str, str]]:
    """Every saved answer, scored; the Act's sections; and each method's prompt version."""
    pipeline_prompt, sections = _pipeline_prompt(args)
    prompts = (baseline.load_prompt(args.prompts), pipeline_prompt)

    def scored(path: Path, sha256: str) -> scoreboard.ScoredSet:
        if file_sha256(path) != sha256:
            raise UnfrozenClausesError(f"{path} is not the frozen set with SHA-256 {sha256}")
        return scoreboard.score_set(
            load_clauses(path), sha256, RESULTS_DIRS[sha256], prompts, sections
        )

    main_set = scored(DEFAULT_CLAUSES_PATH, FROZEN_CLAUSES_SHA256)
    stress_set = scored(STRESS_PATH, FROZEN_STRESS_SHA256)
    selection = baseline_run(
        STRESS_CANDIDATES_RESULTS,
        prompts[0],
        model_by_key(SELECTION_MODEL),
        load_clauses(STRESS_CANDIDATES_PATH),
        file_sha256(STRESS_CANDIDATES_PATH),
        sections,
    )
    closed_book_prompt = closedbook.load_prompt(args.prompts)
    closed_book = {
        spec.key: closedbook_run(
            RESULTS_DIRS[FROZEN_CLAUSES_SHA256],
            closed_book_prompt,
            spec,
            main_set.clauses,
            FROZEN_CLAUSES_SHA256,
            sections,
        )
        for spec in MODELS
    }
    closed_book_runs = {key: run for key, run in closed_book.items() if run.scores}
    checks: quotereview.HandChecks = {}
    record = None
    if len(closed_book_runs) == len(MODELS):
        folder = RESULTS_DIRS[FROZEN_CLAUSES_SHA256] / closed_book_prompt.version
        runs = list(closed_book_runs.values())
        checks = quotereview.write_review(runs, folder, elaws.NOTICE, closed_book_prompt.version)
        record = handcheck.load_record(folder / handcheck.RECORD_DIR)
        if record is not None:
            record.check_against(checks, quotereview.failed_categories(runs))
    board = scoreboard.Board(
        main_set,
        stress_set,
        scoreboard.restrict(selection, stress_set.clauses),
        closed_book_runs,
        checks,
        record,
        sections,
    )
    versions = {
        "baseline": prompts[0].version,
        "pipeline": pipeline_prompt.version,
        "closedbook": closed_book_prompt.version,
    }
    return board, sections, versions


def _check_lease(args: argparse.Namespace) -> int:
    started = time.monotonic()
    text = args.lease.read_text(encoding="utf-8-sig")
    lease = check.split_lease(text)
    clauses = lease.clauses
    spec = model_by_key(args.model)
    prompt, sections = _pipeline_prompt(args)
    store = check.store_for(text, args.cache_dir, prompt.version)
    findings, summary = check.check(
        clauses,
        spec,
        prompt,
        sections,
        store,
        OnFirstCall(lambda: live_models([spec])),  # a replay of saved answers needs no key
        workers=args.workers,
    )
    color = check.use_color(args.color, sys.stdout.isatty())
    width = check.terminal_width()
    rules = len(load_rules(args.rules))
    intro = (
        f"{spec.name} with the grounded pipeline: the text of {rules} sections of the "
        "Residential Tenancies Act, 2006 (Ontario) in the prompt, and every quote checked "
        "against that text. Clauses outside those sections may get a wrong or unsure answer."
    )
    print(f"Lease Clause Checker: {args.lease}")
    print(textwrap.fill(intro, width))
    print()
    if lease.skipped:
        shown = ", ".join(f"“{textwrap.shorten(p, 40, placeholder='…')}”" for p in lease.skipped)
        print(textwrap.fill(f"Not checked (no clause number): {shown}", width))
        print()
    print(check.render(findings, clauses, sections, color=color, width=width))
    cost = sum(
        spec.cost_usd(a.record.input_tokens, a.record.output_tokens, a.record.requested_at.date())
        for finding in findings
        for a in finding.attempts
    )
    print(
        f"{summary.called} new calls, {summary.cached} saved answers reused; "
        f"${cost:.4f} for all the answers shown; {time.monotonic() - started:.1f} s."
    )
    for failure in summary.failures:
        print(f"lcc: failed: {failure}", file=sys.stderr)
    print(textwrap.fill(CHECK_NOT_LEGAL_ADVICE, width))
    return 1 if summary.failures else 0


def _pipeline_prompt(
    args: argparse.Namespace,
) -> tuple[pipeline.PipelinePrompt, dict[str, Section]]:
    """The pipeline prompt with the text of every rule section, and the Act's sections."""
    act = elaws.load_act(elaws.cache_path(args.cache_dir))
    sections = parse_sections(act.content_html)
    rule_sections = select_sections(sections, [rule.section for rule in load_rules(args.rules)])
    prompt = pipeline.load_pipeline_prompt(args.prompts, rule_sections)
    return prompt, sections


def _print_run_summary(summary: RunSummary, results: Path, method: str) -> int:
    print(f"{summary.called} new answers, {summary.cached} already saved in {results}.")
    for failure in summary.failures:
        print(f"lcc: failed: {failure}", file=sys.stderr)
    if summary.failures:
        print(f"{len(summary.failures)} calls failed; run again to retry them.", file=sys.stderr)
    print(f"Score them with `lcc {method} report`. " + NOT_LEGAL_ADVICE)
    return 1 if summary.failures else 0


def _print_report_header(
    version: str, path: Path, clauses: list[Clause], clauses_sha256: str
) -> None:
    void_count = sum(clause.label == "void" for clause in clauses)
    frozen = "frozen" if clauses_sha256 in FROZEN_SETS else "draft labels (not frozen),"
    print(
        f"Prompt {version} on {path} ({len(clauses)} clauses: {void_count} void, "
        f"{len(clauses) - void_count} allowed; {frozen} SHA-256 {clauses_sha256[:12]}…)"
    )
    print("section ok = the deciding section or an accepted alternative; exact = deciding only.")


def _print_details(args: argparse.Namespace, runs: list[ModelRun]) -> None:
    if args.details:
        for model_run in runs:
            print(f"\n{model_run.spec.key}:")
            for line in error_lines(model_run):
                print(f"  {line}")
    print("\n" + NOT_LEGAL_ADVICE)


def _fetch(cache_dir: Path, *, refresh: bool) -> int:
    path = elaws.cache_path(cache_dir)
    download = refresh or not path.exists()
    if download:
        with http_client() as client:
            act = elaws.fetch_act(client)
    else:
        act = elaws.load_act(path)
    # Parse before saving, so text the parser can't read is never cached.
    section_count = len(parse_sections(act.content_html))
    if download:
        elaws.save_act(act, path)
        print(f"Cached at {path}")
    else:
        print(f"Already cached at {path}; use --refresh to download again.")
    print(f"{act.title}: {section_count} sections")
    print(f"Retrieved {act.retrieved_at:%Y-%m-%d %H:%M} UTC from {act.source_url}")
    print(f"Content SHA-256 {act.content_sha256}")
    return 0


def _show_sections(cache_dir: Path, numbers: Sequence[str]) -> int:
    act = elaws.load_act(elaws.cache_path(cache_dir))
    _print_provenance(act)
    _print_headings(select_sections(parse_sections(act.content_html), numbers))
    return 0


def _check_quote(cache_dir: Path, section_arg: str, quote: str) -> int:
    """The pipeline's quote check on one quote, saying where a failed quote leaves the Act."""
    act = elaws.load_act(elaws.cache_path(cache_dir))
    sections = parse_sections(act.content_html)
    section, given = split_citation(section_arg)
    result = check_quote(quote, section, sections)
    # The MCP tool's reading of the same quote, for what the check alone doesn't say.
    tool = Statutes(act, sections).verify_quote(act.title, section_arg, quote)
    if result == "verified":
        found = subsection_of(normalise(quote).strip('"'), sections[section].text) or ""
        print(f"VERIFIED: the quote appears word for word in s. {section}{found}.")
        if given and given != found:
            print(f"Note: you gave s. {section}{given}; the check searches all of s. {section}.")
    else:
        print(f"NOT VERIFIED: {QUOTE_PROBLEMS[result].format(section=section)}")
    if result == "not_found":
        difference = first_difference(quote, sections[section].text)
        if difference is not None:
            print(_difference_line(difference, section, sections[section].text))
    if result == "drops_negation":
        word = dropped_negation(normalise(quote).strip('"'), sections[section].text)
        print(f"The Act has “{word}” right before the quote, and the quote leaves it out.")
    left_out = left_out_note(
        tool.left_out_before, tool.left_out_after, tool.left_out_in, warned=bool(tool.warning)
    )
    for line in (left_out, tool.warning):
        if line:
            print(textwrap.fill(line, check.terminal_width()))
    print(elaws.NOTICE)
    print("Not legal advice: this checks the wording only, not what it means.")
    return 0 if result == "verified" else 1


def _difference_line(difference: Difference, section: str, section_text: str) -> str:
    if difference.matched == 0:
        line = f"The quote's first word, “{difference.quote_word}”, is not in s. {section}"
        if difference.quote_word.lower() in normalise(section_text).lower().split():
            return f"{line} with that capitalisation (the check is case-sensitive)."
        return f"{line}."
    words = "word matches" if difference.matched == 1 else "words match"
    line = (
        f"The first {difference.matched} {words} s. {section}{difference.subsection}; then the "
        f"quote has “{difference.quote_word}”"
    )
    if difference.act_word is None:
        return f"{line} where the subsection ends."
    return f"{line} where the Act has “{difference.act_word}”."


def _check_rules(cache_dir: Path, rules_path: Path) -> int:
    act = elaws.load_act(elaws.cache_path(cache_dir))
    sections = parse_sections(act.content_html)
    rules = load_rules(rules_path)
    problems = check_rules(rules, sections)
    _print_provenance(act)
    if problems:
        for problem in problems:
            print(f"lcc: {rules_path}: {problem}", file=sys.stderr)
        return 1
    _print_headings([sections[rule.section] for rule in rules])
    print(f"\nAll {len(rules)} rules in {rules_path} match the cached Act.")
    return 0


def _review_clauses(cache_dir: Path, rules_path: Path, clauses_path: Path, out: Path | None) -> int:
    out = out or clauses_path.with_name(f"{clauses_path.stem}_review.md")
    act = elaws.load_act(elaws.cache_path(cache_dir))
    sections = parse_sections(act.content_html)
    rules = load_rules(rules_path)
    clauses = load_clauses(clauses_path)
    problems = [f"{rules_path}: {problem}" for problem in check_rules(rules, sections)]
    problems += [f"{clauses_path}: {p}" for p in check_clauses(clauses, rules, sections)]
    _print_provenance(act)
    if problems:
        for problem in problems:
            print(f"lcc: {problem}", file=sys.stderr)
        return 1
    review = render_review(clauses, clauses_path, file_sha256(clauses_path), act)
    out.write_text(review, encoding="utf-8")
    print(f"All {len(clauses)} clauses in {clauses_path} cite a rule and quote its section.")
    print(f"Wrote {out}. Labels are not legal advice.")
    return 0


def _print_provenance(act: elaws.CachedAct) -> None:
    print(act.title)
    print(f"{act.consolidation} Retrieved {act.retrieved_at:%Y-%m-%d} from {act.source_url}")
    print(f"Content SHA-256 {act.content_sha256}")
    print(elaws.NOTICE)
    print()


def _print_headings(sections: Sequence[Section]) -> None:
    width = max(len("Section"), *(len(section.number) for section in sections))
    print(f"{'Section':<{width}}  Heading (as shown on e-Laws)")
    for section in sections:
        print(f"{section.number:<{width}}  {section.heading}")
