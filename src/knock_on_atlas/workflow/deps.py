import os
import time
from dataclasses import dataclass, field

import psycopg

from knock_on_atlas.agents.llm import Models
from knock_on_atlas.agents.loop import ToolCaller
from knock_on_atlas.jev.client import JevCall, JevClient, record_call
from knock_on_atlas.workflow.budgets import WALL_CLOCK_SECONDS
from knock_on_atlas.workflow.state import JevDecision

CRASH_EXIT = 75


@dataclass(frozen=True)
class Deps:
    """What the nodes share: the worker's DB, models, Jev and MCP tool caller."""

    conn: psycopg.Connection
    models: Models
    jev: JevClient | None  # None when JEV_ROUTE=off: nodes use their rule fallbacks
    call_tool: ToolCaller
    # Epoch seconds. Each worker invocation builds its own Deps, so a run resumed
    # after a crash or a review gets a fresh window, like a fresh Lambda timeout.
    deadline: float = field(default_factory=lambda: time.time() + WALL_CLOCK_SECONDS)
    # Durability suite only: "<node>:before|after" or "publish:after".
    crash_at: str | None = None

    def crash(self, point: str) -> None:
        if point == self.crash_at:
            # A hard kill, like a Lambda timeout: no cleanup and no checkpoint write.
            os._exit(CRASH_EXIT)

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
