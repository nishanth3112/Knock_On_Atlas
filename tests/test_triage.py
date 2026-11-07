import asyncio
from typing import Any

import psycopg
import pytest
from fakes import choice, fake_jev, scripted
from langgraph.graph import END

from knock_on_atlas.agents.llm import Models
from knock_on_atlas.agents.triage import route_after_triage, triage
from knock_on_atlas.db.migrate import migrate
from knock_on_atlas.jev.client import JevClient
from knock_on_atlas.jev.questions import J1
from knock_on_atlas.workflow.deps import Deps

FORD = "0000037996"
LABELS = ("MATERIAL_NEGATIVE", "MATERIAL_POSITIVE", "ROUTINE", "NOT_SUPPLY_CHAIN")


def eight_k(items: list[str]) -> dict[str, Any]:
    return {
        "cik": FORD,
        "accession": "0000037996-25-000238",
        "accepted_at": "2025-12-15T21:07:14Z",
        "items": items,
        "url": "https://www.sec.gov/x.htm",
        "excerpt": "Item 8.01. Other Events. Ford priced $2 billion of notes.",
        "exhibit_url": None,
        "exhibit_excerpt": None,
    }


def run(
    db: psycopg.Connection, items: list[str], jev: JevClient | None
) -> dict[str, Any]:
    migrate(db)

    async def call_tool(name: str, args: dict[str, Any]) -> Any:
        return eight_k(items) if name == "get_8k_items" else {"name": "Ford Motor Co"}

    no_llm = scripted()  # any chat-model call would fail: triage makes none
    deps = Deps(db, Models(no_llm, no_llm, no_llm), jev, call_tool)
    return asyncio.run(triage({"hub_cik": FORD, "accession": "a"}, deps))


def test_rule_items_are_never_overridden_by_jev(db: psycopg.Connection) -> None:
    seen: list[Any] = []
    jev = fake_jev({J1: choice("ROUTINE", 0.99, LABELS[:2])}, seen)
    update = run(db, ["2.06", "7.01", "9.01"], jev)
    assert update["triage"].label == "MATERIAL_NEGATIVE"
    assert update["triage"].method == "rule" and seen == []
    assert route_after_triage(update) == "planner"


@pytest.mark.parametrize(
    ("p", "label"), [(0.95, "MATERIAL_POSITIVE"), (0.6, "MONITOR")]
)
def test_free_text_items_go_to_j1_and_low_confidence_is_monitor(
    db: psycopg.Connection, p: float, label: str
) -> None:
    seen: list[Any] = []
    jev = fake_jev({J1: choice("MATERIAL_POSITIVE", p, ("ROUTINE",))}, seen)
    update = run(db, ["8.01"], jev)
    assert update["triage"].label == label
    assert seen[0]["state"]["filer"] == "Ford Motor Co"
    assert update["jev"][0].question == "j1"
    assert db.execute("SELECT purpose, input_tokens FROM jev_calls").fetchall() == [
        ("j1", 100)
    ]


def test_routine_ends_the_run_without_llm_calls(db: psycopg.Connection) -> None:
    jev = fake_jev({J1: choice("ROUTINE", 0.97, LABELS[:2])})
    update = run(db, ["8.01", "9.01"], jev)
    assert update["triage"].label == "ROUTINE"
    assert route_after_triage(update) == END
    assert update["usage"].llm_calls == 0


def test_jev_off_sends_free_text_items_to_monitor(db: psycopg.Connection) -> None:
    assert run(db, ["7.01"], None)["triage"].label == "MONITOR"
    assert run(db, ["5.02"], None)["triage"].label == "ROUTINE"
