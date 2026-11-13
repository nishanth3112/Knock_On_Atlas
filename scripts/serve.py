"""Run the API and a worker in one local process, for the web app.

The API listens on http://127.0.0.1:8000 (the web dev server proxies /api to it).
Jobs go to an in-process queue that a background thread drains with the real
worker: chat models from LLM_PROVIDER, Jev from JEV_ROUTE, checkpoints from
CHECKPOINTER, and edgar-intel in-process. Live runs need DEMO_LIVE_UNTIL set.

Usage: uv run python scripts/serve.py
"""

import asyncio
import logging
import secrets
import threading
import time
from pathlib import Path

import psycopg
import uvicorn

from knock_on_atlas.agents.llm import models_from_settings
from knock_on_atlas.api.app import create_app
from knock_on_atlas.api.jobs import LocalQueue
from knock_on_atlas.config import get_settings
from knock_on_atlas.edgar.client import SecClient
from knock_on_atlas.jev.client import jev_from_settings
from knock_on_atlas.mcp_server.client import local_client, mcp_caller, service_token
from knock_on_atlas.mcp_server.server import TOOL_SCOPES, build_server
from knock_on_atlas.retrieval.embed import BgeEmbedder
from knock_on_atlas.workflow import budgets
from knock_on_atlas.workflow.checkpoints import checkpointer
from knock_on_atlas.workflow.deps import Deps
from knock_on_atlas.workflow.worker import Job, handle

POLL_SECONDS = 1.0

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("serve")
settings = get_settings()
wall_clock = settings.run_wall_clock_seconds or budgets.WALL_CLOCK_SECONDS
queue = LocalQueue()


async def work() -> None:
    with psycopg.connect(
        settings.database_url.get_secret_value(), autocommit=True
    ) as conn:
        sec = SecClient(settings.sec_user_agent, Path("data/raw/sec_cache"))
        # Server and worker share this process, so the signing key never leaves it.
        secret = secrets.token_urlsafe(32)
        server = build_server(conn, sec, BgeEmbedder(), secret)
        models, jev, saver = (
            models_from_settings(settings),
            jev_from_settings(settings),
            checkpointer(settings),
        )
        scopes = set(TOOL_SCOPES.values())
        while True:
            if not queue.jobs:
                await asyncio.sleep(POLL_SECONDS)
                continue
            # A fresh token per batch: the process outlives any one token.
            token = service_token(secret, scopes, "local-worker", int(wall_clock) + 60)
            async with local_client(server, token) as client:

                async def run(job: Job) -> None:
                    deps = Deps(
                        conn,
                        models,
                        jev,
                        mcp_caller(client),
                        deadline=time.time() + wall_clock,
                    )
                    try:
                        log.info(
                            "job %s: %s",
                            job.analysis_id,
                            await handle(job, deps, saver),
                        )
                    except Exception:
                        # SQS would move it to the DLQ after its retries; the row
                        # stays queued and the filing can be started again later.
                        log.exception("job %s failed and was dropped", job.analysis_id)

                await queue.drain(run)


threading.Thread(target=lambda: asyncio.run(work()), daemon=True).start()
uvicorn.run(create_app(queue), host="127.0.0.1", port=8000)
