from pathlib import Path

import pytest
from pydantic import ValidationError

from knock_on_atlas.config import JevRoute, LLMProvider, Settings

UA = "Test User test@example.com"


def test_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in Settings.model_fields:
        monkeypatch.delenv(name.upper(), raising=False)
    s = Settings(_env_file=None, sec_user_agent=UA)
    assert s.llm_provider is LLMProvider.OLLAMA
    assert s.jev_route is JevRoute.TYPESAFE
    assert s.dynamodb_endpoint_url is None
    assert s.groq_api_key is None


def test_env_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "groq")
    monkeypatch.setenv("JEV_ROUTE", "off")
    monkeypatch.setenv("SEC_USER_AGENT", UA)
    s = Settings(_env_file=None)
    assert s.llm_provider is LLMProvider.GROQ
    assert s.jev_route is JevRoute.OFF
    assert s.sec_user_agent == UA


@pytest.mark.parametrize("name", ["LLM_PROVIDER", "JEV_ROUTE"])
def test_unknown_switch_value_rejected(
    monkeypatch: pytest.MonkeyPatch, name: str
) -> None:
    monkeypatch.setenv(name, "openai")
    with pytest.raises(ValidationError):
        Settings(_env_file=None, sec_user_agent=UA)


def test_sec_user_agent_required(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SEC_USER_AGENT", raising=False)
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_blank_env_file_values_are_unset(tmp_path: Path) -> None:
    # A copied .env.example has blank KEY= lines; they must mean "not set".
    env_file = tmp_path / ".env"
    env_file.write_text(f"SEC_USER_AGENT={UA}\nGROQ_API_KEY=\nTYPESAFE_API_KEY=\n")
    s = Settings(_env_file=env_file)
    assert s.groq_api_key is None
    assert s.typesafe_api_key is None


def test_blank_sec_user_agent_rejected(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("SEC_USER_AGENT", raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text("SEC_USER_AGENT=\n")
    with pytest.raises(ValidationError, match="sec_user_agent"):
        Settings(_env_file=env_file)


def test_secrets_not_printed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GROQ_API_KEY", "gsk_supersecret")
    s = Settings(_env_file=None, sec_user_agent=UA)
    assert "gsk_supersecret" not in repr(s)
    assert "koa:koa" not in repr(s)
    assert s.groq_api_key is not None
    assert s.groq_api_key.get_secret_value() == "gsk_supersecret"
