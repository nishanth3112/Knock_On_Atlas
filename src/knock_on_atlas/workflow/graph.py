"""The analysis graph (spec §11.3): triage, plan, three parallel branches, verify,
gate, review, brief."""

import time
from collections.abc import Awaitable, Callable
from typing import Any, Protocol

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import RetryPolicy, Send

from knock_on_atlas.agents.exposure import exposure_analyst
from knock_on_atlas.agents.planner import planner
from knock_on_atlas.agents.researcher import evidence_researcher
from knock_on_atlas.agents.review import (
    human_review,
    risk_gate,
    route_after_review,
    route_after_risk,
)
from knock_on_atlas.agents.synthesizer import brief_synthesizer
from knock_on_atlas.agents.triage import route_after_triage, triage
from knock_on_atlas.agents.verifier import route_after_verify, verifier
from knock_on_atlas.market.calendar import sessions
from knock_on_atlas.market.event_study import study_event
from knock_on_atlas.mcp_server.server import EightK
from knock_on_atlas.workflow.budgets import RECURSION_LIMIT, WALL_CLOCK_SECONDS
from knock_on_atlas.workflow.deps import Deps
from knock_on_atlas.workflow.state import (
    Claim,
    ClaimCheck,
    Evidence,
    ExposureBrief,
    ExposureEdge,
    Gate,
    JevDecision,
    KOAState,
    Plan,
    ReviewDecision,
    StudyRow,
    Supplier,
    Triage,
    Usage,
    VerificationReport,
)

BRANCHES = ["exposure_analyst", "evidence_researcher", "event_study"]
Node = Callable[[KOAState, Deps], Awaitable[dict[str, Any]]]


class StateNode(Protocol):
    def __call__(self, state: KOAState) -> Awaitable[dict[str, Any]]: ...


async def event_study(state: KOAState, deps: Deps) -> dict[str, Any]:
    """Deterministic: Phase 4's market model for the filer and its suppliers."""
    event = state["event"]
    study_event(
        deps.conn,
        event.accession,
        state["hub_cik"],
        event.accepted_at,
        sessions(deps.conn),
    )
    rows = deps.conn.execute(
        "SELECT cik, depth, status, day0, car_0_1, car_m1_5, t_0_1, t_m1_5"
        " FROM event_studies WHERE accession = %s ORDER BY depth, cik",
        (event.accession,),
    ).fetchall()
    fields = [
        "cik",
        "depth",
        "status",
        "day0",
        "car_0_1",
        "car_m1_5",
        "t_0_1",
        "t_m1_5",
    ]
    return {"market": [StudyRow(**dict(zip(fields, row, strict=True))) for row in rows]}


def fan_out(state: KOAState) -> list[Send] | str:
    # Every branch gets the same plan, so all three work from one supplier scope.
    if not state["plan"].suppliers:
        return END
    return [Send(branch, state) for branch in BRANCHES]


def build_graph(
    deps: Deps, checkpointer: BaseCheckpointSaver[Any] | None = None
) -> CompiledStateGraph[Any]:
    def bind(node: Node) -> StateNode:
        async def run(state: KOAState) -> dict[str, Any]:
            return await node(state, deps)

        return run

    retry = RetryPolicy(max_attempts=3)
    builder = StateGraph(KOAState)
    builder.add_node("triage", bind(triage))
    builder.add_node("planner", bind(planner))
    builder.add_node("exposure_analyst", bind(exposure_analyst), retry_policy=retry)
    builder.add_node(
        "evidence_researcher", bind(evidence_researcher), retry_policy=retry
    )
    builder.add_node("event_study", bind(event_study), retry_policy=retry)
    builder.add_node("verifier", bind(verifier))
    builder.add_node("risk_gate", bind(risk_gate))
    builder.add_node("human_review", human_review)
    builder.add_node("brief_synthesizer", bind(brief_synthesizer))

    builder.add_edge(START, "triage")
    builder.add_conditional_edges("triage", route_after_triage, ["planner", END])
    builder.add_conditional_edges("planner", fan_out, [*BRANCHES, END])
    for branch in BRANCHES:
        builder.add_edge(branch, "verifier")
    builder.add_conditional_edges(
        "verifier",
        route_after_verify,
        ["risk_gate", "evidence_researcher", "human_review"],
    )
    builder.add_conditional_edges(
        "risk_gate", route_after_risk, ["brief_synthesizer", "human_review"]
    )
    builder.add_conditional_edges(
        "human_review", route_after_review, ["brief_synthesizer", END]
    )
    builder.add_edge("brief_synthesizer", END)
    return builder.compile(checkpointer=checkpointer)


STATE_MODELS = [
    EightK, Triage, Supplier, Plan, ExposureEdge, Evidence, StudyRow, ClaimCheck,
    VerificationReport, JevDecision, Gate, ReviewDecision, Claim, ExposureBrief, Usage,
]  # fmt: skip


def checkpoint_serde() -> JsonPlusSerializer:
    """Checkpoints may revive only the run's own state models, nothing else."""
    allowed = [(m.__module__, m.__name__) for m in STATE_MODELS]
    return JsonPlusSerializer(allowed_msgpack_modules=allowed)


def start(
    run_id: str, hub_cik: str, accession: str, wall_clock: float = WALL_CLOCK_SECONDS
) -> KOAState:
    return {
        "run_id": run_id,
        "hub_cik": hub_cik,
        "accession": accession,
        "deadline": time.time() + wall_clock,
        "wall_clock": wall_clock,
        "loops": 0,
        "usage": Usage(),
    }


def run_config(thread_id: str) -> RunnableConfig:
    return {
        "configurable": {"thread_id": thread_id},
        "recursion_limit": RECURSION_LIMIT,
    }
