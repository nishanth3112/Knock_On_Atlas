import polars as pl
import psycopg

from knock_on_atlas.edgar.fsn import PLACEHOLDER_MEMBER

FACT_COLUMNS = [
    "cik",
    "accession",
    "form",
    "filed",
    "fiscal_period_end",
    "period_end",
    "qtrs",
    "member",
    "member_label",
    "pct",
    "benchmark",
    "segments",
    "total_share",
    "is_named",
]


def fact_rows(facts: pl.DataFrame) -> pl.DataFrame:
    """FS&N loader output as concentration_facts rows, pct in percent."""

    def day(col: str) -> pl.Expr:
        return pl.col(col).str.to_date("%Y%m%d")

    return facts.select(
        cik="cik",
        accession="adsh",
        form="form",
        filed=day("filed"),
        fiscal_period_end=day("period"),
        period_end=day("ddate"),
        qtrs=pl.col("qtrs").cast(pl.Int32),
        member="customer_member",
        member_label="member_label",
        pct=(pl.col("revenue_share") * 100).round(4),
        benchmark="benchmark",
        segments="segments",
        total_share="total_share",
        is_named=~pl.col("customer_member").str.contains(PLACEHOLDER_MEMBER),
    )


def insert_facts(conn: psycopg.Connection, rows: pl.DataFrame) -> int:
    with conn.cursor() as cur:
        cur.executemany(
            f"INSERT INTO concentration_facts ({', '.join(FACT_COLUMNS)})"
            f" VALUES ({', '.join(['%s'] * len(FACT_COLUMNS))})"
            # One 10-Q fact repeats with 25% and 0%; the first one read wins.
            " ON CONFLICT DO NOTHING",
            rows.select(FACT_COLUMNS).rows(),
        )
        return cur.rowcount


def candidates(rows: pl.DataFrame) -> pl.DataFrame:
    """One fact per (10-K, named customer): its current fiscal-year share.

    Prior-year comparatives never make edges, so a customer that drops out of the
    latest 10-K has no current fact. Without a total-revenue fact, the largest
    part-of-revenue share stands in, flagged by total_share = false.
    """
    return (
        rows.filter(
            pl.col("is_named"),
            pl.col("pct").is_not_null(),
            pl.col("form") == "10-K",
            pl.col("qtrs") == 4,
            pl.col("period_end") == pl.col("fiscal_period_end"),
        )
        .sort(["total_share", "pct"], descending=True)
        .unique(["accession", "member"], keep="first")
        .sort(["cik", "accession", "member"])
    )
