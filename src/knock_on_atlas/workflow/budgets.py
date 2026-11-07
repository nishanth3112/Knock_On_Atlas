"""Per-run limits (spec §11.3). Running out ends the run with BUDGET_EXHAUSTED."""

import time

from knock_on_atlas.workflow.state import Usage

MAX_LLM_CALLS = 12
MAX_TOOL_CALLS = 30
MAX_TOKENS = 25_000
MAX_VERIFIER_LOOPS = 2
RECURSION_LIMIT = 25
WALL_CLOCK_SECONDS = 240
BUDGET_EXHAUSTED = "BUDGET_EXHAUSTED"
# Tokens the agents before the brief leave unspent: one call can overshoot by a few
# thousand, and the brief may need two attempts.
SYNTHESIS_RESERVE_TOKENS = 10_000


def exhausted(
    usage: Usage, deadline: float, *, tool: bool = False, reserve: int = 0
) -> str | None:
    """Which limit the next LLM call (or, with tool=True, tool call) would break."""
    if time.time() >= deadline:
        return f"{BUDGET_EXHAUSTED}: wall clock"
    if tool and usage.tool_calls >= MAX_TOOL_CALLS:
        return f"{BUDGET_EXHAUSTED}: {MAX_TOOL_CALLS} tool calls"
    if not tool and usage.llm_calls >= MAX_LLM_CALLS:
        return f"{BUDGET_EXHAUSTED}: {MAX_LLM_CALLS} LLM calls"
    if not tool and usage.tokens >= MAX_TOKENS - reserve:
        return f"{BUDGET_EXHAUSTED}: {MAX_TOKENS} tokens"
    return None
