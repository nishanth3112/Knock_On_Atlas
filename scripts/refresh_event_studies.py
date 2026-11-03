"""Fetch new closes and recompute every event study that is not complete yet.

Runs daily. New closes are fetched for SPY and each ticker with a pending study
(one Twelve Data call each), then every event in evals/cases/events.csv is studied
for its filer and suppliers; complete studies stay as they are. Writes
evals/event_study/<UTC timestamp>.json.

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
    stale = conn.execute(
        "SELECT p.ticker, max(p.d) FROM price_bars p WHERE p.ticker = %s"
        " OR p.ticker IN (SELECT ticker FROM event_studies WHERE status = 'pending')"
        " GROUP BY p.ticker HAVING max(p.d) < %s",
        (BENCHMARK, last_day),
    ).fetchall()
    for ticker, last in stale:
        credits += 1
        try:
            bars = client.daily_closes(ticker, last + timedelta(days=1), last_day)
        except TwelveDataError as error:  # also raised when no new bar exists yet
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
# A study is pending only once its history is complete (event_study.study), so
# a known day 0 and a pending or complete status mean the window is full.
full = [
    acc
    for acc, (status, day0) in hub_status.items()
    if day0 is not None and status in ("complete", "pending")
]
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
