from datetime import date

import psycopg

from knock_on_atlas.db.migrate import migrate
from knock_on_atlas.market.bars import closes, upsert_closes


def test_upsert_closes_is_idempotent_and_takes_the_latest_value(
    db: psycopg.Connection,
) -> None:
    migrate(db)
    first = {date(2026, 1, 5): 100.0, date(2026, 1, 2): 99.5}
    assert upsert_closes(db, "SPY", first) == 2
    assert upsert_closes(db, "SPY", first) == 2
    upsert_closes(db, "SPY", {date(2026, 1, 5): 101.25})
    assert closes(db, "SPY") == {date(2026, 1, 2): 99.5, date(2026, 1, 5): 101.25}
    assert list(closes(db, "SPY")) == [date(2026, 1, 2), date(2026, 1, 5)]
    assert closes(db, "WMT") == {}
