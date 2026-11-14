"""Typed settings, read from the environment and an optional `.env` file."""

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Project settings.

    API keys use the provider SDKs' standard variable names. Both are optional so that
    offline work (tests, re-scoring cached outputs) needs no keys. Empty values, as left
    by copying `.env.example` unchanged, count as unset.
    """

    model_config = SettingsConfigDict(env_file=".env", env_ignore_empty=True, extra="ignore")

    anthropic_api_key: SecretStr | None = None
    gemini_api_key: SecretStr | None = None
    google_cloud_project: str | None = None
    """When set, Gemini calls go to Vertex AI in this Google Cloud project instead of the Gemini
    API. Needed for keys whose credit pays for Vertex AI only; such a key gets HTTP 402 from the
    Gemini API."""
