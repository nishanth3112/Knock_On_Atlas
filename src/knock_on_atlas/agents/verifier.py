"""Agent 4: checks every quote against the stored filing and the graph, then J4."""

import re
from typing import Any

from knock_on_atlas.jev.questions import J4_PASS, sufficiency
from knock_on_atlas.workflow.budgets import BUDGET_EXHAUSTED, MAX_VERIFIER_LOOPS
from knock_on_atlas.workflow.deps import Deps
from knock_on_atlas.workflow.state import (
    ClaimCheck,
    Evidence,
    KOAState,
    VerificationReport,
)

# A quoted share may round differently from the XBRL fact (22% vs 21.6%).
PCT_TOLERANCE = 0.5
_PERCENT = re.compile(r"(\d+(?:\.\d+)?)\s*(?:%|percent)")
_QUOTES = str.maketrans({"’": "'", "‘": "'", "“": '"', "”": '"'})


def normalize(text: str) -> str:
    return " ".join(text.translate(_QUOTES).split()).lower()


def numbers_match(quote: str, edge_pct: float | None) -> bool:
    """Any percentage in the quote must be the edge's share; no percentage passes."""
    shares = [float(p) for p in _PERCENT.findall(quote)]
    if not shares:
        return True
    return edge_pct is not None and any(
        abs(s - edge_pct) <= PCT_TOLERANCE for s in shares
    )


async def verifier(state: KOAState, deps: Deps) -> dict[str, Any]:
    plan = state["plan"]
    suppliers = {s.cik: s for s in plan.suppliers}
    evidence: list[Evidence] = state.get("evidence", [])
    chunk_ids = list({e.chunk_id for e in evidence})
    chunks = {
        row[0]: (row[1], row[2])
        for row in deps.conn.execute(
            "SELECT id, cik, content FROM document_chunks WHERE id = ANY(%s)",
            (chunk_ids,),
        )
    }
    # The supplier's own edge is the last one on its path.
    edge_ids = [s.edge_ids[-1] for s in plan.suppliers]
    edge_pct = {
        row[0]: None if row[1] is None else float(row[1])
        for row in deps.conn.execute(
            "SELECT id, pct FROM entity_edges WHERE id = ANY(%s)", (edge_ids,)
        )
    }
    names: dict[str, str] = dict(
        deps.conn.execute(
            "SELECT cik, name FROM companies WHERE cik = ANY(%s)",
            (list({*suppliers, *(s.via_cik for s in plan.suppliers)}),),
        ).fetchall()
    )

    earlier = state.get("verification")
    done = {
        (c.supplier_cik, c.chunk_id): c for c in (earlier.checks if earlier else [])
    }
    checks: list[ClaimCheck] = []
    to_score: dict[str, tuple[str, str, str]] = {}
    for e in evidence:
        if (e.supplier_cik, e.chunk_id) in done:
            checks.append(done[(e.supplier_cik, e.chunk_id)])
            continue
        supplier = suppliers.get(e.supplier_cik)
        chunk = chunks.get(e.chunk_id)
        cited = (
            supplier is not None
            and chunk is not None
            and chunk[0] == e.supplier_cik
            and normalize(e.quote) in normalize(chunk[1])
        )
        numbers = supplier is not None and numbers_match(
            e.quote, edge_pct.get(supplier.edge_ids[-1])
        )
        checks.append(
            ClaimCheck(
                supplier_cik=e.supplier_cik,
                chunk_id=e.chunk_id,
                citation_ok=cited,
                numbers_ok=numbers,
                # Jev off: passing both code checks is enough (J4's pass mark).
                score=J4_PASS if cited and numbers else 0,
            )
        )
        if cited and numbers and supplier is not None and deps.jev is not None:
            customer = names.get(supplier.via_cik, supplier.via_cik)
            to_score[f"c{len(checks) - 1}"] = (
                names.get(e.supplier_cik, e.supplier_cik),
                customer,
                e.quote,
            )

    update: dict[str, Any] = {}
    if to_score and deps.jev is not None:
        levels, call = sufficiency(deps.jev, to_score)
        for claim_id, level in levels.items():
            i = int(claim_id[1:])
            checks[i] = checks[i].model_copy(update={"score": level})
        mean = sum(levels.values()) / len(levels)
        update["jev"] = [deps.log_jev(call, "j4", f"mean level {mean:.1f}", None)]

    passed = {
        c.supplier_cik
        for c in checks
        if c.citation_ok and c.numbers_ok and c.score >= J4_PASS
    }
    # A depth-2 supplier's filings talk about its own customer, not the hub, so only
    # direct suppliers must be backed by evidence.
    failed = sorted(
        s.cik for s in plan.suppliers if s.depth == 1 and s.cik not in passed
    )
    report = VerificationReport(
        ok=bool(passed) and not failed, checks=checks, failed_suppliers=failed
    )
    update["verification"] = report
    if not report.ok:
        update["loops"] = state.get("loops", 0) + 1
    return update


def route_after_verify(state: KOAState) -> str:
    if state["verification"].ok:
        return "risk_gate"
    # Another research pass can't run once the budget is spent.
    if any(BUDGET_EXHAUSTED in e for e in state.get("errors", [])):
        return "human_review"
    return (
        "evidence_researcher"
        if state.get("loops", 0) <= MAX_VERIFIER_LOOPS
        else "human_review"
    )
