from datetime import date

import psycopg


def upsert_closes(
    conn: psycopg.Connection, ticker: str, bars: dict[date, float]
) -> int:
    """Store daily closes; a date already stored takes the newer value."""
    with conn.transaction(), conn.cursor() as cur:
        cur.executemany(
            "INSERT INTO price_bars (ticker, d, close) VALUES (%s, %s, %s)"
            " ON CONFLICT (ticker, d) DO UPDATE SET close = excluded.close",
            [(ticker, d, close) for d, close in bars.items()],
        )
    return len(bars)


def closes(conn: psycopg.Connection, ticker: str) -> dict[date, float]:
    """A ticker's stored closes, oldest first."""
    rows = conn.execute(
        "SELECT d, close FROM price_bars WHERE ticker = %s ORDER BY d", (ticker,)
    ).fetchall()
    return {d: float(close) for d, close in rows}
