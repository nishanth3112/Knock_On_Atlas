"""Run one 8-K analysis locally and write evals/runs/<UTC timestamp>.json.

Chat models from LLM_PROVIDER (ollama), Jev from JEV_ROUTE (every call logged in
jev_calls), and edgar-intel in-process over HTTP with a scoped token. The run goes
through the worker with CHECKPOINTER's saver; with CHECKPOINTER=dynamodb, rerunning
with the same --analysis-id resumes it like a redelivered job. With --approve, a
run paused for human review is resumed as approved.

Usage: uv run python scripts/run_analysis.py <hub_cik> <accession>
       [--approve] [--analysis-id UUID]
"""

import argparse
import asyncio
import json
import secrets
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import psycopg

from knock_on_atlas.agents.llm import models_from_settings
from knock_on_atlas.config import Checkpointer, get_settings
from knock_on_atlas.edgar.client import SecClient
from knock_on_atlas.jev.client import jev_from_settings
from knock_on_atlas.mcp_server.client import local_client, mcp_caller, service_token
from knock_on_atlas.mcp_server.server import TOOL_SCOPES, build_server
from knock_on_atlas.retrieval.embed import BgeEmbedder
from knock_on_atlas.workflow import budgets
from knock_on_atlas.workflow.checkpoints import checkpointer
from knock_on_atlas.workflow.deps import Deps
from knock_on_atlas.workflow.graph import build_graph, run_config
from knock_on_atlas.workflow.worker import Job, handle

OUT_DIR = Path("evals/runs")

parser = argparse.ArgumentParser()
parser.add_argument("hub_cik")
parser.add_argument("accession")
parser.add_argument("--approve", action="store_true")
parser.add_argument("--analysis-id")
args = parser.parse_args()
settings = get_settings()
if args.analysis_id and settings.checkpointer is Checkpointer.MEMORY:
    # A memory checkpoint died with the last process: this would rerun from scratch.
    parser.error("--analysis-id needs CHECKPOINTER=dynamodb to resume a run")
wall_clock = settings.run_wall_clock_seconds or budgets.WALL_CLOCK_SECONDS


async def main() -> dict[str, Any]:
    with psycopg.connect(
        settings.database_url.get_secret_value(), autocommit=True
    ) as conn:
        sec = SecClient(settings.sec_user_agent, Path("data/raw/sec_cache"))
        # Server and worker share this process, so the signing key never leaves it.
        secret = secrets.token_urlsafe(32)
        server = build_server(conn, sec, BgeEmbedder(), secret)
        # Valid for both windows of the run: before and after the review pause.
        ttl = int(2 * wall_clock) + 60
        token = service_token(secret, set(TOOL_SCOPES.values()), "local-worker", ttl)
        async with local_client(server, token) as client:
            jev = jev_from_settings(settings)
            models = models_from_settings(settings)
            saver = checkpointer(settings)
            run_id = args.analysis_id or str(uuid.uuid4())
            job = Job(
                analysis_id=run_id, hub_cik=args.hub_cik, accession=args.accession
            )

            def invocation() -> Deps:
                # A fresh window per delivery, as a worker Lambda would get.
                deadline = time.time() + wall_clock
                return Deps(conn, models, jev, mcp_caller(client), deadline=deadline)

            began = time.time()
            status = await handle(job, invocation(), saver)
            if status == "paused" and args.approve:
                decision = {"decision": "approved", "reviewer": "local --approve"}
                job = job.model_copy(update={"decision": decision})
                status = await handle(job, invocation(), saver)
            seconds = round(time.time() - began, 1)
            graph = build_graph(invocation(), saver)
            values = (await graph.aget_state(run_config(run_id))).values

    def dump(key: str) -> Any:
        value = values.get(key)
        if isinstance(value, list):
            return [v.model_dump(mode="json") for v in value]
        return value.model_dump(mode="json") if value is not None else None

    usage = values["usage"]
    jev_tokens = sum(d.input_tokens for d in values.get("jev", []))
    return {
        "built_at": datetime.now(UTC).strftime("%Y-%m-%dT%H%MZ"),
        "run_id": run_id,
        "hub_cik": args.hub_cik,
        "accession": args.accession,
        "llm_provider": settings.llm_provider.value,
        "jev_route": settings.jev_route.value,
        "status": status,
        "seconds": seconds,
        "wall_clock_limit": wall_clock,
        "usage": usage.model_dump(),
        "limits": {
            "llm_calls": budgets.MAX_LLM_CALLS,
            "tool_calls": budgets.MAX_TOOL_CALLS,
            "tokens": budgets.MAX_TOKENS,
        },
        "within_budget": usage.llm_calls <= budgets.MAX_LLM_CALLS
        and usage.tool_calls <= budgets.MAX_TOOL_CALLS
        and usage.tokens <= budgets.MAX_TOKENS,
        "verifier_loops": values.get("loops", 0),
        "jev_input_tokens": jev_tokens,
        "triage": dump("triage"),
        "suppliers": len(values["plan"].suppliers) if "plan" in values else 0,
        "gate": dump("gate"),
        "review": dump("review"),
        "brief": dump("brief"),
        "verification": dump("verification"),
        "evidence": dump("evidence"),
        "exposures": dump("exposures"),
        "market": dump("market"),
        "jev": dump("jev"),
        "errors": values.get("errors", []),
    }


summary = asyncio.run(main())
OUT_DIR.mkdir(parents=True, exist_ok=True)
out = OUT_DIR / f"{summary['built_at']}.json"
out.write_text(json.dumps(summary, indent=2, default=str) + "\n")
print(
    json.dumps(
        {
            k: summary[k]
            for k in (
                "status",
                "seconds",
                "usage",
                "within_budget",
                "jev_input_tokens",
                "errors",
            )
        },
        indent=2,
    )
)
print(out)
