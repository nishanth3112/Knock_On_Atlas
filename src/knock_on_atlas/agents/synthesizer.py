"""Agent 5: writes the cited brief from verified evidence only; no tools."""

import re
from typing import Any

from knock_on_atlas.agents.loop import run_agent
from knock_on_atlas.jev.questions import J4_PASS
from knock_on_atlas.workflow.deps import Deps
from knock_on_atlas.workflow.state import ExposureBrief, KOAState, Usage

SYSTEM = (
    "You write a short exposure brief for analysts: which suppliers a customer's"
    " 8-K affects and why. Use only the facts given. Every claim cites the chunk"
    " ids of the quotes it relies on, and every number must come from the facts."
    " Describe exposure; never recommend buying, selling or shorting a security,"
    " and never give price targets."
)
# Not investment advice: the brief may not tell anyone to trade (spec §19).
ADVICE = re.compile(
    r"(?i)\b(price target|buy rating|sell rating|we recommend|investors should"
    r"|(buy|sell|short)\s+(the\s+)?(stock|shares)|overweight|underweight"
    r"|outperform|underperform)\b"
)
_NUMBER = re.compile(r"\d+(?:\.\d+)?")


def numbers(text: str) -> set[str]:
    return set(_NUMBER.findall(text.replace(",", "")))


def problems(brief: ExposureBrief, cited: set[int], facts: str) -> list[str]:
    found = []
    known = numbers(facts)
    for text in [brief.headline, *(c.text for c in brief.claims)]:
        if ADVICE.search(text):
            found.append(f"investment advice in: {text!r}")
        if unknown := numbers(text) - known:
            found.append(f"numbers not in the facts: {sorted(unknown)} in {text!r}")
    for claim in brief.claims:
        if not claim.citations:
            found.append(f"uncited claim: {claim.text!r}")
        elif bad := set(claim.citations) - cited:
            found.append(f"cites unverified chunks {sorted(bad)}: {claim.text!r}")
    if not brief.claims:
        found.append("the brief has no claims")
    return found


async def brief_synthesizer(state: KOAState, deps: Deps) -> dict[str, Any]:
    report = state.get("verification")
    good = {
        (c.supplier_cik, c.chunk_id)
        for c in (report.checks if report else [])
        if c.citation_ok and c.numbers_ok and c.score >= J4_PASS
    }
    quotes = [
        e for e in state.get("evidence", []) if (e.supplier_cik, e.chunk_id) in good
    ]
    lines = [
        f"Customer: {state['hub_name']}",
        f"8-K:\n{state['event'].excerpt}",
        "Suppliers:",
    ]
    lines += [
        f"- {x.name} ({x.supplier_cik}), depth {x.depth}, {x.weight:g}% of revenue"
        f" from {'the customer' if x.depth == 1 else 'its own customer'}. {x.note}"
        for x in state.get("exposures", [])
    ]
    lines.append("Verified quotes:")
    lines += [f"- chunk {e.chunk_id} ({e.supplier_cik}): {e.quote}" for e in quotes]
    market = [
        f"- {r.cik}: CAR[0,+1] {r.car_0_1 * 100:.1f}%, t {r.t_0_1:.1f}"
        for r in state.get("market", [])
        if r.car_0_1 is not None and r.t_0_1 is not None
    ]
    if market:
        lines += ["Abnormal returns (descriptive, not a signal):", *market]
    facts = "\n".join(lines)
    task = f"<data>\n{facts}\n</data>\nWrite the brief."
    cited = {e.chunk_id for e in quotes}

    usage = Usage()
    found: list[str] = []
    for _ in range(2):  # one retry with the reasons it was rejected
        run = await run_agent(
            deps.models.hard,
            SYSTEM,
            task,
            [],
            ExposureBrief,
            deps.call_tool,
            state.get("usage", Usage()) + usage,
            state["deadline"],
        )
        usage += run.usage
        if run.output is None:
            return {"usage": usage, "errors": [f"brief_synthesizer: {run.error}"]}
        found = problems(run.output, cited, facts)
        if not found:
            gaps = [
                f"no verified evidence for {c}"
                for c in (report.failed_suppliers if report else [])
            ]
            brief = run.output.model_copy(update={"gaps": run.output.gaps + gaps})
            return {"brief": brief, "usage": usage}
        task += "\nYour last brief was rejected:\n" + "\n".join(f"- {p}" for p in found)
    return {
        "usage": usage,
        "errors": ["brief_synthesizer: rejected twice: " + "; ".join(found)],
    }
