from dataclasses import astuple, dataclass, fields
from datetime import date

import polars as pl
import psycopg

from knock_on_atlas.edgar.fsn import PLACEHOLDER_MEMBER
from knock_on_atlas.edgar.submissions import CompanyProfile
from knock_on_atlas.graph_store.resolve import Hub
from knock_on_atlas.jev.questions import J3_APPROVE_MIN

LAB_SIC = "8071"
REIT_SIC = "6798"
# Insurers: a lab's revenue from them is reimbursement for their members' tests.
INSURER_HUBS = {
    "0000064803",  # CVS Health (Aetna)
    "0001071739",  # Centene
    "0000049071",  # Humana
}

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
            pl.col("pct") > 0,  # drops nil, 0% and mis-signed negative facts
            pl.col("form") == "10-K",
            pl.col("qtrs") == 4,
            pl.col("period_end") == pl.col("fiscal_period_end"),
        )
        .sort(["total_share", "pct"], descending=True)
        .unique(["accession", "member"], keep="first")
        .sort(["cik", "accession", "member"])
    )


def sic_flag(supplier_sic: str | None, hub_cik: str) -> str | None:
    """Relationships the supplier's industry code alone shows are not purchases."""
    if supplier_sic == REIT_SIC:
        return "landlord"
    if supplier_sic == LAB_SIC and hub_cik in INSURER_HUBS:
        return "reimbursement"
    return None


def next_annual_filing(filed: date, ten_k_filed: tuple[date, ...]) -> date | None:
    """The supplier's next 10-K ends the edge, whether or not it names the customer."""
    return next((d for d in ten_k_filed if d > filed), None)


def edge_status(confidence: float, total_share: bool, flag: str | None) -> str:
    if confidence >= J3_APPROVE_MIN and total_share and flag is None:
        return "approved"
    return "pending"


@dataclass(frozen=True)
class Edge:
    src_cik: str
    dst_cik: str
    pct: float
    period_end: date
    method: str
    extraction_method: str
    member_label: str
    total_share: bool
    relationship_flag: str | None
    source_accession: str
    confidence: float
    status: str
    valid_from: date
    valid_to: date | None


EDGE_COLUMNS = [f.name for f in fields(Edge)]


def one_edge_per_hub(edges: list[Edge]) -> tuple[list[Edge], int]:
    """Labels from one 10-K that resolve to the same hub keep the surest one.

    Returns the kept edges and how many were dropped.
    """
    kept: dict[tuple[str, str, date], Edge] = {}
    for edge in sorted(edges, key=lambda e: (-e.confidence, -e.pct)):
        kept.setdefault((edge.src_cik, edge.dst_cik, edge.period_end), edge)
    return list(kept.values()), len(edges) - len(kept)


def replace_edges(conn: psycopg.Connection, edges: list[Edge]) -> int:
    """Swap in a fresh build; a reviewed edge keeps its decision, but its window
    still ends when the supplier files its next 10-K.

    Returns rows written: new edges plus reviewed edges whose window was refreshed.
    """
    with conn.transaction(), conn.cursor() as cur:
        cur.execute("DELETE FROM entity_edges WHERE reviewed_by IS NULL")
        cur.executemany(
            f"INSERT INTO entity_edges ({', '.join(EDGE_COLUMNS)})"
            f" VALUES ({', '.join(['%s'] * len(EDGE_COLUMNS))})"
            " ON CONFLICT (src_cik, dst_cik, type, period_end, method)"
            " DO UPDATE SET valid_to = excluded.valid_to",
            [astuple(e) for e in edges],
        )
        return cur.rowcount


def upsert_companies(
    conn: psycopg.Connection, profiles: list[CompanyProfile], hubs: list[Hub]
) -> None:
    aliases = {hub.cik: list(hub.aliases) for hub in hubs}
    with conn.transaction(), conn.cursor() as cur:
        # A company dropped from hubs.csv stops being a hub.
        cur.execute(
            "UPDATE companies SET is_hub = false WHERE NOT cik = ANY(%s)",
            (list(aliases),),
        )
        cur.executemany(
            "INSERT INTO companies (cik, name, sic, tickers, aliases, is_hub)"
            " VALUES (%s, %s, %s, %s, %s, %s)"
            " ON CONFLICT (cik) DO UPDATE SET name = excluded.name,"
            " sic = excluded.sic, tickers = excluded.tickers,"
            " aliases = excluded.aliases, is_hub = excluded.is_hub",
            [
                (
                    p.cik,
                    p.name,
                    p.sic,
                    list(p.tickers),
                    aliases.get(p.cik, []),
                    p.cik in aliases,
                )
                for p in profiles
            ],
        )


# The hub rule counts suppliers that pass both checks, or that a reviewer approved.
REAL_SUPPLIERS_SQL = """
SELECT h.cik, h.name, count(DISTINCT e.src_cik) FILTER (
  WHERE e.status <> 'rejected' AND (
    (e.status = 'approved' AND e.reviewed_by IS NOT NULL)
    OR (e.relationship_flag IS NULL AND e.confidence >= %(min_confidence)s)))
FROM companies h LEFT JOIN entity_edges e ON e.dst_cik = h.cik
WHERE h.is_hub
GROUP BY h.cik, h.name
ORDER BY 3, h.name
"""


def real_suppliers_by_hub(conn: psycopg.Connection) -> dict[str, int]:
    """Hub CIK -> suppliers with a sure identity and a checked relationship."""
    rows = conn.execute(
        REAL_SUPPLIERS_SQL, {"min_confidence": J3_APPROVE_MIN}
    ).fetchall()
    return {cik: count for cik, _, count in rows}
