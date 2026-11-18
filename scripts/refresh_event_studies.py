"""Refetch closes and recompute every event study that is not complete yet.

Runs daily on AWS (koa-poller's refresh-studies job); this runs the same job
locally. Closes are refetched for SPY and each ticker with a pending study (one
Twelve Data call each), then every 8-K in the events table is studied for its filer
and suppliers; complete studies stay as they are. Writes
evals/event_study/<UTC timestamp>.json.

Usage: uv run python scripts/refresh_event_studies.py
"""

import json
import logging
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

import psycopg

from knock_on_atlas.config import get_settings
from knock_on_atlas.ingest import refresh_studies
from knock_on_atlas.market.calendar import sessions
from knock_on_atlas.market.prices import TwelveDataClient

SUMMARY_DIR = Path("evals/event_study")

logging.basicConfig(level=logging.WARNING)
settings = get_settings()
if settings.twelvedata_api_key is None:
    raise SystemExit("TWELVEDATA_API_KEY is not set")
client = TwelveDataClient(settings.twelvedata_api_key.get_secret_value())

with psycopg.connect(settings.database_url.get_secret_value(), autocommit=True) as conn:
    done = refresh_studies(conn, client)
    days = sessions(conn)
    events = [row[0] for row in conn.execute("SELECT accession FROM events")]
    rows = conn.execute(
        "SELECT accession, cik, ticker, depth, day0, status FROM event_studies"
        " WHERE accession = ANY(%s)",
        (events,),
    ).fetchall()

hub_status = {
    acc: (status, day0) for acc, _, _, depth, day0, status in rows if depth == 0
}
# Only a complete study is known to have a full window; a pending one may still
# be waiting for its history to be fetched.
full = [acc for acc, (status, _) in hub_status.items() if status == "complete"]
built_at = datetime.now(UTC).strftime("%Y-%m-%dT%H%MZ")
summary = {
    "built_at": built_at,
    "last_session": days[-1].isoformat() if days else None,
    "events": len(events),
    "events_with_full_window": len(full),
    "events_without_full_window": sorted(set(hub_status) - set(full)),
    "studies": len(rows),
    "studies_by_status": dict(Counter(row[5] for row in rows)),
    "studies_by_depth": dict(Counter(row[3] for row in rows)),
    "pending_events": sorted(
        {(acc, str(day0)) for acc, _, _, _, day0, s in rows if s == "pending"}
    ),
    "insufficient_history_tickers": sorted(
        {ticker for _, _, ticker, _, _, s in rows if s == "insufficient_history"}
    ),
    "written_this_run": done.written,
    "credits_used": done.credits,
    "fetch_failures": done.failed,
}
SUMMARY_DIR.mkdir(parents=True, exist_ok=True)
out = SUMMARY_DIR / f"{built_at}.json"
out.write_text(json.dumps(summary, indent=2, default=str) + "\n")
print(json.dumps(summary, indent=2, default=str))
print(out)
