import math
import random
from datetime import UTC, date, datetime, timedelta

import psycopg
import pytest

from knock_on_atlas.db.migrate import migrate
from knock_on_atlas.graph_store.edges import Edge, replace_edges
from knock_on_atlas.market.bars import upsert_closes
from knock_on_atlas.market.event_study import BEFORE, Study, study, study_event

ALPHA, BETA = 0.0002, 1.3
JUMP_0, JUMP_1 = 0.05, 0.02
SESSIONS = [
    d
    for d in (date(2025, 1, 1) + timedelta(days=i) for i in range(420))
    if d.weekday() < 5
]
T0 = 260  # index of day 0 in SESSIONS


def closes_from(returns: list[float]) -> dict[date, float]:
    prices = [100.0]
    for r in returns:
        prices.append(prices[-1] * math.exp(r))
    return dict(zip(SESSIONS, prices, strict=False))


def market_and_stock() -> tuple[dict[date, float], dict[date, float]]:
    rng = random.Random(7)
    market = [rng.gauss(0, 0.01) for _ in SESSIONS[1:]]
    stock = [ALPHA + BETA * m + rng.gauss(0, 0.0005) for m in market]
    # Return i moves the close from session i to session i + 1.
    stock[T0 - 1] += JUMP_0
    stock[T0] += JUMP_1
    return closes_from(market), closes_from(stock)


def test_car_recovers_a_planted_jump() -> None:
    market, stock = market_and_stock()
    result = study(stock, market, SESSIONS[T0], SESSIONS)
    assert result.status == "complete" and result.day0 == SESSIONS[T0]
    assert result.beta == pytest.approx(BETA, abs=0.01)
    assert result.car_0_1 == pytest.approx(JUMP_0 + JUMP_1, abs=0.002)
    assert result.car_m1_5 == pytest.approx(JUMP_0 + JUMP_1, abs=0.003)
    assert result.t_0_1 is not None and result.t_0_1 > 10


def test_statuses_when_closes_are_missing() -> None:
    market, stock = market_and_stock()
    assert study(stock, market, None, SESSIONS) == Study("pending")
    early = SESSIONS[BEFORE - 1]
    assert study(stock, market, early, SESSIONS).status == "insufficient_history"
    # Day +5 has no close yet.
    assert study(stock, market, SESSIONS[T0], SESSIONS[: T0 + 5]).status == "pending"
    gap = {d: p for d, p in stock.items() if d != SESSIONS[T0 - 100]}
    assert study(gap, market, SESSIONS[T0], SESSIONS).status == "insufficient_history"
    # A fresh event is pending only if its history is complete.
    fresh = SESSIONS[: T0 + 2]
    assert study(gap, market, SESSIONS[T0], fresh).status == "insufficient_history"
    late = {d: p for d, p in stock.items() if d != SESSIONS[T0 + 3]}
    assert study(late, market, SESSIONS[T0], SESSIONS).status == "pending"


def edge(src: str, dst: str) -> Edge:
    return Edge(
        src_cik=src,
        dst_cik=dst,
        pct=20,
        period_end=date(2024, 12, 31),
        method="xbrl",
        extraction_method="alias_exact",
        member_label=dst,
        total_share=True,
        relationship_flag=None,
        source_accession=f"{src}-{dst}",
        confidence=1.0,
        status="approved",
        valid_from=date(2025, 1, 1),
        valid_to=None,
    )


def test_study_event_covers_the_hub_and_its_suppliers(db: psycopg.Connection) -> None:
    migrate(db)
    db.execute(
        "INSERT INTO companies (cik, name, tickers, is_hub) VALUES"
        " ('H', 'Hub', '{HUB}', true), ('S', 'Supplier', '{SUP,SUP-P}', false),"
        " ('N', 'No ticker', '{}', false), ('T', 'Tier two', '{TWO}', false)"
    )
    replace_edges(db, [edge("S", "H"), edge("N", "H"), edge("T", "S")])
    market, stock = market_and_stock()
    upsert_closes(db, "SPY", market)
    upsert_closes(db, "HUB", stock)
    upsert_closes(db, "SUP", stock)
    upsert_closes(db, "TWO", {d: p for d, p in stock.items() if d >= SESSIONS[T0]})
    # 10:00 ET on day 0.
    accepted = datetime.combine(SESSIONS[T0], datetime.min.time(), UTC) + timedelta(
        hours=15
    )

    assert study_event(db, "8k-1", "H", accepted, SESSIONS) == 4
    rows = db.execute(
        "SELECT cik, ticker, depth, status FROM event_studies ORDER BY cik"
    ).fetchall()
    assert rows == [
        ("H", "HUB", 0, "complete"),
        ("N", None, 1, "no_ticker"),
        ("S", "SUP", 1, "complete"),
        ("T", "TWO", 2, "insufficient_history"),
    ]
    # Complete studies stay as they are; the others are recomputed.
    assert study_event(db, "8k-1", "H", accepted, SESSIONS) == 2
