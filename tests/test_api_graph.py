from datetime import date

import psycopg
import pytest
from test_api import HUB, OTHER, app_with
from test_auth import bearer

from knock_on_atlas.graph_store.edges import Edge, replace_edges

SECOND, NEW = "0000099999", "0000088888"


def edge(src: str, dst: str, pct: float, status: str = "approved") -> Edge:
    return Edge(
        src_cik=src,
        dst_cik=dst,
        pct=pct,
        period_end=date(2025, 12, 31),
        method="xbrl",
        extraction_method="alias_exact",
        member_label=f"{dst} Member",
        total_share=True,
        relationship_flag=None,
        source_accession=f"{src}-26-000001",
        confidence=0.97,
        status=status,
        valid_from=date(2026, 2, 1),
        valid_to=None,
    )


def graph(db: psycopg.Connection) -> None:
    """Ford <- Gentherm <- Sable approved, and Pinecrest -> Ford still pending."""
    db.execute(
        "INSERT INTO companies (cik, name, tickers) VALUES"
        " (%s, 'Sable Logistics', '{SBLG}'), (%s, 'Pinecrest Paper', '{}')",
        (SECOND, NEW),
    )
    replace_edges(
        db,
        [
            edge(OTHER, HUB, 22.0),
            edge(SECOND, OTHER, 10.0),
            edge(NEW, HUB, 13.2, status="pending"),
        ],
    )


def test_the_exposure_map_names_each_supplier_and_its_source(
    db: psycopg.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, _ = app_with(db, monkeypatch)
    graph(db)
    ana = bearer(client, "ana@example.com")
    found = client.get(f"/companies/{HUB}/exposure?as_of=2026-03-01", headers=ana)
    body = found.json()
    assert body["hub"]["name"] == "Ford Motor Co" and body["as_of"] == "2026-03-01"
    direct, second = body["suppliers"]
    assert (direct["name"], direct["depth"], direct["weight"]) == (
        "Gentherm Inc",
        1,
        22.0,
    )
    assert direct["edge"]["pct"] == 22.0  # a number: numeric would arrive as a string
    assert direct["edge"]["source_accession"] == f"{OTHER}-26-000001"
    assert direct["edge"]["member_label"] == f"{HUB} Member"
    # A second hop is weighted by both shares and names the company it sells to.
    assert (second["name"], second["via_cik"], second["tickers"]) == (
        "Sable Logistics",
        OTHER,
        ["SBLG"],
    )
    assert second["weight"] == pytest.approx(2.2)
    # Pending relationships never appear, and before valid_from nothing does.
    assert NEW not in {s["supplier_cik"] for s in body["suppliers"]}
    early = client.get(f"/companies/{HUB}/exposure?as_of=2026-01-01", headers=ana)
    assert early.json()["suppliers"] == []
    other = client.get(f"/companies/{OTHER}/exposure", headers=ana)
    assert other.status_code == 404


def test_only_a_reviewer_decides_a_relationship_once(
    db: psycopg.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, _ = app_with(db, monkeypatch)
    graph(db)
    rev = bearer(client, "rev@example.com")
    ana = bearer(client, "ana@example.com")
    assert client.get("/edges/review", headers=ana).status_code == 403
    [pending] = client.get("/edges/review", headers=rev).json()
    assert (pending["supplier"], pending["hub"]) == ("Pinecrest Paper", "Ford Motor Co")
    assert pending["filing_url"].startswith("https://www.sec.gov/Archives/")

    url = f"/edges/{pending['id']}/decision"
    approve = {"decision": "approved"}
    assert client.post(url, json=approve, headers=ana).status_code == 403
    assert client.post(url, json=approve, headers=rev).json() == {
        "id": pending["id"],
        "status": "approved",
    }
    row = db.execute(
        "SELECT status, reviewed_by FROM entity_edges WHERE id = %s", (pending["id"],)
    ).fetchone()
    assert row == ("approved", "rev@example.com")
    again = client.post(url, json={"decision": "rejected"}, headers=rev)
    assert again.status_code == 409
    assert client.get("/edges/review", headers=rev).json() == []
    missing = client.post("/edges/999999/decision", json=approve, headers=rev)
    assert missing.status_code == 404
