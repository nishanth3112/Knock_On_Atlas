"""Agent 1, part 2: the supplier scope from one traversal query; queries from an LLM."""

from typing import Any

from pydantic import BaseModel

from knock_on_atlas.agents.loop import run_agent
from knock_on_atlas.graph_store.traversal import exposure
from knock_on_atlas.market.calendar import EASTERN
from knock_on_atlas.workflow.budgets import SYNTHESIS_RESERVE_TOKENS
from knock_on_atlas.workflow.deps import Deps
from knock_on_atlas.workflow.state import KOAState, Plan, Supplier, Usage

MAX_QUERIES = 3

SYSTEM = (
    "You plan research on how an 8-K filed by a large customer affects its"
    " suppliers. Write short search queries for passages in the suppliers' 10-K and"
    " 10-Q filings: how much revenue they get from the customer, and what the"
    " customer's news could change for them."
)


class Queries(BaseModel):
    queries: list[str]


async def planner(state: KOAState, deps: Deps) -> dict[str, Any]:
    event, hub = state["event"], state["hub_cik"]
    # The filer's local date: edges valid on the day the news came out.
    as_of = event.accepted_at.astimezone(EASTERN).date()
    suppliers: dict[str, Supplier] = {}
    for e in exposure(deps.conn, hub, as_of):  # ordered by depth, then weight
        suppliers.setdefault(
            e.supplier_cik,
            Supplier(
                cik=e.supplier_cik,
                via_cik=e.via_cik,
                depth=e.depth,
                weight=e.weight,
                edge_ids=list(e.edge_ids),
            ),
        )
    plan = Plan(
        hub_cik=hub, as_of=as_of, suppliers=list(suppliers.values()), queries=[]
    )
    if not suppliers:
        return {"plan": plan, "errors": [f"no approved suppliers of {hub} on {as_of}"]}

    names: dict[str, str] = dict(
        deps.conn.execute(
            "SELECT cik, name FROM companies WHERE cik = ANY(%s)", (list(suppliers),)
        ).fetchall()
    )
    task = (
        f"Customer: {state['hub_name']}\n"
        f"Its 8-K:\n<data>\n{event.excerpt}\n</data>\n"
        f"Suppliers: {', '.join(names.get(c, c) for c in suppliers)}\n"
        f"Write up to {MAX_QUERIES} queries."
    )
    run = await run_agent(
        deps.models.small,
        SYSTEM,
        task,
        [],
        Queries,
        deps.call_tool,
        state.get("usage", Usage()),
        state["deadline"],
        SYNTHESIS_RESERVE_TOKENS,
    )
    # Without the model's queries, the plain question still finds disclosures.
    fallback = [f"{state['hub_name']} percentage of net sales revenue customer"]
    queries = (run.output.queries if run.output else [])[:MAX_QUERIES] or fallback
    update: dict[str, Any] = {
        "plan": plan.model_copy(update={"queries": queries}),
        "usage": run.usage,
    }
    if run.error:
        update["errors"] = [f"planner: {run.error}"]
    return update
