from datetime import UTC, date, datetime

import psycopg

from knock_on_atlas.db.migrate import migrate
from knock_on_atlas.market.bars import upsert_closes
from knock_on_atlas.market.calendar import day_zero, sessions

# Thu 2026-01-15, Fri 01-16, (Mon 01-19 is MLK Day), Tue 01-20.
JAN = [date(2026, 1, 15), date(2026, 1, 16), date(2026, 1, 20)]
# Fri 2026-07-10, Mon 07-13 (EDT, UTC-4).
JUL = [date(2026, 7, 10), date(2026, 7, 13)]


def utc(*args: int) -> datetime:
    return datetime(*args, tzinfo=UTC)


def test_before_or_at_the_close_is_that_day() -> None:
    # 15:59 and 16:00 EST are 20:59 and 21:00 UTC.
    assert day_zero(utc(2026, 1, 15, 20, 59), JAN) == date(2026, 1, 15)
    assert day_zero(utc(2026, 1, 15, 21, 0), JAN) == date(2026, 1, 15)
    assert day_zero(utc(2026, 1, 15, 12, 0), JAN) == date(2026, 1, 15)


def test_after_the_close_rolls_to_the_next_session() -> None:
    assert day_zero(utc(2026, 1, 15, 21, 1), JAN) == date(2026, 1, 16)
    # Friday after the close skips the weekend and the holiday.
    assert day_zero(utc(2026, 1, 16, 21, 30), JAN) == date(2026, 1, 20)
    # 01:30 UTC Saturday is still Friday evening in New York.
    assert day_zero(utc(2026, 1, 17, 1, 30), JAN) == date(2026, 1, 20)


def test_weekend_and_holiday_filings_take_the_next_session() -> None:
    assert day_zero(utc(2026, 1, 17, 15, 0), JAN) == date(2026, 1, 20)
    assert day_zero(utc(2026, 1, 19, 14, 0), JAN) == date(2026, 1, 20)


def test_summer_close_is_20_utc() -> None:
    assert day_zero(utc(2026, 7, 10, 19, 59), JUL) == date(2026, 7, 10)
    assert day_zero(utc(2026, 7, 10, 20, 1), JUL) == date(2026, 7, 13)


def test_no_session_yet_is_unknown() -> None:
    assert day_zero(utc(2026, 1, 20, 21, 5), JAN) is None
    assert day_zero(utc(2026, 1, 21, 14, 0), JAN) is None


def test_sessions_are_the_benchmark_dates(db: psycopg.Connection) -> None:
    migrate(db)
    upsert_closes(db, "SPY", {d: 100.0 for d in reversed(JAN)})
    upsert_closes(db, "WMT", {date(2026, 1, 19): 50.0})
    assert sessions(db) == JAN
