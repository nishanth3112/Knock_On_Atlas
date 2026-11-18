import json
import os
from datetime import UTC, datetime
from typing import Any

import psycopg
import pytest
from test_mcp_server import ALL_SCOPES, FORD, SECRET, seeded_server

from knock_on_atlas.api.demo import LiveGate
from knock_on_atlas.config import Settings
from knock_on_atlas.edgar.client import SecClient
from knock_on_atlas.lambdas import api, mcp, worker
from knock_on_atlas.lambdas.mcp import mcp_app
from knock_on_atlas.lambdas.secrets import load_secrets
from knock_on_atlas.mcp_server.client import service_token
from knock_on_atlas.retrieval.embed import Embedder
from knock_on_atlas.workflow.worker import Job

ON = LiveGate(None, datetime(2100, 1, 1, tzinfo=UTC))
OFF = LiveGate(None, None)


def url_event(
    method: str, path: str, body: Any = None, **headers: str
) -> dict[str, Any]:
    """What a Lambda function URL sends (payload format 2.0)."""
    return {
        "version": "2.0",
        "routeKey": "$default",
        "rawPath": path,
        "rawQueryString": "",
        "headers": {"host": "x.lambda-url.us-east-1.on.aws", **headers},
        "requestContext": {
            "accountId": "anonymous",
            "apiId": "x",
            "domainName": "x.lambda-url.us-east-1.on.aws",
            "http": {
                "method": method,
                "path": path,
                "protocol": "HTTP/1.1",
                "sourceIp": "127.0.0.1",
                "userAgent": "test",
            },
            "requestId": "r1",
            "routeKey": "$default",
            "stage": "$default",
            "time": "17/Nov/2026:00:00:00 +0000",
            "timeEpoch": 0,
        },
        "body": json.dumps(body) if body is not None else None,
        "isBase64Encoded": False,
    }


class FakeSsm:
    def __init__(self, values: dict[str, str]) -> None:
        self.values = values

    def get_parameters(self, Names: list[str], WithDecryption: bool) -> dict[str, Any]:  # noqa: N803
        assert WithDecryption
        return {
            "Parameters": [
                {"Name": n, "Value": self.values[n]} for n in Names if n in self.values
            ],
            "InvalidParameters": [n for n in Names if n not in self.values],
        }


def test_secrets_land_in_the_environment_by_their_last_segment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    names = "/koa/prod/DATABASE_URL,/koa/prod/JWT_SECRET"
    monkeypatch.setenv("SSM_PARAMETERS", names)
    monkeypatch.delenv("JWT_SECRET", raising=False)
    ssm = FakeSsm(
        {"/koa/prod/DATABASE_URL": "postgresql://neon", "/koa/prod/JWT_SECRET": "s"}
    )
    load_secrets(ssm)
    assert (os.environ["DATABASE_URL"], os.environ["JWT_SECRET"]) == (
        "postgresql://neon",
        "s",
    )
    monkeypatch.setenv("SSM_PARAMETERS", "/koa/prod/GROQ_API_KEY")
    with pytest.raises(RuntimeError, match="GROQ_API_KEY"):
        load_secrets(ssm)


def test_with_the_demo_off_the_worker_drops_jobs_without_running_them() -> None:
    ran: list[Job] = []

    async def run(job: Job) -> str:
        ran.append(job)
        return "paused"

    job = Job(analysis_id="a1", hub_cik=FORD, accession="0000037996-25-000238")
    event = {"Records": [{"body": job.model_dump_json()}]}
    worker.process(event, OFF, run)
    assert ran == []
    worker.process(event, ON, run)
    assert ran == [job]


def test_the_api_answers_through_a_function_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("JWT_SECRET", "x" * 32)
    api.app.cache_clear()
    reply = api.handler(url_event("GET", "/health"), None)
    assert reply["statusCode"] == 200
    assert json.loads(reply["body"]) == {"ok": True, "live": False}
    api.app.cache_clear()


def test_mcp_tools_answer_through_a_function_url_only_with_a_token(
    db: psycopg.Connection, sec_client: SecClient, fake_embedder: Embedder
) -> None:
    seeded_server(db, sec_client, fake_embedder)  # seeds the rows the tools read
    call = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": "get_company", "arguments": {"cik": FORD}},
    }
    accept = "application/json, text/event-stream"
    token = service_token(SECRET, ALL_SCOPES, "test")

    def send(**headers: str) -> dict[str, Any]:
        # Called like Lambda does: synchronously, on the main thread.
        app = mcp_app(db, sec_client, SECRET)
        content = {"content-type": "application/json"}
        return app(
            url_event("POST", "/mcp", call, accept=accept, **content, **headers), None
        )

    refused = send()
    assert refused["statusCode"] == 401
    answered = send(authorization=f"Bearer {token}")
    assert answered["statusCode"] == 200
    assert "Ford Motor Co" in answered["body"]


def test_anonymous_mcp_requests_are_refused_before_neon_wakes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def no_connection(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("connected to the database for an anonymous request")

    loaded = Settings(sec_user_agent="t t@example.com", mcp_jwt_secret=SECRET)  # type: ignore[arg-type]
    monkeypatch.setattr(mcp, "settings", lambda: loaded)
    monkeypatch.setattr(mcp.psycopg, "connect", no_connection)
    for headers in ({}, {"authorization": "Bearer forged"}):
        assert (
            mcp.handler(url_event("POST", "/mcp", {}, **headers), None)["statusCode"]
            == 401
        )
    # The worker's service token passes; one signed with any other key doesn't.
    token = service_token(SECRET, ALL_SCOPES, "koa-worker")
    assert mcp.signed(
        url_event("POST", "/mcp", {}, authorization=f"Bearer {token}"), SECRET
    )
    other = service_token("another-secret-of-at-least-32-chars", ALL_SCOPES, "x")
    assert not mcp.signed(
        url_event("POST", "/mcp", {}, authorization=f"Bearer {other}"), SECRET
    )
