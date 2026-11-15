# Single source of truth for checks; CI runs these same targets.
.PHONY: check lint typecheck test format report

check: lint typecheck test

lint:
	uv run --locked ruff check .
	uv run --locked ruff format --check .

typecheck:
	uv run --locked mypy

test:
	uv run --locked pytest

format:
	uv run --locked ruff check --fix .
	uv run --locked ruff format .

# Regenerates the README's short version, docs/details.md's results and the chart from saved
# answers; needs `uv run lcc statute fetch`.
report:
	uv run --locked lcc report
