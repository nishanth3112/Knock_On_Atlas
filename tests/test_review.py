import csv
from datetime import date
from pathlib import Path

import psycopg
import pytest

from knock_on_atlas.db.migrate import migrate
from knock_on_atlas.edgar.submissions import CompanyProfile
from knock_on_atlas.graph_store.edges import Edge, replace_edges, upsert_companies
from knock_on_atlas.graph_store.resolve import Hub
from knock_on_atlas.graph_store.review import (
    AUDIT_LABELS,
    EdgeView,
    audit_sample,
    list_edges,
    set_status,
    stratum,
    write_audit_csv,
)

HUB = "0000104169"


def edge(src: str, status: str, method: str = "alias_exact", conf: float = 1.0) -> Edge:
    return Edge(
        src_cik=src,
        dst_cik=HUB,
        pct=20.0,
        period_end=date(2025, 12, 31),
        method="xbrl",
        extraction_method=method,
        member_label="Walmart",
        total_share=True,
        relationship_flag=None,
        source_accession="0001278027-26-000010",
        confidence=conf,
        status=status,
        valid_from=date(2026, 3, 3),
        valid_to=None,
    )


@pytest.fixture
def graph(db: psycopg.Connection) -> psycopg.Connection:
    migrate(db)
    profiles = [
        CompanyProfile(cik, name, None, None, (), ())
        for cik, name in [
            (HUB, "Walmart Inc."),
            ("0001278027", "B&G Foods, Inc."),
            ("0000000002", "Supplier Two"),
        ]
    ]
    upsert_companies(db, profiles, [Hub(HUB, "Walmart Inc.", ())])
    replace_edges(db, [edge("0001278027", "approved"), edge("0000000002", "pending")])
    return db


def test_list_edges_filters_by_status_and_links_the_filing(
    graph: psycopg.Connection,
) -> None:
    assert len(list_edges(graph)) == 2
    (pending,) = list_edges(graph, "pending")
    assert (pending.supplier, pending.hub) == ("Supplier Two", "Walmart Inc.")
    (approved,) = list_edges(graph, "approved")
    assert approved.filing_url == (
        "https://www.sec.gov/Archives/edgar/data/1278027/000127802726000010/"
    )


def test_set_status_records_the_reviewer(graph: psycopg.Connection) -> None:
    (pending,) = list_edges(graph, "pending")
    assert set_status(graph, [pending.id, 999], "approved", "nishanth") == [pending.id]
    row = graph.execute(
        "SELECT status, reviewed_by, reviewed_at IS NOT NULL FROM entity_edges"
        " WHERE id = %s",
        (pending.id,),
    ).fetchone()
    assert row == ("approved", "nishanth", True)


def view(i: int, method: str, conf: float) -> EdgeView:
    return EdgeView(
        i,
        "approved",
        "S",
        "H",
        "L",
        10.0,
        True,
        None,
        method,
        conf,
        date(2025, 12, 31),
        "1",
        "0000000001-26-000001",
    )


EDGES = [view(i, "alias_exact", 1.0) for i in range(150)] + [
    view(200, "jev_choice", 0.9),
    view(201, "jev_choice", 0.97),
]


def test_audit_sample_covers_every_stratum_without_repeats() -> None:
    sample = audit_sample(EDGES, 100, seed=0)
    assert len(sample) == len({e.id for e in sample}) == 100
    assert {stratum(e) for e in sample} == {
        "alias_exact/1.00",
        "jev_choice/0.85-0.94",
        "jev_choice/0.95-0.99",
    }
    assert audit_sample(EDGES, 100, seed=0) == sample
    assert audit_sample(EDGES, 100, seed=1) != sample


def test_audit_sample_takes_everything_when_small() -> None:
    assert len(audit_sample(EDGES[:40], 100, seed=0)) == 40


def test_audit_csv_has_an_empty_label_column(tmp_path: Path) -> None:
    out = tmp_path / "audit.csv"
    write_audit_csv(out, EDGES[:2])
    rows = list(csv.DictReader(out.open()))
    assert [r["edge_id"] for r in rows] == ["0", "1"]
    assert rows[0]["label"] == "" and rows[0]["stratum"] == "alias_exact/1.00"
    assert "correct" in AUDIT_LABELS
