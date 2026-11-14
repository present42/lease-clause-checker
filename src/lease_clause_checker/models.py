"""The models under test: a light and a heavy model from each provider, paired by tier.

Model ids were checked against each provider's models API on 11 November 2026 (Gemini 4 Argon
on 13 November). Prices are standard paid-tier rates in USD per million tokens, read from the
pages in `price_source` on the same days, including the announced Gemini price rises.
Thinking tokens are billed as output tokens by both providers.

None of these models accepts a custom temperature: Claude Opus 5.5 and Haiku 5.5 reject any
non-default value, and Gemini 4 and Gemini 3.6+ ignore it. Every model therefore runs with its
provider's default sampling, and reproducibility comes from saving every raw output instead.
"""

from dataclasses import dataclass
from datetime import date
from typing import Literal

ANTHROPIC_PRICING = "https://platform.claude.com/docs/en/about-claude/pricing"
GEMINI_PRICING = "https://aistudio.google.com/docs/pricing"

# Room for thinking plus a short answer; thinking counts against this cap on both APIs.
MAX_OUTPUT_TOKENS = 16_000
# Claude's thinking depth. "medium" is the default on both Claude models; it is set
# explicitly so a change of default can't silently change a run.
CLAUDE_EFFORT = "medium"


@dataclass(frozen=True)
class Price:
    since: date
    """The first day (UTC) the price applies; `date.min` for the earliest price known."""
    input_usd_per_mtok: float
    output_usd_per_mtok: float


@dataclass(frozen=True)
class ModelSpec:
    key: str
    """Short name used in file paths and tables."""
    name: str
    """The model's name as its provider writes it, for reports."""
    provider: Literal["anthropic", "google"]
    model_id: str
    weight: Literal["light", "heavy"]
    prices: tuple[Price, ...]
    """In date order; a call costs the price in force on the day it was made."""
    price_source: str
    price_note: str = ""

    def price_on(self, day: date) -> Price:
        return [price for price in self.prices if price.since <= day][-1]

    def cost_usd(self, input_tokens: int, output_tokens: int, day: date) -> float:
        price = self.price_on(day)
        return (
            input_tokens * price.input_usd_per_mtok + output_tokens * price.output_usd_per_mtok
        ) / 1_000_000


MODELS = (
    ModelSpec(
        key="claude-haiku-5-5",
        name="Claude Haiku 5.5",
        provider="anthropic",
        model_id="claude-haiku-5-5",
        weight="light",
        prices=(Price(date.min, 0.10, 0.50),),
        price_source=ANTHROPIC_PRICING,
        price_note="rate for prompts up to 100,000 tokens",
    ),
    ModelSpec(
        key="claude-opus-5-5",
        name="Claude Opus 5.5",
        provider="anthropic",
        model_id="claude-opus-5-5",
        weight="heavy",
        prices=(Price(date.min, 4.00, 20.00),),
        price_source=ANTHROPIC_PRICING,
    ),
    ModelSpec(
        key="gemini-3.8-flash",
        name="Gemini 3.8 Flash",
        provider="google",
        model_id="gemini-3.8-flash",
        weight="light",
        prices=(Price(date.min, 0.75, 3.75), Price(date(2027, 1, 1), 1.50, 7.50)),
        price_source=GEMINI_PRICING,
        price_note="rate through 31 Dec 2026; $1.50 / $7.50 from 1 Jan 2027",
    ),
    ModelSpec(
        key="gemini-4-argon",
        name="Gemini 4 Argon",
        provider="google",
        model_id="gemini-4-argon",
        weight="heavy",
        prices=(Price(date.min, 2.00, 10.00), Price(date(2026, 12, 1), 4.00, 20.00)),
        price_source=GEMINI_PRICING,
        price_note="rate through 30 Nov 2026; $4 / $20 from 1 Dec 2026",
    ),
)

DROPPED_MODELS = (
    ModelSpec(
        key="gemini-4-carbon",
        name="Gemini 4 Carbon",
        provider="google",
        model_id="gemini-4-carbon",
        weight="heavy",
        prices=(Price(date.min, 2.00, 10.00), Price(date(2026, 12, 1), 4.00, 20.00)),
        price_source=GEMINI_PRICING,
        price_note="rate through 30 Nov 2026; $4 / $20 from 1 Dec 2026",
    ),
)
"""Run first, then dropped as a top-tier model (Claude Opus 5.5's tier peer is Gemini 4 Argon).
Their saved answers are kept and can be re-scored by key, but no default run, table or chart
includes them."""


def model_by_key(key: str) -> ModelSpec:
    for spec in (*MODELS, *DROPPED_MODELS):
        if spec.key == key:
            return spec
    known = ", ".join(spec.key for spec in (*MODELS, *DROPPED_MODELS))
    raise KeyError(f"unknown model {key!r}; known models: {known}")
