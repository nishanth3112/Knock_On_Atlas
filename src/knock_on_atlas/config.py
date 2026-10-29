from enum import StrEnum
from functools import lru_cache

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class LLMProvider(StrEnum):
    OLLAMA = "ollama"
    GROQ = "groq"
    FAKE = "fake"


class JevRoute(StrEnum):
    TYPESAFE = "typesafe"
    OPENROUTER = "openrouter"
    OFF = "off"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    llm_provider: LLMProvider = LLMProvider.OLLAMA
    jev_route: JevRoute = JevRoute.TYPESAFE

    database_url: SecretStr = SecretStr("postgresql://koa:koa@localhost:5432/koa")
    # None in AWS, where boto3 resolves the real DynamoDB endpoint.
    dynamodb_endpoint_url: str | None = None
    # SEC rejects requests without a descriptive User-Agent, so there is no default.
    sec_user_agent: str

    groq_api_key: SecretStr | None = None
    typesafe_api_key: SecretStr | None = None
    openrouter_api_key: SecretStr | None = None
    twelvedata_api_key: SecretStr | None = None
    logfire_token: SecretStr | None = None
    jwt_secret: SecretStr | None = None


@lru_cache
def get_settings() -> Settings:
    return Settings()
