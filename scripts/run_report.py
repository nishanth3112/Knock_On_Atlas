"""Per-run token and cost report: what each analysis spent.

Chat models cost $0 here (Ollama locally, Groq's free tier); Jev is the only paid
call, priced per input token. Writes evals/runs/cost_<UTC>.json (local only).

Usage: uv run python scripts/run_report.py [--since 2026-11-01]
"""

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path

import psycopg

from knock_on_atlas.config import get_settings
from knock_on_atlas.workflow.analyses import run_costs

OUT = Path("evals/runs")

parser = argparse.ArgumentParser()
parser.add_argument("--since", default="2000-01-01", help="UTC date, YYYY-MM-DD")
since = datetime.fromisoformat(parser.parse_args().since).replace(tzinfo=UTC)
database = get_settings().database_url.get_secret_value()
with psycopg.connect(database) as conn:
    runs = run_costs(conn, since)

for r in runs:
    print(
        f"{r['created_at'][:16]}  {r['status']:<9}  {r['llm_calls']:>2} LLM"
        f"  {r['tool_calls']:>2} tools  {r['chat_tokens']:>6} tokens"
        f"  {r['jev_input_tokens']:>7} Jev  ${r['jev_usd']:.4f}"
    )
total = {
    "runs": len(runs),
    "chat_tokens": sum(r["chat_tokens"] for r in runs),
    "jev_input_tokens": sum(r["jev_input_tokens"] for r in runs),
    "jev_usd": round(sum(r["jev_usd"] for r in runs), 6),
}
print(f"total: {total}")
built_at = datetime.now(UTC).strftime("%Y-%m-%dT%H%MZ")
OUT.mkdir(parents=True, exist_ok=True)
path = OUT / f"cost_{built_at}.json"
path.write_text(
    json.dumps({"since": since.isoformat(), "total": total, "runs": runs}, indent=2)
    + "\n"
)
print(f"wrote {path}")
