from pathlib import Path

import pytest

from lease_clause_checker.config import Settings

KEY_VARS = ("ANTHROPIC_API_KEY", "GEMINI_API_KEY")


@pytest.fixture(autouse=True)
def _clear_key_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in KEY_VARS:
        monkeypatch.delenv(var, raising=False)


def test_keys_default_to_none_without_env() -> None:
    settings = Settings(_env_file=None)

    assert settings.anthropic_api_key is None
    assert settings.gemini_api_key is None


def test_keys_read_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    monkeypatch.setenv("GEMINI_API_KEY", "gem-test")

    settings = Settings(_env_file=None)

    assert settings.anthropic_api_key is not None
    assert settings.anthropic_api_key.get_secret_value() == "sk-ant-test"
    assert settings.gemini_api_key is not None
    assert settings.gemini_api_key.get_secret_value() == "gem-test"


def test_keys_read_from_dotenv_in_working_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / ".env").write_text("ANTHROPIC_API_KEY=from-file\n")
    monkeypatch.chdir(tmp_path)

    settings = Settings()

    assert settings.anthropic_api_key is not None
    assert settings.anthropic_api_key.get_secret_value() == "from-file"


def test_blank_keys_from_copied_env_example_count_as_unset(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / ".env").write_text("ANTHROPIC_API_KEY=\nGEMINI_API_KEY=\n")
    monkeypatch.chdir(tmp_path)

    settings = Settings()

    assert settings.anthropic_api_key is None
    assert settings.gemini_api_key is None


def test_keys_are_masked_in_repr(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-secret-value")

    settings = Settings(_env_file=None)

    assert "sk-ant-secret-value" not in repr(settings)
    assert "sk-ant-secret-value" not in str(settings)
