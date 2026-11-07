import asyncio
import time
from datetime import UTC, date, datetime
from typing import Any

import psycopg
from fakes import call, scripted
from langchain_core.messages import AIMessage

from knock_on_atlas.agents.llm import Models
from knock_on_atlas.agents.synthesizer import brief_synthesizer, problems
from knock_on_atlas.mcp_server.server import EightK
from knock_on_atlas.workflow.deps import Deps
from knock_on_atlas.workflow.state import (
    Claim,
    ClaimCheck,
    Evidence,
    ExposureBrief,
    ExposureEdge,
    KOAState,
    StudyRow,
    VerificationReport,
)

FORD, GOOD, BAD = "0000037996", "0000012345", "0000054321"
QUOTE = "Ford accounted for 22% of our product revenues."


def state() -> KOAState:
    return {
        "hub_name": "Ford Motor Co",
        "deadline": time.time() + 60,
        "event": EightK(
            cik=FORD,
            accession="a",
            accepted_at=datetime(2025, 12, 15, tzinfo=UTC),
            items=["2.06"],
            url="u",
            excerpt="Ford expects a $3 billion charge on its EV battery venture.",
            exhibit_url=None,
            exhibit_excerpt=None,
        ),
        "exposures": [
            ExposureEdge(
                supplier_cik=GOOD, name="Gentherm", depth=1, weight=22, note="EV seats."
            ),
            ExposureEdge(supplier_cik=BAD, name="Other", depth=1, weight=15, note=""),
        ],
        "evidence": [
            Evidence(supplier_cik=GOOD, chunk_id=11, quote=QUOTE, url="u"),
            Evidence(supplier_cik=BAD, chunk_id=12, quote="Made up.", url="u"),
        ],
        "verification": VerificationReport(
            ok=False,
            checks=[
                ClaimCheck(
                    supplier_cik=GOOD,
                    chunk_id=11,
                    citation_ok=True,
                    numbers_ok=True,
                    score=4,
                ),
                ClaimCheck(
                    supplier_cik=BAD,
                    chunk_id=12,
                    citation_ok=False,
                    numbers_ok=True,
                    score=0,
                ),
            ],
            failed_suppliers=[BAD],
        ),
        "market": [
            StudyRow(
                cik=GOOD,
                depth=1,
                status="complete",
                day0=date(2025, 12, 16),
                car_0_1=-0.042,
                car_m1_5=-0.05,
                t_0_1=-2.1,
                t_m1_5=-1.0,
            )
        ],
    }


def brief(text: str, citations: list[int]) -> dict[str, Any]:
    claim = {"text": text, "supplier_ciks": [GOOD], "citations": citations}
    return {
        "headline": "Gentherm exposed to Ford's EV charge",
        "claims": [claim],
        "gaps": [],
    }


def synthesize(
    db: psycopg.Connection, *replies: AIMessage
) -> tuple[dict[str, Any], Any]:
    async def no_tools(name: str, args: dict[str, Any]) -> Any:
        raise AssertionError("the synthesizer has no tools")

    llm = scripted(*replies)
    update = asyncio.run(
        brief_synthesizer(state(), Deps(db, Models(llm, llm, llm), None, no_tools))
    )
    return update, llm


def test_a_cited_brief_built_from_verified_facts_is_kept(
    db: psycopg.Connection,
) -> None:
    good = brief(
        "Ford is 22% of Gentherm's revenue; Ford takes a $3 billion charge.", [11]
    )
    update, llm = synthesize(db, call("submit", **good))
    assert update["brief"].claims[0].citations == [11]
    assert update["brief"].gaps == [f"no verified evidence for {BAD}"]
    facts = str(llm.__dict__["seen"][0][1].content)
    # Unverified quotes never reach the model; returns are labelled descriptive.
    assert QUOTE in facts and "Made up." not in facts
    assert "CAR[0,+1] -4.2%" in facts and "not a signal" in facts


def test_a_rejected_brief_gets_one_retry_with_the_reasons(
    db: psycopg.Connection,
) -> None:
    bad = brief("Investors should sell the stock: Ford is 40% of revenue.", [12])
    good = brief("Ford is 22% of Gentherm's revenue.", [11])
    update, llm = synthesize(db, call("submit", **bad), call("submit", **good))
    assert update["brief"].claims[0].text == good["claims"][0]["text"]
    assert update["usage"].llm_calls == 2
    retry_task = str(llm.__dict__["seen"][1][1].content)
    assert (
        "investment advice" in retry_task
        and "['40']" in retry_task
        and "[12]" in retry_task
    )


def test_a_brief_rejected_twice_is_not_published(db: psycopg.Connection) -> None:
    uncited = brief("Ford is 22% of Gentherm's revenue.", [])
    update, _ = synthesize(db, call("submit", **uncited), call("submit", **uncited))
    assert "brief" not in update
    assert "rejected twice" in update["errors"][0]


def test_problems_lists_each_rule() -> None:
    ok = ExposureBrief(
        headline="h", claims=[Claim(text="t", supplier_ciks=[], citations=[1])], gaps=[]
    )
    assert problems(ok, {1}, "") == []
    assert problems(ExposureBrief(headline="h", claims=[], gaps=[]), set(), "") == [
        "the brief has no claims"
    ]
