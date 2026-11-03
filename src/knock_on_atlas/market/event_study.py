from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import date, datetime

import numpy as np
import psycopg

from knock_on_atlas.graph_store.traversal import exposure
from knock_on_atlas.market.bars import closes
from knock_on_atlas.market.calendar import BENCHMARK, EASTERN, day_zero

# Closes needed around day 0: 251 before it give the 250 returns of [-250, -1];
# 5 after it close the [-1, +5] window.
BEFORE = 251
AFTER = 5


@dataclass(frozen=True)
class Study:
    """Descriptive statistics of one stock's move around one filing; not a signal."""

    status: str  # pending | complete | insufficient_history | no_ticker
    day0: date | None = None
    car_0_1: float | None = None
    car_m1_5: float | None = None
    t_0_1: float | None = None
    t_m1_5: float | None = None
    alpha: float | None = None
    beta: float | None = None


def car(stock: np.ndarray, market: np.ndarray, t0: int) -> Study:
    """Market model on daily log returns; estimation window [-250, -30] (spec §13)."""
    est = slice(t0 - 250, t0 - 30)
    beta, alpha = np.polyfit(market[est], stock[est], 1)
    resid_sd = np.std(stock[est] - (alpha + beta * market[est]), ddof=2)
    ar = stock - (alpha + beta * market)
    car01, car15 = ar[t0 : t0 + 2].sum(), ar[t0 - 1 : t0 + 6].sum()
    return Study(
        status="complete",
        car_0_1=float(car01),
        car_m1_5=float(car15),
        t_0_1=float(car01 / (resid_sd * np.sqrt(2))),
        t_m1_5=float(car15 / (resid_sd * np.sqrt(7))),
        alpha=float(alpha),
        beta=float(beta),
    )


def study(
    stock: dict[date, float],
    market: dict[date, float],
    day0: date | None,
    sessions: Sequence[date],
) -> Study:
    """The event study for one stock, or why it can't be computed yet (or at all)."""
    if day0 is None:
        return Study("pending")
    t = sessions.index(day0)
    if t < BEFORE:
        return Study("insufficient_history", day0)
    days = sessions[t - BEFORE : t + AFTER + 1]
    # Closes up to day -3 are history; from day -2 on (the [-1, +5] returns) they
    # may simply not exist yet. History is checked first, so pending means the
    # estimation window is already complete.
    if any(d not in stock for d in days[: BEFORE - 2]):
        return Study("insufficient_history", day0)
    if len(days) < BEFORE + AFTER + 1 or any(
        d not in stock for d in days[BEFORE - 2 :]
    ):
        return Study("pending", day0)
    returns = [np.diff(np.log([prices[d] for d in days])) for prices in (stock, market)]
    return replace(car(returns[0], returns[1], t0=BEFORE - 1), day0=day0)


def study_event(
    conn: psycopg.Connection,
    accession: str,
    hub_cik: str,
    accepted_at: datetime,
    sessions: Sequence[date],
) -> int:
    """Study the filer and its suppliers (as of the filing) for one 8-K.

    Complete studies are kept as they are; the rest are recomputed and stored.
    Returns the number of studies written.
    """
    as_of = accepted_at.astimezone(EASTERN).date()
    depths = {hub_cik: 0}
    for row in exposure(conn, hub_cik, as_of):
        depths[row.supplier_cik] = min(
            row.depth, depths.get(row.supplier_cik, row.depth)
        )
    done = {
        row[0]
        for row in conn.execute(
            "SELECT cik FROM event_studies"
            " WHERE accession = %s AND status = 'complete'",
            (accession,),
        )
    }
    tickers: dict[str, str | None] = dict(
        conn.execute(
            "SELECT cik, tickers[1] FROM companies WHERE cik = ANY(%s)", (list(depths),)
        ).fetchall()
    )
    market = closes(conn, BENCHMARK)
    day0 = day_zero(accepted_at, sessions)
    written = 0
    for cik, depth in depths.items():
        if cik in done:
            continue
        ticker = tickers.get(cik)
        result = (
            study(closes(conn, ticker), market, day0, sessions)
            if ticker
            else Study("no_ticker", day0)
        )
        conn.execute(
            "INSERT INTO event_studies (accession, cik, ticker, depth, day0, status,"
            " car_0_1, car_m1_5, t_0_1, t_m1_5, alpha, beta)"
            " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"
            " ON CONFLICT (accession, cik) DO UPDATE SET ticker = excluded.ticker,"
            " depth = excluded.depth, day0 = excluded.day0, status = excluded.status,"
            " car_0_1 = excluded.car_0_1, car_m1_5 = excluded.car_m1_5,"
            " t_0_1 = excluded.t_0_1, t_m1_5 = excluded.t_m1_5,"
            " alpha = excluded.alpha, beta = excluded.beta, computed_at = now()",
            (
                accession,
                cik,
                ticker,
                depth,
                result.day0,
                result.status,
                result.car_0_1,
                result.car_m1_5,
                result.t_0_1,
                result.t_m1_5,
                result.alpha,
                result.beta,
            ),
        )
        written += 1
    return written
