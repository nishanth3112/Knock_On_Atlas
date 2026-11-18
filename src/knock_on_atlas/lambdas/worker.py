"""koa-worker: runs analysis jobs from SQS (batch size 1).

With the demo off it acknowledges and drops each job: live runs spend Groq and Jev
calls. An exception propagates, so SQS redelivers and, after its retries, moves the
job to the DLQ; the checkpoint makes a redelivery resume rather than restart.
"""

import asyncio
import logging
from collections.abc import Callable, Coroutine
from functools import cache
from typing import Any

import psycopg

from knock_on_atlas.agents.llm import models_from_settings
from knock_on_atlas.api.demo import LiveGate, gate_from_settings
from knock_on_atlas.config import Settings, get_settings
from knock_on_atlas.jev.client import jev_from_settings
from knock_on_atlas.lambdas.secrets import load_secrets
from knock_on_atlas.mcp_server.client import mcp_caller, remote_client, service_token
from knock_on_atlas.mcp_server.server import TOOL_SCOPES
from knock_on_atlas.observability import configure
from knock_on_atlas.workflow.budgets import WALL_CLOCK_SECONDS
from knock_on_atlas.workflow.checkpoints import checkpointer
from knock_on_atlas.workflow.deps import Deps
from knock_on_atlas.workflow.worker import Job, handle

log = logging.getLogger(__name__)


@cache
def runtime() -> tuple[Settings, LiveGate]:
    load_secrets()
    settings = get_settings()
    configure(settings)
    return settings, gate_from_settings(settings)


def process(
    event: dict[str, Any],
    gate: LiveGate,
    run: Callable[[Job], Coroutine[Any, Any, str]],
) -> None:
    for record in event["Records"]:
        job = Job.model_validate_json(record["body"])
        if not gate.is_live():
            log.info("demo off: dropped job %s", job.analysis_id)
            continue
        log.info("job %s: %s", job.analysis_id, asyncio.run(run(job)))


async def run_job(settings: Settings, job: Job) -> str:
    if settings.mcp_url is None or settings.mcp_jwt_secret is None:
        raise RuntimeError("koa-worker needs MCP_URL and MCP_JWT_SECRET")
    token = service_token(
        settings.mcp_jwt_secret.get_secret_value(),
        set(TOOL_SCOPES.values()),
        "koa-worker",
        WALL_CLOCK_SECONDS + 60,
    )
    url = settings.database_url.get_secret_value()
    # Built per job: async clients from an earlier invocation's event loop are dead.
    with psycopg.connect(url, autocommit=True, prepare_threshold=None) as conn:
        async with remote_client(settings.mcp_url, token) as client:
            deps = Deps(
                conn,
                models_from_settings(settings),
                jev_from_settings(settings),
                mcp_caller(client),
            )
            return await handle(job, deps, checkpointer(settings))


def handler(event: dict[str, Any], context: Any) -> None:
    settings, gate = runtime()
    process(event, gate, lambda job: run_job(settings, job))
