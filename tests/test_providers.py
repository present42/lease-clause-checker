from datetime import date
from typing import Any
from unittest.mock import patch

import anthropic
import pytest
from google.genai import types
from pydantic import SecretStr

from lease_clause_checker.config import Settings
from lease_clause_checker.models import MODELS, model_by_key
from lease_clause_checker.providers import (
    API_RETRIES,
    Complete,
    Completion,
    LiveModels,
    MissingKeyError,
    OnFirstCall,
    anthropic_request,
    from_anthropic,
    from_gemini,
    gemini_request,
    request_for,
)

HAIKU = model_by_key("claude-haiku-5-5")
FLASH = model_by_key("gemini-3.8-flash")


def test_requests_pin_the_settings_and_send_no_sampling_parameters() -> None:
    claude = anthropic_request(HAIKU, "Q?")
    gemini = gemini_request(FLASH, "Q?")

    assert claude == {
        "model": "claude-haiku-5-5",
        "max_tokens": 16_000,
        "output_config": {"effort": "medium"},
        "messages": [{"role": "user", "content": "Q?"}],
    }
    assert gemini == {
        "model": "gemini-3.8-flash",
        "contents": "Q?",
        "config": {"max_output_tokens": 16_000},
    }


def test_a_schema_asks_each_provider_for_json_in_that_shape() -> None:
    schema = {"type": "object", "properties": {"verdict": {"type": "string"}}}

    claude = request_for(HAIKU, "Q?", schema)
    gemini = request_for(FLASH, "Q?", schema)

    assert claude["output_config"] == {
        "effort": "medium",
        "format": {"type": "json_schema", "schema": schema},
    }
    assert gemini["config"] == {
        "max_output_tokens": 16_000,
        "response_mime_type": "application/json",
        "response_json_schema": schema,
    }
    assert request_for(HAIKU, "Q?") == anthropic_request(HAIKU, "Q?")
    assert request_for(FLASH, "Q?") == gemini_request(FLASH, "Q?")


def test_from_anthropic_keeps_the_answer_not_the_thinking() -> None:
    message = anthropic.types.Message.model_validate(
        {
            "id": "msg_1",
            "type": "message",
            "role": "assistant",
            "model": "claude-haiku-5-5",
            "content": [
                {"type": "thinking", "thinking": "", "signature": "sig"},
                {"type": "text", "text": "Verdict: enforceable\n"},
                {"type": "text", "text": "Section: 14"},
            ],
            "stop_reason": "end_turn",
            "stop_sequence": None,
            "usage": {"input_tokens": 200, "output_tokens": 900},
        }
    )

    completion = from_anthropic(message)

    assert completion.text == "Verdict: enforceable\nSection: 14"
    assert (completion.input_tokens, completion.output_tokens) == (200, 900)
    assert (completion.served_model, completion.finish_reason) == ("claude-haiku-5-5", "end_turn")
    assert completion.raw["content"][0]["type"] == "thinking"


def test_from_gemini_bills_thinking_as_output_and_drops_thought_parts() -> None:
    response = types.GenerateContentResponse.model_validate(
        {
            "candidates": [
                {
                    "content": {
                        "role": "model",
                        "parts": [
                            {"text": "Let me think.", "thought": True},
                            {"text": "Verdict: not enforceable\nSection: 14"},
                        ],
                    },
                    "finish_reason": "STOP",
                }
            ],
            "usage_metadata": {
                "prompt_token_count": 200,
                "candidates_token_count": 50,
                "thoughts_token_count": 700,
            },
            "model_version": "gemini-3.8-flash",
        }
    )

    completion = from_gemini(response)

    assert completion.text == "Verdict: not enforceable\nSection: 14"
    assert (completion.input_tokens, completion.output_tokens) == (200, 750)
    assert (completion.served_model, completion.finish_reason) == ("gemini-3.8-flash", "STOP")


def test_from_gemini_without_candidates_has_no_text() -> None:
    completion = from_gemini(types.GenerateContentResponse.model_validate({}))

    assert (completion.text, completion.finish_reason) == ("", "no candidate")


def test_live_models_needs_the_key_of_each_provider_it_will_call() -> None:
    only_anthropic = Settings(anthropic_api_key=SecretStr("k"), gemini_api_key=None)

    claude_only = LiveModels(only_anthropic, [HAIKU])  # no Gemini model, no Gemini key needed
    with pytest.raises(MissingKeyError, match="GEMINI_API_KEY"):
        LiveModels(only_anthropic, [HAIKU, FLASH])
    with pytest.raises(ValueError, match=r"gemini-3\.8-flash was not among"):
        claude_only(FLASH, gemini_request(FLASH, "Q?"))


@pytest.mark.parametrize(
    ("project", "expected"),
    [
        (None, {"api_key": "k"}),
        (
            "123",
            {"vertexai": True, "api_key": "k", "project": "123", "location": "global"},
        ),
    ],
)
def test_gemini_calls_go_to_vertex_ai_only_when_a_project_is_set(
    project: str | None, expected: dict[str, Any]
) -> None:
    settings = Settings(gemini_api_key=SecretStr("k"), google_cloud_project=project)

    with patch("lease_clause_checker.providers.genai.Client") as client:
        LiveModels(settings, [FLASH])

    kwargs = dict(client.call_args.kwargs)
    assert kwargs.pop("http_options").retry_options.attempts == API_RETRIES + 1
    assert kwargs == expected


def test_live_models_send_the_request_unchanged() -> None:
    keys = Settings(anthropic_api_key=SecretStr("k"), gemini_api_key=SecretStr("k"))
    live = LiveModels(keys, [HAIKU, FLASH])
    sent: list[dict[str, Any]] = []
    message = {
        "id": "m",
        "type": "message",
        "role": "assistant",
        "model": "claude-haiku-5-5",
        "content": [{"type": "text", "text": "{}"}],
        "stop_reason": "end_turn",
        "stop_sequence": None,
        "usage": {"input_tokens": 1, "output_tokens": 1},
    }

    def create(**request: Any) -> anthropic.types.Message:
        sent.append(request)
        return anthropic.types.Message.model_validate(message)

    def generate_content(**request: Any) -> types.GenerateContentResponse:
        sent.append(request)
        return types.GenerateContentResponse.model_validate({})

    schema = {"type": "object"}
    claude, gemini = request_for(HAIKU, "Q?", schema), request_for(FLASH, "Q?", schema)
    assert live._anthropic is not None
    assert live._gemini is not None
    with (
        patch.object(live._anthropic.messages, "create", create),
        patch.object(live._gemini.models, "generate_content", generate_content),
    ):
        live(HAIKU, claude)
        live(FLASH, gemini)

    assert sent[0] == claude
    assert sent[1]["model"] == gemini["model"]
    assert sent[1]["contents"] == gemini["contents"]
    assert sent[1]["config"] == types.GenerateContentConfig(**gemini["config"])


def test_models_are_a_light_and_heavy_pair_per_provider() -> None:
    pairs = {(spec.provider, spec.weight) for spec in MODELS}

    assert len(MODELS) == 4
    assert pairs == {(p, w) for p in ("anthropic", "google") for w in ("light", "heavy")}


def test_cost_uses_the_per_million_token_prices() -> None:
    assert HAIKU.cost_usd(1_000_000, 2_000_000, date(2026, 11, 11)) == pytest.approx(1.10)


def test_cost_uses_the_price_in_force_on_the_day_of_the_call() -> None:
    argon = model_by_key("gemini-4-argon")

    assert argon.cost_usd(1_000_000, 0, date(2026, 11, 30)) == pytest.approx(2.00)
    assert argon.cost_usd(1_000_000, 0, date(2026, 12, 1)) == pytest.approx(4.00)


def test_a_dropped_model_can_still_be_looked_up() -> None:
    assert model_by_key("gemini-4-carbon").name == "Gemini 4 Carbon"


def test_unknown_model_names_the_known_ones() -> None:
    with pytest.raises(KeyError, match="claude-opus-5-5"):
        model_by_key("gpt-x")


def test_on_first_call_makes_the_models_once_and_only_when_called() -> None:
    made: list[int] = []

    def make() -> Complete:
        made.append(1)
        return lambda spec, request: Completion("t", spec.model_id, "end_turn", 1, 1, {})

    models = OnFirstCall(make)
    assert made == []

    spec = model_by_key("claude-haiku-5-5")
    models(spec, {})
    models(spec, {})
    assert made == [1]
