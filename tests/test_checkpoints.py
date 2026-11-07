import asyncio
from typing import Any

import pytest
from conftest import DYNAMODB_LOCAL
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Command, interrupt

from knock_on_atlas.config import Settings
from knock_on_atlas.workflow.checkpoints import checkpointer, dynamodb_saver
from knock_on_atlas.workflow.graph import run_config
from knock_on_atlas.workflow.state import ExposureBrief, KOAState, Triage

TRIAGE = Triage(label="MATERIAL_NEGATIVE", method="rule", reason="item 2.06")
BRIEF = ExposureBrief(headline="Gentherm exposed to Ford EV cuts", claims=[], gaps=[])


def review_graph(table: str) -> CompiledStateGraph[Any]:
    """triage -> interrupt -> brief, on its own saver instance like a new process."""

    def triage(state: KOAState) -> dict[str, Any]:
        return {"triage": TRIAGE}

    def review(state: KOAState) -> dict[str, Any]:
        return {"review": interrupt({"run_id": state["run_id"]})}

    def brief(state: KOAState) -> dict[str, Any]:
        return {"brief": BRIEF}

    builder = StateGraph(KOAState)
    builder.add_node("triage", triage)
    builder.add_node("human_review", review)
    builder.add_node("brief_synthesizer", brief)
    builder.add_edge(START, "triage")
    builder.add_edge("triage", "human_review")
    builder.add_edge("human_review", "brief_synthesizer")
    builder.add_edge("brief_synthesizer", END)
    return builder.compile(checkpointer=dynamodb_saver(DYNAMODB_LOCAL, table))


def test_a_paused_run_resumes_from_dynamodb_on_another_saver(
    checkpoint_table: str, caplog: pytest.LogCaptureFixture
) -> None:
    config = run_config("run-1")

    async def go() -> tuple[dict[str, Any], dict[str, Any]]:
        first = await review_graph(checkpoint_table).ainvoke(
            {"run_id": "run-1"}, config, durability="sync"
        )
        second = review_graph(checkpoint_table)
        assert (await second.aget_state(config)).values["triage"] == TRIAGE
        decision = {"decision": "approved", "reviewer": "test"}
        return first, await second.ainvoke(Command(resume=decision), config)

    paused, done = asyncio.run(go())
    assert "__interrupt__" in paused and "brief" not in paused
    assert done["brief"] == BRIEF and done["review"]["decision"] == "approved"
    # Without the allowlist the default serde still revives them, but logs this.
    assert "unregistered type" not in caplog.text


def test_memory_is_the_default_checkpointer() -> None:
    settings = Settings(_env_file=None, sec_user_agent="Test test@example.com")
    assert isinstance(checkpointer(settings), InMemorySaver)
