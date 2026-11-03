"""Fetch new closes and recompute every event study that is not complete yet.

Runs daily, after scripts/backfill_prices.py. Closes are refetched for SPY and each
ticker with a pending study (one Twelve Data call each), then every event in
evals/cases/events.csv is studied for its filer and suppliers; complete studies stay
as they are. Writes evals/event_study/<UTC timestamp>.json.

Usage: uv run python scripts/refresh_event_studies.py
"""

import csv
import json
import logging
from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path

import psycopg

from knock_on_atlas.config import get_settings
from knock_on_atlas.market.bars import upsert_closes
from knock_on_atlas.market.calendar import BENCHMARK, EASTERN, sessions
from knock_on_atlas.market.event_study import study_event
from knock_on_atlas.market.prices import TwelveDataClient, TwelveDataError

EVENTS = Path("evals/cases/events.csv")
SUMMARY_DIR = Path("evals/event_study")

logging.basicConfig(level=logging.WARNING)
logger = logging.getLogger("refresh_event_studies")
settings = get_settings()
if settings.twelvedata_api_key is None:
    raise SystemExit("TWELVEDATA_API_KEY is not set")
client = TwelveDataClient(settings.twelvedata_api_key.get_secret_value())
with EVENTS.open(newline="") as f:
    events = list(csv.DictReader(f))
# Through yesterday in New York: a bar for today may still be moving.
last_day = datetime.now(EASTERN).date() - timedelta(days=1)
credits = 0
failed: dict[str, str] = {}

with psycopg.connect(settings.database_url.get_secret_value(), autocommit=True) as conn:
    # The whole stored range, not just new days, for the same one credit: Twelve
    # Data re-adjusts past closes after a split, so appended bars could mix bases.
    start = sessions(conn)[0]
    tickers = [BENCHMARK] + [
        row[0]
        for row in conn.execute(
            "SELECT DISTINCT ticker FROM event_studies"
            " WHERE status = 'pending' AND ticker <> %s ORDER BY 1",
            (BENCHMARK,),
        )
    ]
    for ticker in tickers:
        credits += 1
        try:
            bars = client.daily_closes(ticker, start, last_day)
        except TwelveDataError as error:
            logger.warning("%s", error)
            failed[ticker] = str(error)
            continue
        upsert_closes(conn, ticker, bars)

    days = sessions(conn)
    written = sum(
        study_event(
            conn,
            event["accession"],
            event["hub_cik"],
            datetime.fromisoformat(event["accepted_at"]),
            days,
        )
        for event in events
    )
    rows = conn.execute(
        "SELECT accession, cik, ticker, depth, day0, status FROM event_studies"
        " WHERE accession = ANY(%s)",
        ([event["accession"] for event in events],),
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
    "written_this_run": written,
    "credits_used": credits,
    "fetch_failures": failed,
}
SUMMARY_DIR.mkdir(parents=True, exist_ok=True)
out = SUMMARY_DIR / f"{built_at}.json"
out.write_text(json.dumps(summary, indent=2, default=str) + "\n")
print(json.dumps(summary, indent=2, default=str))
print(out)
