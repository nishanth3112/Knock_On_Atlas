from datetime import date

import polars as pl
import psycopg

from knock_on_atlas.db.migrate import migrate
from knock_on_atlas.graph_store.edges import candidates, fact_rows, insert_facts

TOTAL = "ConcentrationRiskByBenchmark=SalesRevenueNet;ConcentrationRiskByType=X;"


def fact(
    member: str,
    share: float | None,
    *,
    adsh: str = "0001-25-000001",
    form: str = "10-K",
    ddate: str = "20250630",
    qtrs: str = "4",
    segments: str = TOTAL,
) -> dict[str, object]:
    return {
        "cik": "0000000001",
        "adsh": adsh,
        "form": form,
        "filed": "20250815",
        "period": "20250630",
        "ddate": ddate,
        "qtrs": qtrs,
        "customer_member": member,
        "member_label": member,
        "revenue_share": share,
        "benchmark": "SalesRevenueNet",
        "segments": segments + f"MajorCustomers={member};",
        "total_share": segments == TOTAL,
    }


FACTS = pl.DataFrame(
    [
        fact("WalMart", 0.31),
        fact("WalMart", 0.28, ddate="20240630"),  # prior-year comparative
        fact("WalMart", 0.30, form="10-Q", qtrs="1", adsh="0001-25-000002"),
        fact("CustomerA", 0.12),  # placeholder
        fact("Kroger", None),  # nil fact: FS&N leaves the value blank
        # Target appears only per segment: the larger part-of-revenue share stands in.
        fact("Target", 0.40, segments="BusinessSegments=Retail;" + TOTAL),
        fact("Target", 0.15, segments="BusinessSegments=Food;" + TOTAL),
        # Costco's total share wins over its larger segment share.
        fact("Costco", 0.11),
        fact("Costco", 0.45, segments="BusinessSegments=Retail;" + TOTAL),
    ]
)


def test_fact_rows_convert_types_and_flag_placeholders() -> None:
    rows = fact_rows(FACTS)
    first = rows.row(0, named=True)
    assert first["pct"] == 31.0
    assert first["filed"] == date(2025, 8, 15)
    assert first["period_end"] == first["fiscal_period_end"] == date(2025, 6, 30)
    assert rows.filter(~pl.col("is_named"))["member"].to_list() == ["CustomerA"]


def test_candidates_keep_current_fiscal_year_10k_facts() -> None:
    picked = candidates(fact_rows(FACTS))
    assert picked.select("member", "pct", "total_share").rows() == [
        ("Costco", 11.0, True),
        ("Target", 40.0, False),
        ("WalMart", 31.0, True),
    ]


def test_insert_facts_is_idempotent(db: psycopg.Connection) -> None:
    migrate(db)
    rows = fact_rows(FACTS)
    assert insert_facts(db, rows) == len(FACTS)
    assert insert_facts(db, rows) == 0
