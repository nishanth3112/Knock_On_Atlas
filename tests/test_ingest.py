from datetime import date
from typing import Any

import httpx
import psycopg
import pytest
from test_lambdas import OFF

from knock_on_atlas.db.migrate import migrate
from knock_on_atlas.edgar.client import SecClient
from knock_on_atlas.ingest import poll, refresh_studies
from knock_on_atlas.lambdas import poller
from knock_on_atlas.market.bars import upsert_closes
from knock_on_atlas.market.prices import TwelveDataClient

FORD = "0000037996"
FORD_8K = "0000037996-25-000238"  # the one 8-K in the recorded submissions


def test_new_hub_8ks_reach_the_inbox_once(
    db: psycopg.Connection, sec_client: SecClient
) -> None:
    migrate(db)
    db.execute(
        "INSERT INTO companies (cik, name, is_hub) VALUES (%s, 'Ford Motor Co', true)",
        (FORD,),
    )
    assert poll(db, sec_client, date(2025, 12, 1)) == 1
    assert poll(db, sec_client, date(2025, 12, 1)) == 0  # every 5 minutes, no copies
    [(accession, items, url)] = db.execute(
        "SELECT accession, items, url FROM events"
    ).fetchall()
    assert accession == FORD_8K and items == ["2.06", "7.01", "9.01"]
    assert url.startswith("https://www.sec.gov/Archives/edgar/data/37996/")


def test_a_refresh_records_failed_tickers_and_still_studies_the_inbox(
    db: psycopg.Connection,
) -> None:
    migrate(db)
    db.execute(
        "INSERT INTO events (accession, cik, accepted_at, items, url) VALUES"
        " (%s, %s, '2025-12-15T21:07:14Z', '{2.06}', 'u')",
        (FORD_8K, FORD),
    )
    db.execute(
        "INSERT INTO companies (cik, name, tickers, is_hub)"
        " VALUES (%s, 'Ford', '{F}', true)",
        (FORD,),
    )
    upsert_closes(db, "SPY", {date(2025, 12, 12): 600.0, date(2025, 12, 15): 601.0})

    def respond(request: httpx.Request) -> httpx.Response:
        body = {"code": 404, "message": "symbol not found", "status": "error"}
        return httpx.Response(404, json=body)

    done = refresh_studies(
        db, TwelveDataClient("k", transport=httpx.MockTransport(respond))
    )
    assert done.credits == 1 and "SPY" in done.failed
    assert done.written >= 1  # the filer's study, with too little history for now


def test_with_the_demo_off_the_poller_never_opens_a_connection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def no_connection(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("connected to the database with the demo off")

    monkeypatch.setattr(poller, "runtime", lambda: (None, OFF))
    monkeypatch.setattr(poller.psycopg, "connect", no_connection)
    for job in ("edgar-poll", "refresh-studies"):
        assert poller.handler({"job": job}, None) == {"job": job, "skipped": "demo off"}
