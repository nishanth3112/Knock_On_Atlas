import pytest
from langchain_groq import ChatGroq

from knock_on_atlas.agents.llm import GROQ_LARGE, GROQ_SMALL, models_from_settings
from knock_on_atlas.config import Settings


def settings(**values: str) -> Settings:
    return Settings(llm_provider="groq", sec_user_agent="t t@example.com", **values)  # type: ignore[arg-type]


def test_groq_gives_a_small_planner_and_a_large_model_for_the_rest() -> None:
    models = models_from_settings(settings(groq_api_key="test-key"))
    assert isinstance(models.small, ChatGroq) and isinstance(models.hard, ChatGroq)
    assert models.small.model_name == GROQ_SMALL
    assert models.reason.model_name == models.hard.model_name == GROQ_LARGE
    # ChatGroq sends 1e-8 for 0, which Groq's API rejects; either way it's greedy.
    assert models.hard.temperature < 1e-6 and models.hard.max_retries > 0


def test_groq_without_a_key_fails_at_startup() -> None:
    with pytest.raises(ValueError, match="GROQ_API_KEY"):
        models_from_settings(settings())
