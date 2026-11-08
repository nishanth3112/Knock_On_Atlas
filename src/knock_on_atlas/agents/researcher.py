"""Agent 3: finds quotable evidence in the suppliers' own filings."""

from datetime import date
from typing import Any

from pydantic import BaseModel, create_model

from knock_on_atlas.agents.loop import AgentTool, run_agent, scoped_cik_args
from knock_on_atlas.workflow.budgets import branch_limit
from knock_on_atlas.workflow.deps import Deps
from knock_on_atlas.workflow.state import Evidence, KOAState, Usage

# Five passages a search keep a tool result near 2k tokens.
SEARCH_HITS = 5

SYSTEM = (
    "You are an evidence researcher. Search the suppliers' 10-K and 10-Q filings"
    " for passages stating how much revenue each supplier gets from the customer or"
    " how the customer's plans affect it. Submit exact quotes copied from those"
    " passages, each with the chunk_id it came from. If no passage names the"
    " customer, search again with different words; if that finds nothing either,"
    " submit an empty list."
)


class Query(BaseModel):
    query: str


class SectionPage(BaseModel):
    accession: str
    section: str
    page: int = 0


class Quote(BaseModel):
    supplier_cik: str
    chunk_id: int
    quote: str


class Found(BaseModel):
    evidence: list[Quote]


def list_filings_args(ciks: list[str]) -> type[BaseModel]:
    return create_model(
        "FilingsArgs",
        __base__=scoped_cik_args(ciks),
        forms=(list[str], ["10-K", "10-Q"]),
        since=(date, ...),
    )


def seen_chunks(calls: list[tuple[str, dict[str, Any], Any]]) -> dict[int, str]:
    """chunk_id -> url for every passage a tool actually returned to the agent."""
    urls: dict[int, str] = {}
    for name, _, result in calls:
        if name == "search_filings":
            for hit in result.get("result", []):
                urls[hit["chunk_id"]] = hit["url"]
        elif name == "get_filing_section" and "chunk_id" in result:
            urls[result["chunk_id"]] = result["url"]
    return urls


async def evidence_researcher(state: KOAState, deps: Deps) -> dict[str, Any]:
    plan = state["plan"]
    report = state.get("verification")
    # A verifier loop re-researches only the suppliers whose evidence failed.
    retry = report is not None and bool(report.failed_suppliers)
    ciks = report.failed_suppliers if report and retry else plan.supplier_ciks
    tools = [
        AgentTool(
            "search_filings",
            "Search the suppliers' 10-K and 10-Q sections.",
            Query,
            fixed={"ciks": ciks, "as_of": plan.as_of.isoformat(), "k": SEARCH_HITS},
        ),
        AgentTool(
            "get_filing_section",
            "Read one page of a filing section.",
            SectionPage,
            fixed={"ciks": ciks},
        ),
        AgentTool(
            "list_filings", "A supplier's recent filings.", list_filings_args(ciks)
        ),
    ]
    task = (
        f"Customer: {state['hub_name']}\n"
        f"Its 8-K:\n<data>\n{state['event'].excerpt}\n</data>\n"
        f"Suppliers (CIK): {', '.join(ciks)}\n"
        f"Start with these queries: {'; '.join(plan.queries)}"
    )
    if retry:
        task += "\nEarlier quotes for these suppliers failed verification."
    run = await run_agent(
        deps.models.reason,
        SYSTEM,
        task,
        tools,
        Found,
        deps.call_tool,
        state.get("usage", Usage()),
        deps.deadline,
        # Alongside the analyst on the first pass; alone on a verifier retry.
        limit=branch_limit(state.get("usage", Usage()), branches=1 if retry else 2),
    )
    urls = seen_chunks(run.calls)
    # A quote can only cite a passage this run was shown, for a supplier in scope.
    evidence = [
        Evidence(
            supplier_cik=q.supplier_cik,
            chunk_id=q.chunk_id,
            quote=q.quote,
            url=urls[q.chunk_id],
        )
        for q in (run.output.evidence if run.output else [])
        if q.chunk_id in urls and q.supplier_cik in ciks
    ]
    update: dict[str, Any] = {"evidence": evidence, "usage": run.usage}
    if run.error:
        update["errors"] = [f"evidence_researcher: {run.error}"]
    return update
