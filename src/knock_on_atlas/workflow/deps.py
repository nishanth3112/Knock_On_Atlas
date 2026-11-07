from dataclasses import dataclass

import psycopg

from knock_on_atlas.agents.llm import Models
from knock_on_atlas.agents.loop import ToolCaller
from knock_on_atlas.jev.client import JevCall, JevClient, record_call
from knock_on_atlas.workflow.state import JevDecision


@dataclass(frozen=True)
class Deps:
    """What the nodes share: the worker's DB, models, Jev and MCP tool caller."""

    conn: psycopg.Connection
    models: Models
    jev: JevClient | None  # None when JEV_ROUTE=off: nodes use their rule fallbacks
    call_tool: ToolCaller

    def log_jev(
        self, call: JevCall, question: str, answer: str, p: float | None
    ) -> JevDecision:
        record_call(self.conn, call, question)
        return JevDecision(
            question=question,
            answer=answer,
            probability=p,
            request_id=call.request_id,
            input_tokens=call.response.usage.input_tokens,
        )
