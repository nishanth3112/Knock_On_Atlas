from datetime import date

import polars as pl
import psycopg

from knock_on_atlas.db.migrate import migrate
from knock_on_atlas.edgar.submissions import CompanyProfile
from knock_on_atlas.graph_store.edges import (
    Edge,
    candidates,
    edge_status,
    fact_rows,
    insert_facts,
    next_annual_filing,
    one_edge_per_hub,
    replace_edges,
    sic_flag,
    upsert_companies,
)
from knock_on_atlas.graph_store.resolve import Hub

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
        fact("Aetna", 0.0),  # disclosed at 0% this year
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


HUMANA, TARGET = "0000049071", "0000027419"


def test_sic_flag_reits_and_lab_insurer_pairs() -> None:
    assert sic_flag("6798", TARGET) == "landlord"
    assert sic_flag("8071", HUMANA) == "reimbursement"
    # A lab selling to a retailer is an ordinary sale; only the insurer pair is not.
    assert sic_flag("8071", TARGET) is None
    assert sic_flag("2000", HUMANA) is None
    assert sic_flag(None, TARGET) is None


def test_edge_ends_at_the_next_10k_even_on_the_same_day_twice() -> None:
    filed = (date(2024, 2, 28), date(2025, 2, 25), date(2026, 3, 3))
    assert next_annual_filing(date(2025, 2, 25), filed) == date(2026, 3, 3)
    assert next_annual_filing(date(2024, 1, 1), filed) == date(2024, 2, 28)
    assert next_annual_filing(date(2026, 3, 3), filed) is None


def test_only_sure_total_unflagged_edges_are_approved() -> None:
    assert edge_status(1.0, True, None) == "approved"
    assert edge_status(0.85, True, None) == "approved"
    assert edge_status(0.84, True, None) == "pending"
    assert edge_status(1.0, False, None) == "pending"
    assert edge_status(1.0, True, "unclear") == "pending"


def edge(label: str, **changes: object) -> Edge:
    values: dict[str, object] = {
        "src_cik": "0000000001",
        "dst_cik": TARGET,
        "pct": 20.0,
        "period_end": date(2025, 6, 30),
        "method": "xbrl",
        "extraction_method": "alias_exact",
        "member_label": label,
        "total_share": True,
        "relationship_flag": None,
        "source_accession": "0001-25-000001",
        "confidence": 1.0,
        "status": "approved",
        "valid_from": date(2025, 8, 15),
        "valid_to": None,
    }
    return Edge(**(values | changes))


def test_one_edge_per_hub_keeps_the_surest_label() -> None:
    kept, dropped = one_edge_per_hub(
        [
            edge("Target Stores", extraction_method="jev_choice", confidence=0.9),
            edge("Target", pct=10.0),
            edge("Target", period_end=date(2024, 6, 30)),
        ]
    )
    assert dropped == 1
    assert sorted((e.member_label, e.period_end.year) for e in kept) == [
        ("Target", 2024),
        ("Target", 2025),
    ]


def test_replace_edges_keeps_reviewed_edges(db: psycopg.Connection) -> None:
    migrate(db)
    assert (
        replace_edges(db, [edge("Target"), edge("Target", src_cik="0000000002")]) == 2
    )
    db.execute(
        "UPDATE entity_edges SET status = 'rejected', reviewed_by = 'me'"
        " WHERE src_cik = '0000000002'"
    )
    # The rebuild drops the unreviewed edge it no longer produces and keeps the
    # rejected one, even though it produces it again.
    assert replace_edges(db, [edge("Target", src_cik="0000000002")]) == 0
    rows = db.execute("SELECT src_cik, status FROM entity_edges").fetchall()
    assert rows == [("0000000002", "rejected")]


def test_upsert_companies_marks_hubs(db: psycopg.Connection) -> None:
    migrate(db)
    hub = Hub(TARGET, "Target Corporation", ("Target",))

    def profile(cik: str, name: str) -> CompanyProfile:
        return CompanyProfile(cik, name, "5331", None, ("TGT",), ())

    upsert_companies(db, [profile(TARGET, "TARGET CORP")], [hub])
    upsert_companies(db, [profile(TARGET, "Target Corp"), profile("1", "Sup")], [hub])
    rows = db.execute(
        "SELECT cik, name, aliases, is_hub FROM companies ORDER BY cik"
    ).fetchall()
    assert rows == [(TARGET, "Target Corp", ["Target"], True), ("1", "Sup", [], False)]
