"""Risk gate (rules first, then Jev J5) and the human review interrupt."""

import time
from typing import Any

from langgraph.graph import END
from langgraph.types import interrupt

from knock_on_atlas.jev.questions import J5_MIN, escalation
from knock_on_atlas.workflow.budgets import WALL_CLOCK_SECONDS
from knock_on_atlas.workflow.deps import Deps
from knock_on_atlas.workflow.state import Gate, KOAState, ReviewDecision

# Spec J5 rule: a hit to ≥10% of a direct supplier's revenue always gets a reviewer.
HIGH_IMPACT_PCT = 10.0


async def risk_gate(state: KOAState, deps: Deps) -> dict[str, Any]:
    triage, plan = state["triage"], state["plan"]
    reasons = []
    if triage.label == "MONITOR":
        reasons.append(f"triage uncertain ({triage.reason})")
    if state.get("errors"):
        reasons.append("the run recorded gaps: " + "; ".join(state["errors"]))
    big = [
        s.cik for s in plan.suppliers if s.depth == 1 and s.weight >= HIGH_IMPACT_PCT
    ]
    if triage.label == "MATERIAL_NEGATIVE" and big:
        reasons.append(
            f"negative news for suppliers with ≥{HIGH_IMPACT_PCT:.0f}% of revenue"
            f" from the filer: {', '.join(big)}"
        )
    if reasons:
        return {"gate": Gate(route="HUMAN_REVIEW", reasons=reasons)}
    if deps.jev is None:
        return {
            "gate": Gate(
                route="HUMAN_REVIEW", reasons=["Jev off: no automatic publishing"]
            )
        }

    evidence = {f"e{i}": e.quote for i, e in enumerate(state.get("evidence", []))}
    jev_state = {
        "filing": state["event"].excerpt,
        "suppliers": {x.name: x.note for x in state.get("exposures", [])},
        "evidence": evidence,
    }
    answer, p, call = escalation(deps.jev, jev_state)
    route = answer if p >= J5_MIN else "HUMAN_REVIEW"
    gate = Gate(route=route, reasons=[f"J5 {answer} p={p:.2f}"])
    return {"gate": gate, "jev": [deps.log_jev(call, "j5", answer, p)]}


def route_after_risk(state: KOAState) -> str:
    return (
        "brief_synthesizer" if state["gate"].route == "AUTO_PUBLISH" else "human_review"
    )


def human_review(state: KOAState) -> dict[str, Any]:
    """Pause until a reviewer decides; the run resumes with their decision."""
    report = state.get("verification")
    decision = interrupt(
        {
            "run_id": state.get("run_id"),
            "reasons": state["gate"].reasons
            if "gate" in state
            else ["verification failed"],
            "exposures": [e.model_dump() for e in state.get("exposures", [])],
            "failed_suppliers": report.failed_suppliers if report else [],
        }
    )
    # A reviewer's wait is not compute: the rest of the run gets a fresh window.
    return {
        "review": ReviewDecision.model_validate(decision),
        "deadline": time.time() + state.get("wall_clock", WALL_CLOCK_SECONDS),
    }


def route_after_review(state: KOAState) -> str:
    return "brief_synthesizer" if state["review"].decision == "approved" else END
