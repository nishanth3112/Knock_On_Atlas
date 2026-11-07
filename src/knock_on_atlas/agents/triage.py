"""Agent 1, part 1: 8-K item rules first, Jev J1 only for free-text items."""

from typing import Any

from langgraph.graph import END

from knock_on_atlas.jev.questions import J1_MIN, materiality, materiality_state
from knock_on_atlas.mcp_server.server import EightK
from knock_on_atlas.workflow.deps import Deps
from knock_on_atlas.workflow.state import KOAState, Triage, Usage

# Items that always mean trouble for suppliers; Jev never overrides them (spec §12).
RULE_ITEMS = {
    "1.03": "bankruptcy or receivership",
    "2.05": "exit or disposal costs",
    "2.06": "material impairment",
    "4.02": "non-reliance on financial statements",
}
# Items whose effect depends on the text, so J1 reads it.
JEV_ITEMS = {"1.01", "1.02", "2.01", "7.01", "8.01"}
ANALYZED = {"MATERIAL_NEGATIVE", "MATERIAL_POSITIVE", "MONITOR"}


async def triage(state: KOAState, deps: Deps) -> dict[str, Any]:
    args = {"cik": state["hub_cik"], "accession": state["accession"]}
    event_data = await deps.call_tool("get_8k_items", args)
    company = await deps.call_tool("get_company", {"cik": state["hub_cik"]})
    usage = Usage(tool_calls=2)
    if "error" in event_data or "error" in company:
        return {
            "usage": usage,
            "errors": [str(event_data.get("error") or company["error"])],
        }
    event = EightK.model_validate(event_data)
    update: dict[str, Any] = {
        "event": event,
        "hub_name": company["name"],
        "usage": usage,
    }
    items = set(event.items)
    if rule := sorted(RULE_ITEMS.keys() & items):
        reason = f"item {rule[0]}: {RULE_ITEMS[rule[0]]}"
        update["triage"] = Triage(
            label="MATERIAL_NEGATIVE", method="rule", reason=reason
        )
    elif not JEV_ITEMS & items:
        update["triage"] = Triage(
            label="ROUTINE", method="rule", reason="no material item"
        )
    elif deps.jev is None:
        update["triage"] = Triage(label="MONITOR", method="fallback", reason="Jev off")
    else:
        jev_state = materiality_state(company["name"], event_data)
        label, p, call = materiality(deps.jev, jev_state)
        update["jev"] = [deps.log_jev(call, "j1", label, p)]
        confident = p >= J1_MIN
        update["triage"] = Triage(
            label=label if confident else "MONITOR",
            method="jev",
            probability=p,
            reason=f"J1 {label} p={p:.2f}",
        )
    return update


def route_after_triage(state: KOAState) -> str:
    triage = state.get("triage")
    return "planner" if triage is not None and triage.label in ANALYZED else END
