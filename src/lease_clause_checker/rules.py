"""The rules this project tests: which RTA sections, paraphrased, with links to e-Laws.

`data/rules.yaml` is hand-written. `check_rules` compares it with the cached Act, so a
re-numbered section or changed heading on e-Laws is caught rather than silently trusted.
"""

from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, ValidationError

from lease_clause_checker.elaws import section_url
from lease_clause_checker.fields import OneLine, SectionNumber
from lease_clause_checker.statute import Section


class Rule(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    section: SectionNumber
    heading: OneLine
    """The section's headnote exactly as e-Laws shows it."""
    paraphrase: OneLine
    """One plain-English line written for this project. Not statute text; not legal advice."""
    source: OneLine


class RulesFileError(ValueError):
    """rules.yaml is malformed."""


def load_rules(path: Path) -> list[Rule]:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as error:
        raise RulesFileError(f"{path}: not valid YAML: {error}") from error
    if not isinstance(data, dict) or not isinstance(data.get("rules"), list) or not data["rules"]:
        raise RulesFileError(f"{path}: expected a non-empty top-level `rules:` list")
    rules = [_parse_rule(path, entry) for entry in data["rules"]]
    numbers = [rule.section for rule in rules]
    duplicates = sorted({number for number in numbers if numbers.count(number) > 1})
    if duplicates:
        raise RulesFileError(f"{path}: section(s) listed more than once: {', '.join(duplicates)}")
    return rules


def check_rules(rules: list[Rule], sections: dict[str, Section]) -> list[str]:
    """Describe every way the rules disagree with the parsed Act; empty means they agree."""
    problems = []
    for rule in rules:
        section = sections.get(rule.section)
        if section is None:
            problems.append(f"s. {rule.section}: not in the Act")
            continue
        if rule.heading != section.heading:
            problems.append(
                f"s. {rule.section}: heading {rule.heading!r}, e-Laws has {section.heading!r}"
            )
        expected_source = section_url(section.anchor)
        if rule.source != expected_source:
            problems.append(f"s. {rule.section}: source {rule.source}, expected {expected_source}")
    return problems


def _parse_rule(path: Path, entry: object) -> Rule:
    if isinstance(entry, dict) and "section" in entry and not isinstance(entry["section"], str):
        # YAML reads an unquoted 14.10 as the float 14.1, so numbers must be strings.
        raise RulesFileError(
            f'{path}: section {entry.get("section")!r} must be quoted, e.g. section: "14"'
        )
    try:
        return Rule.model_validate(entry)
    except ValidationError as error:
        raise RulesFileError(f"{path}: {error}") from error
