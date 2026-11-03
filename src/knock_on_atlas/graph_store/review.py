import csv
import random
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import psycopg

AUDIT_LABELS = (
    "correct",
    "wrong_customer",
    "wrong_pct",
    "stale",
    "not_a_customer_relationship",
)


@dataclass(frozen=True)
class EdgeView:
    id: int
    status: str
    supplier: str
    hub: str
    member_label: str
    pct: float
    total_share: bool
    relationship_flag: str | None
    extraction_method: str
    confidence: float
    period_end: date
    src_cik: str
    source_accession: str

    @property
    def filing_url(self) -> str:
        folder = self.source_accession.replace("-", "")
        return f"https://www.sec.gov/Archives/edgar/data/{int(self.src_cik)}/{folder}/"


def list_edges(conn: psycopg.Connection, status: str | None = None) -> list[EdgeView]:
    rows = conn.execute(
        "SELECT e.id, e.status, s.name, h.name, e.member_label, e.pct, e.total_share,"
        " e.relationship_flag, e.extraction_method, e.confidence, e.period_end,"
        " e.src_cik, e.source_accession"
        " FROM entity_edges e"
        " JOIN companies s ON s.cik = e.src_cik JOIN companies h ON h.cik = e.dst_cik"
        " WHERE %(status)s::text IS NULL OR e.status = %(status)s"
        " ORDER BY h.name, e.pct DESC, e.id",
        {"status": status},
    ).fetchall()
    return [
        EdgeView(
            id=r[0],
            status=r[1],
            supplier=r[2],
            hub=r[3],
            member_label=r[4],
            pct=float(r[5]),
            total_share=r[6],
            relationship_flag=r[7],
            extraction_method=r[8],
            confidence=float(r[9]),
            period_end=r[10],
            src_cik=r[11],
            source_accession=r[12],
        )
        for r in rows
    ]


def set_status(
    conn: psycopg.Connection, ids: list[int], status: str, reviewer: str
) -> list[int]:
    """Record a reviewer's decision; returns the ids that exist."""
    rows = conn.execute(
        "UPDATE entity_edges SET status = %s, reviewed_by = %s, reviewed_at = now()"
        " WHERE id = ANY(%s) RETURNING id",
        (status, reviewer, ids),
    ).fetchall()
    return sorted(r[0] for r in rows)


def stratum(edge: EdgeView) -> str:
    if edge.confidence >= 1.0:
        band = "1.00"
    elif edge.confidence >= 0.95:
        band = "0.95-0.99"
    else:
        band = "0.85-0.94"
    return f"{edge.extraction_method}/{band}"


def audit_sample(edges: list[EdgeView], n: int, seed: int) -> list[EdgeView]:
    """Up to n edges: one from every stratum, the remaining seats drawn at random."""
    if len(edges) <= n:
        return sorted(edges, key=lambda e: (stratum(e), e.id))
    rng = random.Random(seed)
    strata: dict[str, list[EdgeView]] = {}
    for edge in sorted(edges, key=lambda e: e.id):
        strata.setdefault(stratum(edge), []).append(edge)
    picked = [rng.choice(group) for _, group in sorted(strata.items())]
    rest = [e for e in sorted(edges, key=lambda e: e.id) if e not in picked]
    picked += rng.sample(rest, n - len(picked))
    return sorted(picked, key=lambda e: (stratum(e), e.id))


def write_audit_csv(path: Path, edges: list[EdgeView]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as f:
        out = csv.writer(f)
        out.writerow(
            [
                "edge_id",
                "stratum",
                "supplier",
                "hub",
                "member_label",
                "pct",
                "period_end",
                "filing_url",
                "label",
                "notes",
            ]
        )
        for e in edges:
            out.writerow(
                [
                    e.id,
                    stratum(e),
                    e.supplier,
                    e.hub,
                    e.member_label,
                    e.pct,
                    e.period_end,
                    e.filing_url,
                    "",
                    "",
                ]
            )
