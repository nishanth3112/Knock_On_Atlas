"""Phase 7 done-when: start an analysis, see it pause for review, approve it
through the API, and read the published brief. The real app, worker and DynamoDB
Local checkpoints; only the LLM is scripted and the queue is in-process."""

import asyncio

import psycopg
import pytest
from conftest import DYNAMODB_LOCAL
from test_api import FILING, HUB, ON
from test_auth import add_user, api, bearer
from test_mcp_server import ALL_SCOPES, SECRET, seeded_server
from test_worker import Calls

from knock_on_atlas.agents.llm import FakeChatModel, Models
from knock_on_atlas.api.jobs import LocalQueue
from knock_on_atlas.edgar.client import SecClient
from knock_on_atlas.mcp_server.client import local_client, mcp_caller, service_token
from knock_on_atlas.retrieval.embed import Embedder
from knock_on_atlas.workflow.checkpoints import dynamodb_saver
from knock_on_atlas.workflow.deps import Deps
from knock_on_atlas.workflow.worker import Job, handle


def test_start_review_publish(
    db: psycopg.Connection,
    sec_client: SecClient,
    fake_embedder: Embedder,
    checkpoint_table: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    server = seeded_server(db, sec_client, fake_embedder)  # Ford is the hub
    queue = LocalQueue()
    client = api(db, monkeypatch, queue, ON)
    add_user(db, "ana@example.com", "analyst")
    add_user(db, "rev@example.com", "reviewer")
    saver = dynamodb_saver(DYNAMODB_LOCAL, checkpoint_table)
    calls = Calls()

    def worker_drains_the_queue() -> int:
        async def go() -> int:
            token = service_token(SECRET, ALL_SCOPES, "worker")
            async with local_client(server, token) as mcp:
                llm = FakeChatModel(responder=calls)
                deps = Deps(db, Models(llm, llm, llm), None, mcp_caller(mcp))

                async def run(job: Job) -> object:
                    return await handle(job, deps, saver)

                return await queue.drain(run)

        return asyncio.run(go())

    analyst = bearer(client, "ana@example.com")
    started = client.post(
        "/analyses", json={"hub_cik": HUB, "accession": FILING}, headers=analyst
    )
    assert started.status_code == 202
    url = f"/analyses/{started.json()['id']}"

    assert worker_drains_the_queue() == 1
    paused = client.get(url, headers=analyst).json()
    assert paused["status"] == "paused"
    assert paused["result"]["reasons"] and paused["result"]["exposures"]

    approve = client.post(
        f"/reviews/{started.json()['id']}",
        json={"decision": "approved", "note": "22% matches the 10-K"},
        headers=bearer(client, "rev@example.com"),
    )
    assert approve.status_code == 202
    assert worker_drains_the_queue() == 1

    published = client.get(url, headers=analyst).json()
    assert published["status"] == "published" and published["completed_at"]
    [claim] = published["result"]["brief"]["claims"]
    assert claim["citations"] and "22%" in claim["text"]
    # Each agent ran once: the review resumed the run, it didn't start over.
    assert len(calls.prompts) == 6
