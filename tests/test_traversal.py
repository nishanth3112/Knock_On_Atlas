import random
from datetime import date, timedelta

import psycopg
import pytest

from knock_on_atlas.db.migrate import migrate
from knock_on_atlas.graph_store.edges import Edge, replace_edges
from knock_on_atlas.graph_store.traversal import MAX_DEPTH, exposure

START = date(2025, 1, 1)


def edge(
    src: str,
    dst: str,
    pct: float,
    status: str = "approved",
    valid_from: date = START,
    valid_to: date | None = None,
) -> Edge:
    return Edge(
        src_cik=src,
        dst_cik=dst,
        pct=pct,
        period_end=START,
        method="xbrl",
        extraction_method="alias_exact",
        member_label=dst,
        total_share=True,
        relationship_flag=None,
        source_accession=f"{src}-{dst}",
        confidence=1.0,
        status=status,
        valid_from=valid_from,
        valid_to=valid_to,
    )


@pytest.fixture
def graph(db: psycopg.Connection) -> psycopg.Connection:
    migrate(db)
    replace_edges(
        db,
        [
            edge("A", "H", 30),
            edge("B", "H", 20, valid_to=date(2025, 6, 1)),
            edge("C", "H", 10, status="pending"),
            edge("G", "H", 15, status="rejected"),
            edge("F", "H", 25, valid_from=date(2026, 1, 1)),
            edge("D", "A", 50),
            edge("E", "D", 40),  # a third hop
            edge("H", "A", 10),  # the hub supplies its own supplier: a cycle
        ],
    )
    return db


def found(conn: psycopg.Connection, as_of: date) -> list[tuple[str, str, float, int]]:
    return [
        (x.supplier_cik, x.via_cik, x.weight, x.depth)
        for x in exposure(conn, "H", as_of)
    ]


def test_two_hops_with_product_weight_and_no_cycle(graph: psycopg.Connection) -> None:
    assert found(graph, date(2025, 3, 1)) == [
        ("A", "H", 30.0, 1),
        ("B", "H", 20.0, 1),
        ("D", "A", 15.0, 2),
    ]


def test_validity_window_end_is_exclusive(graph: psycopg.Connection) -> None:
    assert [s for s, *_ in found(graph, date(2025, 5, 31))] == ["A", "B", "D"]
    assert [s for s, *_ in found(graph, date(2025, 6, 1))] == ["A", "D"]


def test_edges_count_only_from_valid_from(graph: psycopg.Connection) -> None:
    assert [s for s, *_ in found(graph, date(2026, 2, 1))] == ["A", "F", "D"]
    assert found(graph, date(2024, 12, 31)) == []


def test_path_lists_edge_ids_hub_first(graph: psycopg.Connection) -> None:
    ids = dict(
        graph.execute(
            "SELECT src_cik || '>' || dst_cik, id FROM entity_edges"
        ).fetchall()
    )
    second = exposure(graph, "H", date(2025, 3, 1))[-1]
    assert second.edge_ids == (ids["A>H"], ids["D>A"])


def reference(
    rows: list[tuple[int, str, str, float, str, date, date | None]],
    hub: str,
    as_of: date,
) -> set[tuple[str, str, int, float, tuple[int, ...]]]:
    """Plain-Python walk of the same rules, to check the SQL against."""
    live = [
        r
        for r in rows
        if r[4] == "approved" and r[5] <= as_of and (r[6] is None or r[6] > as_of)
    ]
    out = set()
    frontier = [(hub, 100.0, (), (hub,))]
    for depth in range(1, MAX_DEPTH + 1):
        nxt = []
        for node, weight, path, nodes in frontier:
            for eid, src, dst, pct, *_ in live:
                if dst == node and src not in nodes:
                    w = pct if depth == 1 else weight * pct / 100
                    out.add((src, node, depth, round(w, 6), (*path, eid)))
                    nxt.append((src, w, (*path, eid), (*nodes, src)))
        frontier = nxt
    return out


@pytest.mark.parametrize("seed", range(5))
def test_matches_reference_on_random_graphs(db: psycopg.Connection, seed: int) -> None:
    # 20 companies, 40 edges with mixed statuses, windows and cycles.
    rng = random.Random(seed)
    companies = [f"C{i:02}" for i in range(20)]
    pairs = rng.sample([(s, d) for s in companies for d in companies if s != d], 40)

    def day() -> date:
        return START + timedelta(days=rng.randrange(730))

    edges = []
    for src, dst in pairs:
        valid_from = day()
        valid_to = rng.choice(
            [None, valid_from + timedelta(days=rng.randrange(1, 400))]
        )
        status = rng.choice(["approved"] * 4 + ["pending", "rejected"])
        edges.append(edge(src, dst, rng.randrange(5, 60), status, valid_from, valid_to))
    migrate(db)
    replace_edges(db, edges)
    rows = db.execute(
        "SELECT id, src_cik, dst_cik, pct::float, status, valid_from, valid_to"
        " FROM entity_edges"
    ).fetchall()
    for hub in rng.sample(companies, 5):
        for as_of in (day(), day()):
            got = {
                (x.supplier_cik, x.via_cik, x.depth, round(x.weight, 6), x.edge_ids)
                for x in exposure(db, hub, as_of)
            }
            assert got == reference(rows, hub, as_of)
