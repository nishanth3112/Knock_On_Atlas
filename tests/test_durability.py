"""Durability suite: kill a worker at each step, redeliver the job in a fresh process.

Every scenario is a list of deliveries of one job (start, or the reviewer's
approval); a delivery may carry a crash point. The baseline runs uncrashed, and
every scenario must end with the baseline's brief, published once.

With DURABILITY_REPORT=<dir>, the measured numbers are also written to
<dir>/<UTC timestamp>.json (evals/durability is the committed home).
"""

import json
import os
import statistics
import subprocess
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import psycopg
from test_mcp_server import FORD, FORD_8K, seeded_server

from knock_on_atlas.config import get_settings
from knock_on_atlas.edgar.client import SecClient
from knock_on_atlas.retrieval.embed import Embedder
from knock_on_atlas.workflow.deps import CRASH_EXIT

CHILD = Path(__file__).with_name("durability_child.py")
APPROVE = {"decision": "approved", "reviewer": "suite"}
START, REVIEW = "start", "review"

# Crash points before the review pause are hit on the start job, later ones on
# the review job; the delivery after a crash is the redelivery.
SCENARIOS: dict[str, list[tuple[str, str | None]]] = {
    "baseline": [(START, None), (REVIEW, None)],
    **{
        point: [(START, point), (START, None), (REVIEW, None)]
        for point in [
            "triage:after",
            "planner:after",
            "exposure_analyst:after",
            "evidence_researcher:after",
            "event_study:before",
            "verifier:after",
            "risk_gate:after",
        ]
    },
    **{
        point: [(START, None), (REVIEW, point), (REVIEW, None)]
        for point in ["brief_synthesizer:after", "publish:after"]
    },
    "paused job redelivered": [(START, None), (START, None), (REVIEW, None)],
    "finished job redelivered": [(START, None), (REVIEW, None), (REVIEW, None)],
}

# LLM calls a crash may cost: the killed step's own. The analyst and researcher
# run in one parallel step (two calls each), so killing one also kills the
# other's work in flight; event_study runs beside them too.
PARALLEL_STEP = 4
MAX_RERUN = {
    "planner:after": 1,
    "exposure_analyst:after": PARALLEL_STEP,
    "evidence_researcher:after": PARALLEL_STEP,
    "event_study:before": PARALLEL_STEP,
    "brief_synthesizer:after": 1,
}


def run_scenario(
    db: psycopg.Connection, table: str, tmp: Path, name: str
) -> dict[str, Any]:
    analysis_id = str(uuid.uuid4())
    calls, writes = tmp / f"{analysis_id}.calls", tmp / f"{analysis_id}.writes"
    calls.touch()
    writes.touch()
    schema = db.execute("SELECT current_schema()").fetchone()
    assert schema is not None
    published: list[tuple[Any, ...]] = []  # the row after each delivery, once published
    exits = []
    for kind, crash_at in SCENARIOS[name]:
        job = {"analysis_id": analysis_id, "hub_cik": FORD, "accession": FORD_8K}
        if kind == REVIEW:
            job["decision"] = APPROVE  # type: ignore[assignment]
        env = {
            "PATH": os.environ["PATH"],
            "DATABASE_URL": get_settings().database_url.get_secret_value(),
            "KOA_SCHEMA": schema[0],
            "KOA_TABLE": table,
            "KOA_JOB": json.dumps(job),
            "KOA_CALLS": str(calls),
            "KOA_WRITES": str(writes),
            "KOA_CRASH_AT": crash_at or "",
        }
        done = subprocess.run(
            [sys.executable, str(CHILD)],
            env=env,
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
        exits.append(done.returncode)
        expected = CRASH_EXIT if crash_at else 0
        # All of it: the server-side cause of a failed tool call is logged early.
        assert done.returncode == expected, done.stderr
        row = db.execute(
            "SELECT status, result, completed_at FROM analyses WHERE id = %s",
            (analysis_id,),
        ).fetchone()
        if row is not None and row[0] == "published":
            published.append(tuple(row))
    return {
        "scenario": name,
        "exits": exits,
        "llm_calls": calls.read_text().splitlines(),
        "published": published,
        "writes": [json.loads(line) for line in writes.read_text().splitlines()],
    }


def spread(values: list[float]) -> dict[str, float]:
    p95 = statistics.quantiles(values, n=100, method="inclusive")[94]
    return {
        "p50": round(statistics.median(values), 1),
        "p95": round(p95, 1),
        "max": round(max(values), 1),
    }


def report(results: list[dict[str, Any]]) -> dict[str, Any]:
    baseline = results[0]
    # The brief, not the whole row: usage counts the calls a crash made re-run.
    brief = baseline["published"][0][1]["brief"]
    rows = []
    for r in results:
        planned = sum(c.startswith("You plan research") for c in r["llm_calls"])
        rows.append(
            {
                "scenario": r["scenario"],
                "deliveries": [list(d) for d in SCENARIOS[r["scenario"]]],
                # Same brief as the uncrashed run, and not restarted from scratch.
                "resumed": bool(r["published"])
                and r["published"][0][1]["brief"] == brief
                and planned == (2 if r["scenario"] == "planner:after" else 1),
                "llm_calls_rerun": len(r["llm_calls"]) - len(baseline["llm_calls"]),
                # Distinct versions of the published row: 1 means never rewritten.
                "brief_versions": len(
                    {json.dumps(p, default=str) for p in r["published"]}
                ),
            }
        )
    crashed = [row for row in rows if any(c for _, c in row["deliveries"])]
    writes = [w for r in results for w in r["writes"]]
    by_kind = {
        kind: {
            "count": len(sizes := [w["bytes"] for w in writes if w["kind"] == kind]),
            "bytes": spread(sizes),
            "ms": spread([w["ms"] for w in writes if w["kind"] == kind]),
        }
        for kind in ("checkpoint", "writes")
    }
    return {
        "built_at": datetime.now(UTC).strftime("%Y-%m-%dT%H%MZ"),
        "workload": "Ford 8-K fixture, 1 supplier, scripted fake LLM, Jev off,"
        " DynamoDB Local 3.3.2 (amazon/dynamodb-local), one process per delivery",
        "baseline_llm_calls": len(baseline["llm_calls"]),
        "crash_scenarios": len(crashed),
        "resume_rate": sum(row["resumed"] for row in crashed) / len(crashed),
        "llm_calls_rerun": sum(row["llm_calls_rerun"] for row in rows),
        "duplicate_briefs": sum(row["brief_versions"] - 1 for row in rows),
        "checkpoint_writes": by_kind,
        "scenarios": rows,
    }


def test_every_crash_resumes_to_the_same_single_brief(
    db: psycopg.Connection,
    sec_client: SecClient,
    fake_embedder: Embedder,
    checkpoint_table: str,
    tmp_path: Path,
) -> None:
    seeded_server(db, sec_client, fake_embedder)
    results = [run_scenario(db, checkpoint_table, tmp_path, n) for n in SCENARIOS]
    assert results[0]["published"][0][1]["brief"]["claims"]
    assert len(results[0]["llm_calls"]) == 6
    measured = report(results)
    if out := os.environ.get("DURABILITY_REPORT"):
        path = Path(out) / f"{measured['built_at']}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(measured, indent=2) + "\n")

    assert measured["resume_rate"] == 1.0, measured["scenarios"]
    assert measured["duplicate_briefs"] == 0
    for row in measured["scenarios"]:
        assert 0 <= row["llm_calls_rerun"] <= MAX_RERUN.get(row["scenario"], 0), row
        assert row["resumed"], row
    # DynamoDB rejects items over 400 KB; the saver offloads only with S3 set up.
    assert measured["checkpoint_writes"]["checkpoint"]["bytes"]["max"] < 350_000
