import logging
import time
from dataclasses import dataclass
from typing import Annotated, Any, Literal

import httpx
import psycopg
from psycopg.types.json import Jsonb
from pydantic import BaseModel, Field

from knock_on_atlas.config import JevRoute, Settings

logger = logging.getLogger(__name__)

# Pinned, never an alias, so answers don't drift when jev-latest moves.
MODELS = {JevRoute.TYPESAFE: "jev-1.14.0", JevRoute.OPENROUTER: "typesafe/jev-1.14"}
BASE_URLS = {
    JevRoute.TYPESAFE: "https://api.typesafe.ai",
    JevRoute.OPENROUTER: "https://openrouter.ai/api",
}


class NoulCriteria(BaseModel):
    true: str
    false: str


class NoulQuestion(BaseModel):
    type: Literal["noul"] = "noul"
    instructions: str
    criteria: NoulCriteria | None = None


class ChoiceQuestion(BaseModel):
    type: Literal["choice"] = "choice"
    instructions: str
    criteria: dict[str, str | None] = Field(min_length=1, max_length=255)


Question = Annotated[NoulQuestion | ChoiceQuestion, Field(discriminator="type")]


class SystemOneRequest(BaseModel):
    model: str
    state: dict[str, Any]
    questions: dict[str, Question] = Field(min_length=1)


class NoulAnswer(BaseModel):
    type: Literal["noul"]
    noul: float


class ChoiceAnswer(BaseModel):
    type: Literal["choice"]
    choice: str
    confidence: float
    probabilities: dict[str, float]


class Usage(BaseModel):
    input_tokens: int
    output_tokens: int


class SystemOneResponse(BaseModel):
    model: str
    # OpenRouter's generation id; the direct route sends a request id header instead.
    id: str | None = None
    answers: dict[
        str, Annotated[NoulAnswer | ChoiceAnswer, Field(discriminator="type")]
    ]
    usage: Usage


class JevError(RuntimeError):
    pass


@dataclass(frozen=True)
class JevCall:
    route: JevRoute
    request: SystemOneRequest
    response: SystemOneResponse
    request_id: str | None
    latency_ms: int


class JevClient:
    def __init__(
        self,
        route: JevRoute,
        api_key: str,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.route = route
        self._http = httpx.Client(
            base_url=BASE_URLS[route],
            headers={"Authorization": f"Bearer {api_key}"},
            transport=transport,
            timeout=30,
        )

    def ask(self, state: dict[str, Any], questions: dict[str, Question]) -> JevCall:
        request = SystemOneRequest(
            model=MODELS[self.route], state=state, questions=questions
        )
        start = time.monotonic()
        response = self._http.post(
            "/v1/systemone", json=request.model_dump(mode="json", exclude_none=True)
        )
        if response.is_error:
            # A failed call is an error, never read as a "no".
            raise JevError(f"{self.route}: HTTP {response.status_code} {response.text}")
        parsed = SystemOneResponse.model_validate_json(response.content)
        call = JevCall(
            route=self.route,
            request=request,
            response=parsed,
            request_id=response.headers.get("x-typesafe-request-id") or parsed.id,
            latency_ms=round((time.monotonic() - start) * 1000),
        )
        logger.info(
            "jev %s %s input_tokens=%d",
            self.route,
            call.response.model,
            call.response.usage.input_tokens,
        )
        return call


def jev_from_settings(settings: Settings) -> JevClient | None:
    """None when JEV_ROUTE=off: callers fall back to the stub path."""
    if settings.jev_route == JevRoute.OFF:
        return None
    key = (
        settings.typesafe_api_key
        if settings.jev_route == JevRoute.TYPESAFE
        else settings.openrouter_api_key
    )
    if key is None:
        raise JevError(f"JEV_ROUTE={settings.jev_route} but its API key is not set")
    return JevClient(settings.jev_route, key.get_secret_value())


def record_call(conn: psycopg.Connection, call: JevCall, purpose: str) -> None:
    """Keep every call, with its exact payload, so spend and answers can be audited."""
    usage = call.response.usage
    conn.execute(
        "INSERT INTO jev_calls (purpose, route, model, request_id, input_tokens,"
        " output_tokens, latency_ms, request, answers)"
        " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
        (
            purpose,
            call.route.value,
            call.response.model,
            call.request_id,
            usage.input_tokens,
            usage.output_tokens,
            call.latency_ms,
            Jsonb(call.request.model_dump(mode="json", exclude_none=True)),
            Jsonb(call.response.model_dump(mode="json")["answers"]),
        ),
    )
