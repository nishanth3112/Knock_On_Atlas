"""One delivery of a job, in its own process, for the durability suite.

The suite starts this file, maybe with a crash point, then starts it again to
redeliver the job: nothing survives between the two but Postgres and DynamoDB.
Every LLM call is appended to KOA_CALLS, and every checkpoint write (size and
latency) to KOA_WRITES, as it happens, so work done before a crash is counted too.

Env: DATABASE_URL, KOA_SCHEMA, KOA_TABLE, KOA_JOB (Job JSON), KOA_CALLS and
KOA_WRITES (paths), KOA_CRASH_AT (optional).
"""

import asyncio
import json
import os
import tempfile
import time
from pathlib import Path
from typing import Any

import psycopg
from conftest import DYNAMODB_LOCAL, FakeEmbedder, fixture_sec_client
from langchain_core.messages import AIMessage, BaseMessage
from test_graph import role_responder
from test_mcp_server import ALL_SCOPES, SECRET

from knock_on_atlas.agents.llm import FakeChatModel, Models
from knock_on_atlas.mcp_server.client import local_client, mcp_caller, service_token
from knock_on_atlas.mcp_server.server import build_server
from knock_on_atlas.workflow.checkpoints import dynamodb_saver
from knock_on_atlas.workflow.deps import Deps
from knock_on_atlas.workflow.worker import Job, handle

CALLS = Path(os.environ["KOA_CALLS"])
WRITES = Path(os.environ["KOA_WRITES"])


def responder(messages: list[BaseMessage], tools: list[str]) -> AIMessage:
    with CALLS.open("a") as f:
        f.write(str(messages[0].content)[:24] + "\n")
    return role_responder(messages, tools)


def measured(saver: Any) -> Any:
    """Time each DynamoDB write and record its serialized size (aput calls put)."""
    put, put_writes = saver.put, saver.put_writes

    def record(kind: str, size: int, began: float) -> None:
        ms = (time.perf_counter() - began) * 1000
        with WRITES.open("a") as f:
            f.write(json.dumps({"kind": kind, "bytes": size, "ms": ms}) + "\n")

    def timed_put(config: Any, checkpoint: Any, *args: Any) -> Any:
        size = len(saver.serde.dumps_typed(checkpoint)[1])
        began = time.perf_counter()
        result = put(config, checkpoint, *args)
        record("checkpoint", size, began)
        return result

    def timed_put_writes(config: Any, writes: Any, *args: Any) -> None:
        size = sum(len(saver.serde.dumps_typed(value)[1]) for _, value in writes)
        began = time.perf_counter()
        put_writes(config, writes, *args)
        record("writes", size, began)

    saver.put, saver.put_writes = timed_put, timed_put_writes
    return saver


async def deliver() -> str:
    with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True) as conn:
        conn.execute(f"SET search_path TO {os.environ['KOA_SCHEMA']}, public")
        embedder = FakeEmbedder()
        sec = fixture_sec_client(Path(tempfile.mkdtemp()))
        server = build_server(conn, sec, embedder, SECRET)
        token = service_token(SECRET, ALL_SCOPES, "worker")
        async with local_client(server, token) as client:
            llm = FakeChatModel(responder=responder)
            deps = Deps(
                conn,
                Models(llm, llm, llm),
                None,
                mcp_caller(client),
                crash_at=os.environ.get("KOA_CRASH_AT") or None,
            )
            saver = measured(dynamodb_saver(DYNAMODB_LOCAL, os.environ["KOA_TABLE"]))
            return await handle(
                Job.model_validate_json(os.environ["KOA_JOB"]), deps, saver
            )


print(asyncio.run(deliver()))
