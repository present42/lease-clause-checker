"""One call to a model, through each provider's official SDK, as a provider-neutral result.

Requests are built as plain dicts, sent as they are, and saved next to the response. An
optional JSON Schema asks the provider for structured output in that shape. Refusal
fallbacks to other models are deliberately not enabled: an answer must come from the model
being scored.
"""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import anthropic
from google import genai
from google.genai import types

from lease_clause_checker.config import Settings
from lease_clause_checker.models import CLAUDE_EFFORT, MAX_OUTPUT_TOKENS, ModelSpec

API_RETRIES = 4
# Vertex AI serves the newest Gemini models from its global endpoint only.
VERTEX_LOCATION = "global"


class MissingKeyError(RuntimeError):
    """The API key for a provider is not set."""


@dataclass(frozen=True)
class Completion:
    text: str
    """The visible answer, without thinking."""
    served_model: str
    finish_reason: str
    input_tokens: int
    output_tokens: int
    """Billed output tokens, thinking included."""
    raw: dict[str, Any]


Complete = Callable[[ModelSpec, dict[str, Any]], Completion]
"""Send `spec` one request, built by `request_for`, and return its answer."""


def anthropic_request(
    spec: ModelSpec, prompt: str, schema: dict[str, Any] | None = None
) -> dict[str, Any]:
    output_config: dict[str, Any] = {"effort": CLAUDE_EFFORT}
    if schema is not None:
        output_config["format"] = {"type": "json_schema", "schema": schema}
    return {
        "model": spec.model_id,
        "max_tokens": MAX_OUTPUT_TOKENS,
        "output_config": output_config,
        "messages": [{"role": "user", "content": prompt}],
    }


def gemini_request(
    spec: ModelSpec, prompt: str, schema: dict[str, Any] | None = None
) -> dict[str, Any]:
    config: dict[str, Any] = {"max_output_tokens": MAX_OUTPUT_TOKENS}
    if schema is not None:
        config |= {"response_mime_type": "application/json", "response_json_schema": schema}
    return {"model": spec.model_id, "contents": prompt, "config": config}


def request_for(
    spec: ModelSpec, prompt: str, schema: dict[str, Any] | None = None
) -> dict[str, Any]:
    """The request asking `spec` the one-message `prompt`, answered in `schema`'s shape if given."""
    if spec.provider == "anthropic":
        return anthropic_request(spec, prompt, schema)
    return gemini_request(spec, prompt, schema)


def from_anthropic(message: anthropic.types.Message) -> Completion:
    return Completion(
        text="".join(block.text for block in message.content if block.type == "text"),
        served_model=message.model,
        finish_reason=str(message.stop_reason),
        input_tokens=message.usage.input_tokens,
        output_tokens=message.usage.output_tokens,
        raw=message.model_dump(mode="json"),
    )


def from_gemini(response: types.GenerateContentResponse) -> Completion:
    candidate = response.candidates[0] if response.candidates else None
    parts = candidate.content.parts if candidate and candidate.content else None
    usage = response.usage_metadata
    return Completion(
        text="".join(part.text for part in parts or [] if part.text and not part.thought),
        served_model=response.model_version or "",
        finish_reason=_gemini_finish_reason(candidate),
        input_tokens=(usage.prompt_token_count or 0) if usage else 0,
        output_tokens=(
            (usage.candidates_token_count or 0) + (usage.thoughts_token_count or 0) if usage else 0
        ),
        raw=response.model_dump(mode="json", exclude_none=True),
    )


def _gemini_finish_reason(candidate: types.Candidate | None) -> str:
    if candidate is None:
        return "no candidate"
    return candidate.finish_reason.name if candidate.finish_reason else "unknown"


class LiveModels:
    """Calls the real APIs. Costs money; never used by the default test run.

    Clients are created up front, so a missing key fails before any call and worker
    threads share ready clients.
    """

    def __init__(self, settings: Settings, specs: list[ModelSpec]) -> None:
        providers = {spec.provider for spec in specs}
        self._anthropic: anthropic.Anthropic | None = None
        self._gemini: genai.Client | None = None
        if "anthropic" in providers:
            if settings.anthropic_api_key is None:
                raise MissingKeyError("ANTHROPIC_API_KEY is not set (see .env.example)")
            self._anthropic = anthropic.Anthropic(
                api_key=settings.anthropic_api_key.get_secret_value(), max_retries=API_RETRIES
            )
        if "google" in providers:
            if settings.gemini_api_key is None:
                raise MissingKeyError("GEMINI_API_KEY is not set (see .env.example)")
            retries = types.HttpOptions(
                retry_options=types.HttpRetryOptions(attempts=API_RETRIES + 1)
            )
            key = settings.gemini_api_key.get_secret_value()
            if settings.google_cloud_project is None:
                self._gemini = genai.Client(api_key=key, http_options=retries)
            else:
                self._gemini = genai.Client(
                    vertexai=True,
                    api_key=key,
                    project=settings.google_cloud_project,
                    location=VERTEX_LOCATION,
                    http_options=retries,
                )

    def __call__(self, spec: ModelSpec, request: dict[str, Any]) -> Completion:
        if spec.provider == "anthropic":
            if self._anthropic is None:
                raise ValueError(f"{spec.key} was not among the models passed to LiveModels")
            return from_anthropic(self._anthropic.messages.create(**request))
        if self._gemini is None:
            raise ValueError(f"{spec.key} was not among the models passed to LiveModels")
        response = self._gemini.models.generate_content(
            model=request["model"],
            contents=request["contents"],
            config=types.GenerateContentConfig(**request["config"]),
        )
        return from_gemini(response)
