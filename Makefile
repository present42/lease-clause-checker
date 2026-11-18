# Single source of truth for checks; CI runs these same targets.
.PHONY: check lint typecheck test test-js format report og

check: lint typecheck test

lint:
	uv run --locked ruff check .
	uv run --locked ruff format --check .

typecheck:
	uv run --locked mypy

test:
	uv run --locked pytest

# The explorer's JavaScript tests, in Node's built-in runner, with the Misquotes counts checked
# against site/data.json (`uv run lcc site`). `make test` runs them on a mini export.
test-js:
	node --test tests/js/*.test.mjs

format:
	uv run --locked ruff check --fix .
	uv run --locked ruff format .

# Regenerates the README's short version, docs/details.md's results and the chart from saved
# answers; needs `uv run lcc statute fetch`.
report:
	uv run --locked lcc report

# Remakes the explorer's preview image from its #/card view: exports the page to build/og/
# (gitignored; each export replaces the last), serves it locally and has headless Chrome save a
# 1200x630 screenshot. Needs the cached Act and Google Chrome (set CHROME elsewhere than
# macOS). Commit the image it writes.
CHROME ?= /Applications/Google Chrome.app/Contents/MacOS/Google Chrome
OG_IMAGE := src/lease_clause_checker/explorer_assets/og.png
OG_PORT ?= 8799

og:
	uv run --locked lcc site --out build/og/site > /dev/null
	@if curl -s -o /dev/null http://127.0.0.1:$(OG_PORT)/; then \
		echo "port $(OG_PORT) is in use: set OG_PORT to a free one" >&2; exit 1; fi
	@set -e; rm -f build/og/og.png; \
	python3 -m http.server $(OG_PORT) --bind 127.0.0.1 --directory build/og/site > /dev/null 2>&1 & \
	server=$$!; trap 'kill $$server' EXIT; \
	for i in 1 2 3 4 5 6 7 8 9 10; do \
		curl -sf -o /dev/null http://127.0.0.1:$(OG_PORT)/data.json && break; sleep 0.5; done; \
	curl -sf -o /dev/null http://127.0.0.1:$(OG_PORT)/data.json; \
	"$(CHROME)" --headless=new --disable-gpu --hide-scrollbars --user-data-dir=build/og/chrome \
		--window-size=1200,630 --virtual-time-budget=4000 --screenshot="$(CURDIR)/build/og/og.png" \
		"http://127.0.0.1:$(OG_PORT)/index.html#/card" > /dev/null 2>&1; \
	test -s build/og/og.png; \
	mv build/og/og.png $(OG_IMAGE); \
	echo "Wrote $(OG_IMAGE)"
