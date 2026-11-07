import asyncio
from datetime import date
from typing import Any

import psycopg
import pytest
from fakes import fake_jev, scripted
from test_mcp_server import FORD, SUPPLIER, seeded_server

from knock_on_atlas.agents.llm import Models
from knock_on_atlas.agents.verifier import route_after_verify, verifier
from knock_on_atlas.edgar.client import SecClient
from knock_on_atlas.jev.client import JevClient
from knock_on_atlas.retrieval.embed import Embedder
from knock_on_atlas.workflow.deps import Deps
from knock_on_atlas.workflow.state import (
    Evidence,
    KOAState,
    Plan,
    Supplier,
    VerificationReport,
)

QUOTE = "Ford accounted for 22% of our product revenues."


@pytest.fixture
def seeded(
    db: psycopg.Connection, sec_client: SecClient, fake_embedder: Embedder
) -> tuple[int, int]:
    seeded_server(db, sec_client, fake_embedder)
    [(chunk_id,)] = db.execute("SELECT id FROM document_chunks").fetchall()
    [(edge_id,)] = db.execute("SELECT id FROM entity_edges").fetchall()
    return chunk_id, edge_id


def verify(
    db: psycopg.Connection,
    ids: tuple[int, int],
    quotes: list[str],
    jev: JevClient | None = None,
    loops: int = 0,
) -> dict[str, Any]:
    chunk_id, edge_id = ids
    state: KOAState = {
        "plan": Plan(
            hub_cik=FORD,
            as_of=date(2026, 1, 2),
            suppliers=[
                Supplier(
                    cik=SUPPLIER, via_cik=FORD, depth=1, weight=22, edge_ids=[edge_id]
                )
            ],
            queries=[],
        ),
        "evidence": [
            Evidence(supplier_cik=SUPPLIER, chunk_id=chunk_id, quote=q, url="u")
            for q in quotes
        ],
        "loops": loops,
    }

    async def no_tools(name: str, args: dict[str, Any]) -> Any:
        raise AssertionError("the verifier reads the DB directly")

    llm = scripted()
    return asyncio.run(verifier(state, Deps(db, Models(llm, llm, llm), jev, no_tools)))


def score(level: float) -> dict[str, Any]:
    return {
        "type": "score",
        "score": level,
        "confidence": 0.9,
        "probabilities": {"4": 1.0},
    }


def test_a_quote_found_in_the_filing_with_the_edge_share_passes(
    db: psycopg.Connection, seeded: tuple[int, int]
) -> None:
    update = verify(db, seeded, ["Ford  accounted for 22% of our product\nrevenues."])
    report = update["verification"]
    assert report.ok and report.failed_suppliers == []
    assert "loops" not in update
    assert route_after_verify(update) == "risk_gate"


@pytest.mark.parametrize(
    ("quote", "citation", "numbers"),
    [
        ("Ford accounted for 35% of our product revenues.", False, False),
        ("Ford is our largest customer by far.", False, True),
    ],
)
def test_a_made_up_quote_or_a_wrong_share_fails(
    db: psycopg.Connection,
    seeded: tuple[int, int],
    quote: str,
    citation: bool,
    numbers: bool,
) -> None:
    update = verify(db, seeded, [quote])
    [check] = update["verification"].checks
    assert (check.citation_ok, check.numbers_ok) == (citation, numbers)
    assert update["verification"].failed_suppliers == [SUPPLIER]
    assert update["loops"] == 1
    assert route_after_verify(update) == "evidence_researcher"


def test_j4_scores_the_quotes_that_pass_the_code_checks(
    db: psycopg.Connection, seeded: tuple[int, int]
) -> None:
    seen: list[Any] = []
    update = verify(
        db, seeded, [QUOTE, "Ford is our customer."], fake_jev({"c0": score(1.4)}, seen)
    )
    # Only the first quote reached Jev; level 1 is below the pass mark of 3.
    assert list(seen[0]["questions"]) == ["c0"]
    assert seen[0]["questions"]["c0"]["type"] == "score"
    assert [c.score for c in update["verification"].checks] == [1, 0]
    assert not update["verification"].ok
    assert update["jev"][0].question == "j4"
    assert db.execute("SELECT purpose FROM jev_calls").fetchall() == [("j4",)]


def test_the_loop_stops_after_two_retries() -> None:
    failed = VerificationReport(ok=False, checks=[], failed_suppliers=[SUPPLIER])
    assert (
        route_after_verify({"verification": failed, "loops": 2})
        == "evidence_researcher"
    )
    assert route_after_verify({"verification": failed, "loops": 3}) == "human_review"


def test_no_retry_once_the_budget_is_spent() -> None:
    failed = VerificationReport(ok=False, checks=[], failed_suppliers=[SUPPLIER])
    spent = ["evidence_researcher: BUDGET_EXHAUSTED: 25000 tokens"]
    state = {"verification": failed, "loops": 1, "errors": spent}
    assert route_after_verify(state) == "human_review"  # type: ignore[arg-type]
