from datetime import datetime
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


class Checkpointer(StrEnum):
    MEMORY = "memory"
    DYNAMODB = "dynamodb"


class JobQueueKind(StrEnum):
    LOCAL = "local"
    SQS = "sqs"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_ignore_empty=True, extra="ignore"
    )

    llm_provider: LLMProvider = LLMProvider.OLLAMA
    jev_route: JevRoute = JevRoute.TYPESAFE
    # memory dies with the process; dynamodb lets a run resume in another one.
    checkpointer: Checkpointer = Checkpointer.MEMORY
    # local keeps jobs in the API process; sqs sends them to the worker Lambda.
    job_queue: JobQueueKind = JobQueueKind.LOCAL
    job_queue_url: str | None = None
    # Live runs only before this time: SSM parameter on AWS, a setting locally.
    demo_live_param: str | None = None
    demo_live_until: datetime | None = None

    database_url: SecretStr = SecretStr("postgresql://koa:koa@localhost:5432/koa")
    # None in AWS, where boto3 resolves the real DynamoDB endpoint.
    dynamodb_endpoint_url: str | None = None
    # SEC rejects requests without a descriptive User-Agent, so there is no default.
    sec_user_agent: str
    # Overrides a run's 240 s wall clock (spec §11) on a machine too slow for it;
    # unset in CI and on AWS.
    run_wall_clock_seconds: int | None = None

    groq_api_key: SecretStr | None = None
    typesafe_api_key: SecretStr | None = None
    openrouter_api_key: SecretStr | None = None
    twelvedata_api_key: SecretStr | None = None
    logfire_token: SecretStr | None = None
    jwt_secret: SecretStr | None = None


@lru_cache
def get_settings() -> Settings:
    return Settings()
