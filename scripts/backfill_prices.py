"""Backfill daily closes for SPY and every graph company, from before the first event.

The start is the earliest event in evals/cases/events.csv minus LOOKBACK, enough for
the [-250, -30] estimation window. One Twelve Data call per ticker; tickers already
with stored bars are skipped, so a stopped run can be restarted. Writes
evals/prices/<UTC timestamp>.json.

Usage: uv run python scripts/backfill_prices.py
"""

import csv
import json
import logging
import math
from datetime import UTC, datetime, timedelta
from pathlib import Path

import psycopg

from knock_on_atlas.config import get_settings
from knock_on_atlas.market.bars import upsert_closes
from knock_on_atlas.market.calendar import BENCHMARK, EASTERN
from knock_on_atlas.market.prices import TwelveDataClient, TwelveDataError

EVENTS = Path("evals/cases/events.csv")
SUMMARY_DIR = Path("evals/prices")
# 250 sessions before day 0 is about 363 calendar days; the rest covers holidays.
LOOKBACK = timedelta(days=400)
# A daily move this large in split-adjusted closes points at a bad bar or a split.
JUMP = 0.5

logging.basicConfig(level=logging.WARNING)
logger = logging.getLogger("backfill_prices")
settings = get_settings()
if settings.twelvedata_api_key is None:
    raise SystemExit("TWELVEDATA_API_KEY is not set")
client = TwelveDataClient(settings.twelvedata_api_key.get_secret_value())

with EVENTS.open(newline="") as f:
    first_event = min(
        datetime.fromisoformat(row["accepted_at"]).date() for row in csv.DictReader(f)
    )
start = first_event - LOOKBACK
# Through yesterday in New York: a bar for today may still be moving.
end = datetime.now(EASTERN).date() - timedelta(days=1)
fetched: dict[str, int] = {}
skipped: list[str] = []
failed: dict[str, str] = {}
jumps: dict[str, list[str]] = {}

with psycopg.connect(settings.database_url.get_secret_value(), autocommit=True) as conn:
    # The first listed ticker is the common share (F, not F-PB).
    tickers = [BENCHMARK] + [
        row[0]
        for row in conn.execute(
            "SELECT DISTINCT tickers[1] FROM companies WHERE cardinality(tickers) > 0"
            " AND (is_hub OR cik IN (SELECT src_cik FROM entity_edges"
            " WHERE status <> 'rejected' UNION SELECT dst_cik FROM entity_edges"
            " WHERE status <> 'rejected')) ORDER BY 1"
        )
    ]
    # One call stores a ticker's whole range, so any stored bar means it is done
    # (a stock listed after the start has fewer bars, not a gap to refill).
    stored = {row[0] for row in conn.execute("SELECT DISTINCT ticker FROM price_bars")}
    for ticker in tickers:
        if ticker in stored:
            skipped.append(ticker)
            continue
        try:
            bars = client.daily_closes(ticker, start, end)
        except TwelveDataError as error:
            logger.warning("%s", error)
            failed[ticker] = str(error)
            continue
        fetched[ticker] = upsert_closes(conn, ticker, bars)
        prices = list(bars.items())
        jumps[ticker] = [
            d.isoformat()
            for (_, before), (d, after) in zip(prices, prices[1:], strict=False)
            if abs(math.log(after / before)) > JUMP
        ]

built_at = datetime.now(UTC).strftime("%Y-%m-%dT%H%MZ")
summary = {
    "built_at": built_at,
    "start": start.isoformat(),
    "end": end.isoformat(),
    "tickers": len(tickers),
    "fetched": len(fetched),
    "skipped_already_covered": len(skipped),
    "failed": failed,
    "bars_stored": sum(fetched.values()),
    "credits_used": len(fetched) + len(failed),
    "large_daily_moves": {t: days for t, days in jumps.items() if days},
}
SUMMARY_DIR.mkdir(parents=True, exist_ok=True)
out = SUMMARY_DIR / f"{built_at}.json"
out.write_text(json.dumps(summary, indent=2) + "\n")
print(json.dumps(summary, indent=2))
print(out)
