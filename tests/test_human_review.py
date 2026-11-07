import asyncio
from datetime import UTC, date, datetime
from typing import Any

import psycopg
import pytest
from fakes import choice, fake_jev, scripted
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command

from knock_on_atlas.agents.llm import Models
from knock_on_atlas.agents.review import (
    human_review,
    risk_gate,
    route_after_review,
    route_after_risk,
)
from knock_on_atlas.db.migrate import migrate
from knock_on_atlas.jev.client import JevClient
from knock_on_atlas.jev.questions import J5
from knock_on_atlas.mcp_server.server import EightK
from knock_on_atlas.workflow.deps import Deps
from knock_on_atlas.workflow.state import (
    ExposureBrief,
    KOAState,
    Plan,
    Supplier,
    Triage,
)

FORD, SUPPLIER = "0000037996", "0000012345"
BRIEF = ExposureBrief(headline="Gentherm exposed to Ford EV cuts", claims=[], gaps=[])


def run(
    db: psycopg.Connection,
    label: str,
    jev: JevClient | None,
    resume: dict[str, Any] | None = None,
) -> dict[str, Any]:
    migrate(db)

    async def no_tools(name: str, args: dict[str, Any]) -> Any:
        raise AssertionError("no tool calls")

    llm = scripted()
    deps = Deps(db, Models(llm, llm, llm), jev, no_tools)

    async def gate(state: KOAState) -> dict[str, Any]:
        return await risk_gate(state, deps)

    def synthesize(state: KOAState) -> dict[str, Any]:
        return {"brief": BRIEF}

    builder = StateGraph(KOAState)
    builder.add_node("risk_gate", gate)
    builder.add_node("human_review", human_review)
    builder.add_node("brief_synthesizer", synthesize)
    builder.add_edge(START, "risk_gate")
    builder.add_conditional_edges(
        "risk_gate", route_after_risk, ["brief_synthesizer", "human_review"]
    )
    builder.add_conditional_edges(
        "human_review", route_after_review, ["brief_synthesizer", END]
    )
    builder.add_edge("brief_synthesizer", END)
    graph = builder.compile(checkpointer=InMemorySaver())

    state: KOAState = {
        "run_id": "r1",
        "triage": Triage(label=label, method="rule", reason="item 2.06"),  # type: ignore[arg-type]
        "plan": Plan(
            hub_cik=FORD,
            as_of=date(2026, 1, 2),
            suppliers=[
                Supplier(cik=SUPPLIER, via_cik=FORD, depth=1, weight=22, edge_ids=[1])
            ],
            queries=[],
        ),
        "event": EightK(
            cik=FORD,
            accession="a",
            accepted_at=datetime(2025, 12, 15, tzinfo=UTC),
            items=["8.01"],
            url="u",
            excerpt="Ford adds a shift at its Kentucky plant.",
            exhibit_url=None,
            exhibit_excerpt=None,
        ),
    }
    config = {"configurable": {"thread_id": "t"}}

    async def go() -> dict[str, Any]:
        result = await graph.ainvoke(state, config)
        if resume is not None and "__interrupt__" in result:
            result = await graph.ainvoke(Command(resume=resume), config)
        return result

    return asyncio.run(go())


def test_negative_news_for_a_big_supplier_waits_for_a_reviewer(
    db: psycopg.Connection,
) -> None:
    paused = run(db, "MATERIAL_NEGATIVE", None)
    [pause] = paused["__interrupt__"]
    assert "≥10% of revenue" in pause.value["reasons"][0]
    assert "brief" not in paused

    approved = run(
        db, "MATERIAL_NEGATIVE", None, {"decision": "approved", "reviewer": "nm"}
    )
    assert approved["review"].decision == "approved" and approved["brief"] == BRIEF


def test_a_rejected_review_ends_the_run_without_a_brief(db: psycopg.Connection) -> None:
    rejected = run(
        db, "MATERIAL_NEGATIVE", None, {"decision": "rejected", "reviewer": "nm"}
    )
    assert rejected["review"].decision == "rejected" and "brief" not in rejected


@pytest.mark.parametrize(("p", "published"), [(0.95, True), (0.6, False)])
def test_j5_publishes_only_when_confident(
    db: psycopg.Connection, p: float, published: bool
) -> None:
    jev = fake_jev({J5: choice("AUTO_PUBLISH", p, ("HUMAN_REVIEW",))})
    result = run(db, "MATERIAL_POSITIVE", jev)
    assert ("brief" in result) is published
    assert ("__interrupt__" in result) is not published
    assert result["jev"][0].question == "j5"


def test_jev_off_never_publishes_on_its_own(db: psycopg.Connection) -> None:
    result = run(db, "MATERIAL_POSITIVE", None)
    assert result["gate"].reasons == ["Jev off: no automatic publishing"]
    assert "__interrupt__" in result
