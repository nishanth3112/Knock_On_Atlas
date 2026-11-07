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

RUN_LIMIT = Usage(llm_calls=MAX_LLM_CALLS, tool_calls=MAX_TOOL_CALLS, tokens=MAX_TOKENS)
# What the agents before the brief may reach. The brief keeps two calls (it may need
# a retry) and 10k tokens (one call can also overshoot by a few thousand).
BEFORE_BRIEF = Usage(
    llm_calls=MAX_LLM_CALLS - 2, tool_calls=MAX_TOOL_CALLS, tokens=MAX_TOKENS - 10_000
)


def branch_limit(spent: Usage, branches: int) -> Usage:
    """One parallel branch's ceiling: its share of what is left before the brief.

    Branches check their own snapshot of the spend, so without a share each one
    would take the whole remainder.
    """

    def share(used: int, cap: int) -> int:
        return used + max(cap - used, 0) // branches

    return Usage(
        llm_calls=share(spent.llm_calls, BEFORE_BRIEF.llm_calls),
        tool_calls=share(spent.tool_calls, BEFORE_BRIEF.tool_calls),
        tokens=share(spent.tokens, BEFORE_BRIEF.tokens),
    )


def exhausted(
    usage: Usage, deadline: float, *, tool: bool = False, limit: Usage = RUN_LIMIT
) -> str | None:
    """Which limit the next LLM call (or, with tool=True, tool call) would break."""
    if time.time() >= deadline:
        return f"{BUDGET_EXHAUSTED}: wall clock"
    if tool and usage.tool_calls >= limit.tool_calls:
        return f"{BUDGET_EXHAUSTED}: {limit.tool_calls} tool calls"
    if not tool and usage.llm_calls >= limit.llm_calls:
        return f"{BUDGET_EXHAUSTED}: {limit.llm_calls} LLM calls"
    if not tool and usage.tokens >= limit.tokens:
        return f"{BUDGET_EXHAUSTED}: {limit.tokens} tokens"
    return None
