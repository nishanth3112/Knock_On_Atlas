"""Load the eval 8-K filings into the events table, so the inbox lists them.

Safe to rerun: existing rows are updated.

Usage: uv run python scripts/load_events.py
"""

import csv
from pathlib import Path

import psycopg

from knock_on_atlas.config import get_settings

EVENTS = Path("evals/cases/events.csv")

with EVENTS.open(newline="") as f:
    events = list(csv.DictReader(f))
rows = [
    (e["accession"], e["hub_cik"], e["accepted_at"], e["items"].split("|"), e["url"])
    for e in events
]
database = get_settings().database_url.get_secret_value()
with (
    psycopg.connect(database, autocommit=True) as conn,
    conn.transaction(),
    conn.cursor() as cur,
):
    cur.executemany(
        "INSERT INTO events (accession, cik, accepted_at, items, url)"
        " VALUES (%s, %s, %s, %s, %s) ON CONFLICT (accession) DO UPDATE"
        " SET cik = EXCLUDED.cik, accepted_at = EXCLUDED.accepted_at,"
        " items = EXCLUDED.items, url = EXCLUDED.url",
        rows,
    )
print(f"loaded {len(rows)} events")
