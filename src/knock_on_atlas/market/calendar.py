from bisect import bisect_left, bisect_right
from collections.abc import Sequence
from datetime import date, datetime, time
from zoneinfo import ZoneInfo

import psycopg

from knock_on_atlas.market.bars import closes

BENCHMARK = "SPY"
EASTERN = ZoneInfo("America/New_York")
MARKET_CLOSE = time(16, 0)


def sessions(conn: psycopg.Connection) -> list[date]:
    """Trading days, oldest first: the days the benchmark has a close."""
    return list(closes(conn, BENCHMARK))


def day_zero(accepted_at: datetime, sessions: Sequence[date]) -> date | None:
    """The first session that could react to a filing; None if it has no close yet.

    A filing accepted after 4:00 pm ET counts from the next session.
    """
    local = accepted_at.astimezone(EASTERN)
    after_close = local.time() > MARKET_CLOSE
    find = bisect_right if after_close else bisect_left
    i = find(sessions, local.date())
    return sessions[i] if i < len(sessions) else None
