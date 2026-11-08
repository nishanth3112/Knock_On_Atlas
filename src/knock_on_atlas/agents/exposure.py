"""Agent 2: explains why each supplier in scope is exposed. Numbers come from the DB."""

from typing import Any

from pydantic import BaseModel

from knock_on_atlas.agents.loop import AgentTool, NoArgs, run_agent, scoped_cik_args
from knock_on_atlas.workflow.budgets import branch_limit
from knock_on_atlas.workflow.deps import Deps
from knock_on_atlas.workflow.state import ExposureEdge, KOAState, Usage

SYSTEM = (
    "You are an exposure analyst. The customer below filed an 8-K. For each"
    " supplier, check its disclosed customer revenue shares, then write one sentence"
    " on why the customer's news matters to it. Submit one entry per supplier."
)


class Note(BaseModel):
    supplier_cik: str
    note: str


class Notes(BaseModel):
    exposures: list[Note]


async def exposure_analyst(state: KOAState, deps: Deps) -> dict[str, Any]:
    plan = state["plan"]
    ciks = plan.supplier_ciks
    tools = [
        AgentTool(
            "get_exposure_edges",
            "The customer's suppliers within two hops, with revenue weights.",
            NoArgs,
            fixed={"hub_cik": plan.hub_cik, "as_of": plan.as_of.isoformat()},
        ),
        AgentTool(
            "get_concentration_facts",
            "A supplier's disclosed customer revenue shares, newest first.",
            scoped_cik_args(ciks),
        ),
        AgentTool(
            "get_company",
            "A supplier's name, industry and tickers.",
            scoped_cik_args(ciks),
        ),
    ]
    names: dict[str, str] = dict(
        deps.conn.execute(
            "SELECT cik, name FROM companies WHERE cik = ANY(%s)", (ciks,)
        ).fetchall()
    )
    task = (
        f"Customer: {state['hub_name']}\n"
        f"Its 8-K:\n<data>\n{state['event'].excerpt}\n</data>\n"
        "Suppliers: " + ", ".join(f"{names.get(c, c)} ({c})" for c in ciks)
    )
    run = await run_agent(
        deps.models.reason,
        SYSTEM,
        task,
        tools,
        Notes,
        deps.call_tool,
        state.get("usage", Usage()),
        deps.deadline,
        limit=branch_limit(state.get("usage", Usage()), branches=2),
    )
    notes = {
        n.supplier_cik: n.note for n in (run.output.exposures if run.output else [])
    }
    # Depth and weight are copied from the traversal: the model only writes notes.
    exposures = [
        ExposureEdge(
            supplier_cik=s.cik,
            name=names.get(s.cik, s.cik),
            depth=s.depth,
            weight=s.weight,
            note=notes.get(s.cik, ""),
        )
        for s in plan.suppliers
    ]
    update: dict[str, Any] = {"exposures": exposures, "usage": run.usage}
    if run.error:
        update["errors"] = [f"exposure_analyst: {run.error}"]
    return update
