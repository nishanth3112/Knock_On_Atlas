import asyncio
import json
import re
import time
from typing import Any

import psycopg
from fakes import call
from langchain_core.messages import AIMessage, BaseMessage, ToolMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command
from pydantic import BaseModel
from test_mcp_server import ALL_SCOPES, FORD, FORD_8K, SECRET, SUPPLIER, seeded_server

from knock_on_atlas.agents.llm import FakeChatModel, Models
from knock_on_atlas.edgar.client import SecClient
from knock_on_atlas.mcp_server.client import local_client, mcp_caller, service_token
from knock_on_atlas.retrieval.embed import Embedder
from knock_on_atlas.workflow import state as state_module
from knock_on_atlas.workflow.budgets import (
    MAX_LLM_CALLS,
    MAX_TOKENS,
    MAX_TOOL_CALLS,
    WALL_CLOCK_SECONDS,
)
from knock_on_atlas.workflow.deps import Deps
from knock_on_atlas.workflow.graph import (
    STATE_MODELS,
    build_graph,
    checkpoint_serde,
    run_config,
    start,
)

QUOTE = "Ford accounted for 22% of our product revenues."


def role_responder(messages: list[BaseMessage], tools: list[str]) -> AIMessage:
    """Plays each agent by its system prompt, so parallel branches can't race."""
    system = str(messages[0].content)
    results = [str(m.content) for m in messages if isinstance(m, ToolMessage)]
    task = str(messages[1].content)
    if system.startswith("You plan research"):
        return call("submit", queries=["Ford percentage of revenues"])
    if system.startswith("You are an exposure analyst"):
        if not results:
            return call("get_concentration_facts", cik=SUPPLIER)
        return call(
            "submit",
            exposures=[
                {"supplier_cik": SUPPLIER, "note": "Ford is its largest customer."}
            ],
        )
    if system.startswith("You are an evidence researcher"):
        if not results:
            return call("search_filings", query="Ford percentage of revenues")
        hits = json.loads(results[-1].split("\n", 1)[1].rsplit("\n", 1)[0])["result"]
        hit = next(h for h in hits if QUOTE in h["content"])
        return call(
            "submit",
            evidence=[
                {"supplier_cik": SUPPLIER, "chunk_id": hit["chunk_id"], "quote": QUOTE}
            ],
        )
    if system.startswith("You write a short exposure brief"):
        chunk = int(re.search(r"chunk (\d+)", task).group(1))  # type: ignore[union-attr]
        claim = {
            "text": "Ford accounted for 22% of Gentherm's product revenues.",
            "supplier_ciks": [SUPPLIER],
            "citations": [chunk],
        }
        return call(
            "submit",
            headline="Gentherm is exposed to Ford's EV charges",
            claims=[claim],
            gaps=[],
        )
    raise AssertionError(f"unexpected prompt: {system[:40]}")


def test_a_material_8k_ends_in_a_cited_brief_within_budget(
    db: psycopg.Connection, sec_client: SecClient, fake_embedder: Embedder
) -> None:
    server = seeded_server(db, sec_client, fake_embedder)
    llm = FakeChatModel(responder=role_responder)

    async def go() -> tuple[list[list[str]], dict[str, Any]]:
        async with local_client(
            server, service_token(SECRET, ALL_SCOPES, "worker")
        ) as client:
            deps = Deps(db, Models(llm, llm, llm), None, mcp_caller(client))
            graph = build_graph(deps, InMemorySaver(serde=checkpoint_serde()))
            config = run_config("run-1")
            steps = [
                list(update)
                async for update in graph.astream(
                    start("run-1", FORD, FORD_8K), config, stream_mode="updates"
                )
            ]
            # Item 2.06 for a 22% supplier always pauses for a reviewer.
            assert steps[-1] == ["__interrupt__"]
            resume = Command(resume={"decision": "approved", "reviewer": "test"})
            steps += [
                list(u)
                async for u in graph.astream(resume, config, stream_mode="updates")
            ]
            return steps, (await graph.aget_state(config)).values

    steps, final = asyncio.run(go())

    order = [node for step in steps for node in step]
    # The three branches run in one superstep, and the verifier runs once after them.
    assert order[:2] == ["triage", "planner"]
    assert sorted(order[2:5]) == [
        "event_study",
        "evidence_researcher",
        "exposure_analyst",
    ]
    assert order[5:] == [
        "verifier",
        "risk_gate",
        "__interrupt__",
        "human_review",
        "brief_synthesizer",
    ]

    brief = final["brief"]
    [chunk_id] = brief.claims[0].citations
    assert {e.chunk_id for e in final["evidence"]} == {chunk_id}
    assert final["verification"].ok and final["triage"].method == "rule"
    assert {r.status for r in final["market"]} == {
        "pending"
    }  # no closes in the test DB
    usage = final["usage"]
    assert usage.llm_calls <= MAX_LLM_CALLS and usage.tool_calls <= MAX_TOOL_CALLS
    assert usage.tokens <= MAX_TOKENS
    assert final.get("errors", []) == []


def test_a_run_carries_its_wall_clock() -> None:
    assert start("r", FORD, FORD_8K)["wall_clock"] == WALL_CLOCK_SECONDS
    slow = start("r", FORD, FORD_8K, wall_clock=900)
    assert slow["wall_clock"] == 900
    assert 899 < slow["deadline"] - time.time() <= 900


def test_checkpoints_may_revive_every_state_model() -> None:
    # A model missing here would be refused when a paused run is resumed.
    models = {
        obj
        for obj in vars(state_module).values()
        if isinstance(obj, type) and issubclass(obj, BaseModel) and obj is not BaseModel
    }
    assert models <= set(STATE_MODELS)
