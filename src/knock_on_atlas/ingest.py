"""Scheduled jobs: new hub 8-Ks into the inbox, and the daily event-study refresh."""

import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

import psycopg

from knock_on_atlas.edgar.client import SecClient
from knock_on_atlas.edgar.submissions import material_8ks, recent_8ks
from knock_on_atlas.market.bars import upsert_closes
from knock_on_atlas.market.calendar import EASTERN, sessions
from knock_on_atlas.market.event_study import refresh_tickers, study_event
from knock_on_atlas.market.prices import TwelveDataClient, TwelveDataError

log = logging.getLogger(__name__)


def poll(conn: psycopg.Connection, sec: SecClient, since: date) -> int:
    """Add each hub's material 8-Ks filed since `since` to the inbox; returns new rows.

    Only the inbox: an analyst starts the run, so polling spends no model calls.
    """
    hubs = [row[0] for row in conn.execute("SELECT cik FROM companies WHERE is_hub")]
    added = 0
    for hub in hubs:
        for f in material_8ks(recent_8ks(sec, hub), since):
            added += conn.execute(
                "INSERT INTO events (accession, cik, accepted_at, items, url)"
                " VALUES (%s, %s, %s, %s, %s) ON CONFLICT (accession) DO NOTHING",
                (f.accession, hub, f.accepted_at, list(f.items), f.url),
            ).rowcount
    return added


@dataclass
class Refresh:
    credits: int = 0
    written: int = 0
    failed: dict[str, str] = field(default_factory=dict)


def refresh_studies(conn: psycopg.Connection, prices: TwelveDataClient) -> Refresh:
    """Refetch closes for SPY and every pending ticker, then restudy every inbox 8-K.

    Complete studies stay as they are.
    """
    done = Refresh()
    # Through yesterday in New York: a bar for today may still be moving.
    last_day = datetime.now(EASTERN).date() - timedelta(days=1)
    # The whole stored range, not just new days, for the same one credit: Twelve
    # Data re-adjusts past closes after a split, so appended bars could mix bases.
    start = sessions(conn)[0]
    for ticker in refresh_tickers(conn):
        done.credits += 1
        try:
            bars = prices.daily_closes(ticker, start, last_day)
        except TwelveDataError as error:
            log.warning("%s", error)
            done.failed[ticker] = str(error)
            continue
        upsert_closes(conn, ticker, bars)
    days = sessions(conn)
    events = conn.execute("SELECT accession, cik, accepted_at FROM events").fetchall()
    done.written = sum(study_event(conn, a, c, at, days) for a, c, at in events)
    return done
