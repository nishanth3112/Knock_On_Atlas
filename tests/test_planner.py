import asyncio
from datetime import UTC, datetime
from typing import Any

import psycopg
from fakes import approved_edge, call, scripted

from knock_on_atlas.agents.llm import Models
from knock_on_atlas.agents.planner import planner
from knock_on_atlas.db.migrate import migrate
from knock_on_atlas.graph_store.edges import replace_edges
from knock_on_atlas.mcp_server.server import EightK
from knock_on_atlas.workflow.deps import Deps
from knock_on_atlas.workflow.state import KOAState

HUB, S1, S2, OTHER = "0000037996", "0000000001", "0000000002", "0000000009"
EVENT = EightK(
    cik=HUB,
    accession="0000037996-25-000238",
    accepted_at=datetime(2025, 12, 15, 21, 7, tzinfo=UTC),
    items=["2.06"],
    url="https://www.sec.gov/x.htm",
    excerpt="Item 2.06. Ford takes EV charges and cuts battery plans.",
    exhibit_url=None,
    exhibit_excerpt=None,
)


async def no_tools(name: str, args: dict[str, Any]) -> Any:
    raise AssertionError("the planner calls no tools")


def plan_with(db: psycopg.Connection, llm: Any) -> dict[str, Any]:
    state: KOAState = {
        "hub_cik": HUB,
        "hub_name": "Ford Motor Co",
        "event": EVENT,
    }
    deps = Deps(db, Models(llm, llm, llm), None, no_tools)
    return asyncio.run(planner(state, deps))


def test_scope_is_the_traversal_and_the_llm_only_writes_queries(
    db: psycopg.Connection,
) -> None:
    migrate(db)
    db.execute(
        "INSERT INTO companies (cik, name) VALUES (%s, 'Ford'), (%s, 'Gentherm'),"
        " (%s, 'Tier Two'), (%s, 'Elsewhere')",
        (HUB, S1, S2, OTHER),
    )
    # S2 reaches Ford through S1; OTHER supplies nobody in the graph.
    replace_edges(db, [approved_edge(S1, HUB, 22), approved_edge(S2, S1, 40)])
    llm = scripted(
        call(
            "submit",
            queries=["Ford share of revenue", "EV program cuts", "battery", "extra"],
            ciks=[OTHER],  # not in the schema: ignored
        )
    )
    update = plan_with(db, llm)

    plan = update["plan"]
    assert plan.supplier_ciks == [S1, S2]
    assert [(s.cik, s.depth, s.via_cik) for s in plan.suppliers] == [
        (S1, 1, HUB),
        (S2, 2, S1),
    ]
    assert plan.queries == ["Ford share of revenue", "EV program cuts", "battery"]
    assert plan.as_of.isoformat() == "2025-12-15"  # 16:07 ET
    assert update["usage"].llm_calls == 1
    task = str(llm.__dict__["seen"][0][1].content)
    assert "Gentherm" in task and "<data>" in task


def test_no_suppliers_is_a_gap_and_skips_the_llm(db: psycopg.Connection) -> None:
    migrate(db)
    update = plan_with(db, scripted())
    assert update["plan"].suppliers == [] and update["plan"].queries == []
    assert "no approved suppliers" in update["errors"][0]
