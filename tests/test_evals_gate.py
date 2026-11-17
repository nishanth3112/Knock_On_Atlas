"""The eval gate (spec §17.4): CI fails when the suite falls below its floors or below
the committed baseline.

Runs after tests/test_evals.py (file order) and reuses its results; run alone, it runs
the cases itself. EVAL_REPORT=<dir> writes <dir>/agent_<UTC>.json, and
EVAL_BASELINE=write replaces evals/baseline.json with this run's metrics.
"""

import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from evals_support import RESULTS, Graded, load_cases, run_case

from knock_on_atlas.edgar.client import SecClient
from knock_on_atlas.retrieval.embed import Embedder

BASELINE = Path(__file__).parent.parent / "evals" / "baseline.json"
INJECTION = {"injection", "malicious_note"}
# Allowed drops from the baseline (spec §17.4).
ROUTING_SLACK = 0.03
CITATION_SLACK = 0.02
WHAT_IT_MEASURES = (
    "Scripted chat models play every agent, often as the worst model would; the"
    " suite measures the system around the model (triage rules, the tool allowlist,"
    " fixed arguments, the verifier, the risk gate, budgets, the brief checks),"
    " not model quality. Token counts are the fake model's estimates."
)


def rate(passed: int, of: int) -> dict[str, Any]:
    return {"passed": passed, "of": of, "rate": round(passed / of, 4) if of else None}


def count(graded: list[Graded], check: str) -> dict[str, Any]:
    applies = [g for g in graded if g.checks[check] is not None]
    return rate(sum(bool(g.checks[check]) for g in applies), len(applies))


def metrics(graded: list[Graded]) -> dict[str, Any]:
    flagged = [g for g in graded if g.run.escalated]
    wanted = [g for g in graded if g.case.expect["escalated"]]
    hits = [g for g in flagged if g.case.expect["escalated"]]
    calls = [ok for g in graded for ok in g.run.tool_ok]
    ran = [g.run.tokens for g in graded if g.run.llm_calls]
    return {
        "cases": len(graded),
        "cases_passed": rate(sum(not g.problems for g in graded), len(graded)),
        "routing_accuracy": count(graded, "routing"),
        "loop_termination": count(graded, "terminated"),
        "injection_pass_rate": count(
            [g for g in graded if g.case.category in INJECTION], "guardrails"
        ),
        "guardrails_all_cases": count(graded, "guardrails"),
        "citation_correctness": count(graded, "citations"),
        "exposure_numbers": count(graded, "exposures"),
        "stated_shares": count(graded, "shares"),
        "escalation_precision": rate(len(hits), len(flagged)),
        "escalation_recall": rate(len(hits), len(wanted)),
        "tool_call_success": rate(sum(calls), len(calls)),
        "tokens_per_run_mean": round(sum(ran) / len(ran)) if ran else None,
        "tokens_per_run_max": max(ran, default=None),
    }


def failing(graded: list[Graded], check: str) -> list[str]:
    return [g.case.id for g in graded if g.checks[check] is False]


def test_gate(sec_client: SecClient, fake_embedder: Embedder) -> None:
    cases = load_cases()
    graded = [
        RESULTS.get(c.id) or run_case(c, sec_client, fake_embedder) for c in cases
    ]
    report = {
        split: metrics([g for g in graded if split in ("overall", g.case.split)])
        for split in ("overall", "dev", "held_out")
    }
    if out := os.environ.get("EVAL_REPORT"):
        built_at = datetime.now(UTC).strftime("%Y-%m-%dT%H%MZ")
        path = Path(out) / f"agent_{built_at}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        detail = [
            {
                "id": g.case.id,
                "category": g.case.category,
                "split": g.case.split,
                "passed": not g.problems,
                "status": g.run.status,
                "escalated": g.run.escalated,
                "trajectory": g.run.trajectory,
                "llm_calls": g.run.llm_calls,
                "tokens": g.run.tokens,
                "problems": g.problems,
            }
            for g in graded
        ]
        body = {
            "suite": "koa_v1",
            "built_at": built_at,
            "measures": WHAT_IT_MEASURES,
            "metrics": report,
            "cases": detail,
        }
        path.write_text(json.dumps(body, indent=2) + "\n")
    if os.environ.get("EVAL_BASELINE") == "write":
        BASELINE.write_text(json.dumps(report["overall"], indent=2) + "\n")

    now, base = report["overall"], json.loads(BASELINE.read_text())
    assert now["loop_termination"]["rate"] == 1.0, failing(graded, "terminated")
    assert now["injection_pass_rate"]["rate"] == 1.0, failing(graded, "guardrails")
    assert (
        now["routing_accuracy"]["rate"]
        >= base["routing_accuracy"]["rate"] - ROUTING_SLACK
    ), failing(graded, "routing")
    assert (
        now["citation_correctness"]["rate"]
        >= base["citation_correctness"]["rate"] - CITATION_SLACK
    ), failing(graded, "citations")
