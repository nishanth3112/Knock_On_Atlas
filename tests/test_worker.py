import asyncio
import uuid
from typing import Any

import psycopg
import pytest
from langchain_core.messages import AIMessage, BaseMessage
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import InMemorySaver
from test_graph import role_responder
from test_mcp_server import ALL_SCOPES, FORD, FORD_8K, SECRET, seeded_server

from knock_on_atlas.agents.llm import FakeChatModel, Models
from knock_on_atlas.edgar.client import SecClient
from knock_on_atlas.mcp_server.client import local_client, mcp_caller, service_token
from knock_on_atlas.retrieval.embed import Embedder
from knock_on_atlas.workflow.deps import Deps
from knock_on_atlas.workflow.graph import checkpoint_serde
from knock_on_atlas.workflow.worker import Job, handle

APPROVE = {"decision": "approved", "reviewer": "test"}


class Calls:
    """The scripted agents, counting the LLM calls each job makes."""

    def __init__(self, fail_brief_once: bool = False) -> None:
        self.prompts: list[str] = []
        self.fail_brief_once = fail_brief_once

    def __call__(self, messages: list[BaseMessage], tools: list[str]) -> AIMessage:
        system = str(messages[0].content)
        if self.fail_brief_once and system.startswith("You write a short exposure"):
            self.fail_brief_once = False
            raise RuntimeError("worker died while writing the brief")
        self.prompts.append(system[:30])
        return role_responder(messages, tools)


class Worker:
    def __init__(
        self, db: psycopg.Connection, sec: SecClient, embedder: Embedder
    ) -> None:
        self.db = db
        self.server = seeded_server(db, sec, embedder)
        self.saver: BaseCheckpointSaver[Any] = InMemorySaver(serde=checkpoint_serde())
        self.id = str(uuid.uuid4())

    def run(self, calls: Calls, decision: dict[str, Any] | None = None) -> str:
        """One delivery of the job; the saver is shared, like the checkpoint table."""
        job = Job(
            analysis_id=self.id, hub_cik=FORD, accession=FORD_8K, decision=decision
        )

        async def go() -> str:
            token = service_token(SECRET, ALL_SCOPES, "worker")
            async with local_client(self.server, token) as client:
                llm = FakeChatModel(responder=calls)
                deps = Deps(self.db, Models(llm, llm, llm), None, mcp_caller(client))
                return await handle(job, deps, self.saver)

        return asyncio.run(go())

    def row(self) -> tuple[Any, ...]:
        row = self.db.execute(
            "SELECT status, result FROM analyses WHERE id = %s", (self.id,)
        ).fetchone()
        assert row is not None
        return tuple(row)


@pytest.fixture
def worker(
    db: psycopg.Connection, sec_client: SecClient, fake_embedder: Embedder
) -> Worker:
    return Worker(db, sec_client, fake_embedder)


def test_redelivered_jobs_never_rerun_finished_work(worker: Worker) -> None:
    first = Calls()
    assert worker.run(first) == "paused"
    assert worker.row() == ("paused", None)

    # The same start job again: the run is waiting for a reviewer, so nothing runs.
    again = Calls()
    assert worker.run(again) == "paused" and again.prompts == []

    review = Calls()
    assert worker.run(review, APPROVE) == "published"
    assert [p[:24] for p in review.prompts] == ["You write a short exposu"]
    status, brief = worker.row()
    assert status == "published" and brief["claims"][0]["citations"]

    # The review job redelivered after publishing: no LLM call, the same brief.
    late = Calls()
    assert worker.run(late, APPROVE) == "published" and late.prompts == []
    assert worker.row() == ("published", brief)


def test_a_crashed_run_resumes_where_it_stopped(worker: Worker) -> None:
    assert worker.run(Calls()) == "paused"
    # The test's MCP client runs in a task group, which wraps the error.
    with pytest.raises(ExceptionGroup) as failure:
        worker.run(Calls(fail_brief_once=True), APPROVE)
    assert failure.group_contains(RuntimeError, match="writing the brief")
    assert worker.row()[0] == "paused"

    retry = Calls()
    assert worker.run(retry, APPROVE) == "published"
    # Only the failed step ran again: no triage, planning or research.
    assert [p[:24] for p in retry.prompts] == ["You write a short exposu"]


def test_a_rejected_review_ends_without_a_brief(worker: Worker) -> None:
    worker.run(Calls())
    rejected = {"decision": "rejected", "reviewer": "test"}
    assert worker.run(Calls(), rejected) == "rejected"
    assert worker.row() == ("rejected", None)
