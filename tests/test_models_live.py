"""Each model accepts the pipeline's structured-output request. Costs well under one cent per
model; runs only with `uv run pytest -m live`, and skips a provider whose key isn't set."""

import pytest

from lease_clause_checker.config import Settings
from lease_clause_checker.models import MODELS, ModelSpec
from lease_clause_checker.pipeline import ANSWER_SCHEMA, PipelineAnswer
from lease_clause_checker.providers import LiveModels, request_for

pytestmark = pytest.mark.live

PROMPT = (
    'Section 1 of a made-up Act says: "A provision in a tenancy agreement prohibiting widgets '
    'is void." Is the clause "The tenant shall keep no widgets." void under section 1? Answer '
    "with a verdict (void, allowed or unsure), the section number, a quote of at least five "
    "words from the section, and a one-sentence reason."
)


@pytest.mark.parametrize("spec", MODELS, ids=[spec.key for spec in MODELS])
def test_each_model_answers_in_the_pipeline_schema(spec: ModelSpec) -> None:
    settings = Settings()
    key = settings.anthropic_api_key if spec.provider == "anthropic" else settings.gemini_api_key
    if key is None:
        pytest.skip(f"no API key for {spec.provider}")

    completion = LiveModels(settings, [spec])(spec, request_for(spec, PROMPT, ANSWER_SCHEMA))

    answer = PipelineAnswer.model_validate_json(completion.text)
    assert answer.verdict == "void"
    assert answer.cited_section == "1"
