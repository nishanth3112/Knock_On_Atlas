import csv
import math
import statistics
from datetime import UTC, date, datetime
from itertools import pairwise
from pathlib import Path

import psycopg
import pytest

from knock_on_atlas.db.migrate import migrate
from knock_on_atlas.market.bars import upsert_closes
from knock_on_atlas.market.calendar import day_zero
from knock_on_atlas.market.event_study import study, study_event

# Synthetic closes (no vendor data): stock = 0.0003 + 0.9 * market + noise, with
# -6% planted on day 0 (Fri 2026-02-20) and -1.5% on day +1.
FIXTURE = Path(__file__).parent / "fixtures" / "event_study" / "known_case.csv"
with FIXTURE.open(newline="") as f:
    ROWS = list(csv.DictReader(f))
SESSIONS = [date.fromisoformat(r["session"]) for r in ROWS]
MARKET = {d: float(r["market_close"]) for d, r in zip(SESSIONS, ROWS, strict=True)}
STOCK = {d: float(r["stock_close"]) for d, r in zip(SESSIONS, ROWS, strict=True)}
T0 = SESSIONS.index(date(2026, 2, 20))
BEFORE_CLOSE = datetime(2026, 2, 20, 20, 30, tzinfo=UTC)  # 15:30 ET
AFTER_CLOSE = datetime(2026, 2, 20, 21, 30, tzinfo=UTC)  # 16:30 ET


def by_hand(t0: int) -> dict[str, float]:
    """The market model written out with the standard library, not numpy."""
    rm = [math.log(b / a) for a, b in pairwise(MARKET.values())]
    rs = [math.log(b / a) for a, b in pairwise(STOCK.values())]
    # Return j moves the close from session j to j + 1, so day k is j = t0 - 1 + k.
    est = range(t0 - 251, t0 - 31)  # days -250 .. -31
    beta, alpha = statistics.linear_regression(
        [rm[j] for j in est], [rs[j] for j in est]
    )
    resid = [rs[j] - alpha - beta * rm[j] for j in est]
    mean = statistics.fmean(resid)
    sd = math.sqrt(sum((e - mean) ** 2 for e in resid) / (len(resid) - 2))

    def ar(j: int) -> float:
        return rs[j] - alpha - beta * rm[j]

    car01 = ar(t0 - 1) + ar(t0)
    car15 = sum(ar(j) for j in range(t0 - 2, t0 + 5))  # days -1 .. +5
    return {
        "alpha": alpha,
        "beta": beta,
        "car_0_1": car01,
        "car_m1_5": car15,
        "t_0_1": car01 / (sd * math.sqrt(2)),
        "t_m1_5": car15 / (sd * math.sqrt(7)),
    }


def test_known_case_matches_the_hand_computation() -> None:
    result = study(STOCK, MARKET, day_zero(BEFORE_CLOSE, SESSIONS), SESSIONS)
    assert result.status == "complete" and result.day0 == date(2026, 2, 20)
    expected = by_hand(T0)
    for name, value in expected.items():
        assert getattr(result, name) == pytest.approx(value, rel=1e-9), name
    # The planted -7.5% shows through the noise.
    assert expected["car_0_1"] == pytest.approx(-0.075, abs=0.02)
    assert expected["t_0_1"] < -3


def test_after_close_filing_starts_the_next_monday() -> None:
    result = study(STOCK, MARKET, day_zero(AFTER_CLOSE, SESSIONS), SESSIONS)
    assert result.day0 == date(2026, 2, 23)
    assert result.car_0_1 == pytest.approx(by_hand(T0 + 1)["car_0_1"], rel=1e-9)


def test_fresh_event_is_pending_until_its_closes_exist(db: psycopg.Connection) -> None:
    migrate(db)
    db.execute(
        "INSERT INTO companies (cik, name, tickers) VALUES ('H', 'Hub', '{HUB}')"
    )
    # Bars only through day +2.
    seen = SESSIONS[: T0 + 3]
    upsert_closes(db, "SPY", {d: MARKET[d] for d in seen})
    upsert_closes(db, "HUB", {d: STOCK[d] for d in seen})
    study_event(db, "8k-1", "H", BEFORE_CLOSE, seen)
    row = db.execute("SELECT day0, status, car_0_1 FROM event_studies").fetchone()
    assert row == (date(2026, 2, 20), "pending", None)

    upsert_closes(db, "SPY", MARKET)
    upsert_closes(db, "HUB", STOCK)
    study_event(db, "8k-1", "H", BEFORE_CLOSE, SESSIONS)
    status, car01 = db.execute("SELECT status, car_0_1 FROM event_studies").fetchone()
    assert status == "complete"
    assert car01 == pytest.approx(by_hand(T0)["car_0_1"], rel=1e-9)
