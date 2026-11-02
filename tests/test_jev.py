import json
from pathlib import Path

import httpx
import psycopg
import pytest

from knock_on_atlas.config import JevRoute, get_settings
from knock_on_atlas.db.migrate import migrate
from knock_on_atlas.graph_store.resolve import Candidate, Hub
from knock_on_atlas.jev.client import (
    MODELS,
    JevCall,
    JevClient,
    JevError,
    SystemOneRequest,
    SystemOneResponse,
    jev_from_settings,
    record_call,
)
from knock_on_atlas.jev.questions import (
    J3_APPROVE_MIN,
    choose_entity,
    decide_entity,
    entity_questions,
)

FIXTURES = Path(__file__).parent / "fixtures" / "jev"
REQUEST = json.loads((FIXTURES / "j3_request.json").read_text())
LABEL = REQUEST["state"]["customer_label"]
SUPPLIER = REQUEST["state"]["supplier"]
CANDS = [
    Candidate(Hub(f"{i:010}", name, tuple(aliases)), 100.0 - i)
    for i, (name, aliases) in enumerate(REQUEST["state"]["candidates"].items())
]
URLS = {
    JevRoute.TYPESAFE: "https://api.typesafe.ai/v1/systemone",
    JevRoute.OPENROUTER: "https://openrouter.ai/api/v1/systemone",
}


def client(route: JevRoute, seen: list[httpx.Request]) -> JevClient:
    def respond(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        body = (FIXTURES / f"j3_{route}.json").read_bytes()
        return httpx.Response(200, content=body)

    return JevClient(route, "test-key", transport=httpx.MockTransport(respond))


@pytest.mark.parametrize("route", [JevRoute.TYPESAFE, JevRoute.OPENROUTER])
def test_j3_request_shape_and_recorded_answer(route: JevRoute) -> None:
    seen: list[httpx.Request] = []
    decision, call = choose_entity(client(route, seen), LABEL, SUPPLIER, CANDS)

    sent = seen[0]
    assert str(sent.url) == URLS[route]
    assert sent.headers["Authorization"] == "Bearer test-key"
    assert json.loads(sent.content) == {**REQUEST, "model": MODELS[route]}
    assert decision.hub is not None and decision.hub.name == "Walmart Inc."
    assert decision.confidence >= J3_APPROVE_MIN
    assert decision.method == "jev_choice"
    assert call is not None and call.response.usage.input_tokens > 0
    if route == JevRoute.OPENROUTER:
        assert call.request_id == "gen-dec-1793648034-ReFSS6IH7AhtETq68GAC"


def fake_call(names: float, pick: str, reversed_pick: str) -> JevCall:
    def choice(name: str) -> dict[str, object]:
        probabilities = {c.hub.name: 0.0 for c in CANDS} | {name: 0.95}
        return {
            "type": "choice",
            "choice": name,
            "confidence": 0.95,
            "probabilities": probabilities,
        }

    body = {
        "model": "jev-1.14.0",
        "answers": {
            "j3_names_candidate": {"type": "noul", "noul": names},
            "j3_pick": choice(pick),
            "j3_pick_reversed": choice(reversed_pick),
        },
        "usage": {"input_tokens": 10, "output_tokens": 1},
    }
    request = SystemOneRequest(
        model="jev-1.14.0", state=REQUEST["state"], questions=entity_questions(CANDS)
    )
    return JevCall(
        route=JevRoute.TYPESAFE,
        request=request,
        response=SystemOneResponse.model_validate(body),
        request_id=None,
        latency_ms=1,
    )


def test_label_naming_no_candidate_resolves_to_none() -> None:
    decision = decide_entity(CANDS, fake_call(0.05, "Walmart Inc.", "Walmart Inc."))
    assert decision.hub is None


def test_order_disagreement_keeps_confidence_below_approval() -> None:
    decision = decide_entity(
        CANDS, fake_call(0.95, "Walmart Inc.", "Costco Wholesale Corporation")
    )
    assert decision.hub is not None and decision.hub.name == "Walmart Inc."
    assert decision.confidence < J3_APPROVE_MIN


def test_stub_sends_top_fuzzy_hub_to_review_without_calling_jev() -> None:
    decision, call = choose_entity(None, LABEL, SUPPLIER, CANDS)
    assert call is None
    assert decision.hub == CANDS[0].hub
    assert decision.confidence == 0.0
    assert decision.method == "fuzzy"


def test_error_status_raises_instead_of_answering() -> None:
    transport = httpx.MockTransport(
        lambda request: httpx.Response(422, json={"detail": []})
    )
    jev = JevClient(JevRoute.TYPESAFE, "test-key", transport=transport)
    with pytest.raises(JevError, match="HTTP 422"):
        choose_entity(jev, LABEL, SUPPLIER, CANDS)


def test_settings_pick_route_and_require_its_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert jev_from_settings(get_settings()) is None  # tests run with JEV_ROUTE=off
    monkeypatch.setenv("JEV_ROUTE", "openrouter")
    get_settings.cache_clear()
    with pytest.raises(JevError, match="API key is not set"):
        jev_from_settings(get_settings())
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    get_settings.cache_clear()
    jev = jev_from_settings(get_settings())
    assert jev is not None and jev.route == JevRoute.OPENROUTER


def test_record_call_keeps_tokens_and_payload(db: psycopg.Connection) -> None:
    migrate(db)
    record_call(db, fake_call(0.95, "Walmart Inc.", "Walmart Inc."), "j3")
    row = db.execute(
        "SELECT purpose, model, input_tokens, request->'state'->>'customer_label'"
        " FROM jev_calls"
    ).fetchone()
    assert row == ("j3", "jev-1.14.0", 10, LABEL)
