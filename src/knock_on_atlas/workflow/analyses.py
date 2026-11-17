"""The analyses table: where a run's outcome, and its brief, is published."""

from datetime import datetime
from typing import Any, Literal

import psycopg
from psycopg.types.json import Jsonb

Status = Literal["queued", "paused", "published", "rejected", "ended"]


def publish(
    conn: psycopg.Connection,
    analysis_id: str,
    input: dict[str, Any],
    status: Status,
    result: dict[str, Any] | None = None,
) -> bool:
    """Record a run's outcome; False if nothing changed.

    A redelivered job can finish the same run twice, so a published row is final:
    the same analysis_id never gets a second brief.
    """
    row = conn.execute(
        "INSERT INTO analyses (id, input, status, result, completed_at)"
        " VALUES (%s, %s, %s, %s, CASE WHEN %s = 'paused' THEN NULL ELSE now() END)"
        " ON CONFLICT (id) DO UPDATE SET status = EXCLUDED.status,"
        " result = EXCLUDED.result, completed_at = EXCLUDED.completed_at"
        " WHERE analyses.status <> 'published'"
        " RETURNING id",
        (
            analysis_id,
            Jsonb(input),
            status,
            Jsonb(result) if result is not None else None,
            status,
        ),
    ).fetchone()
    return row is not None


# Spec §12: Jev costs $0.042 per 1M input tokens; output is free.
JEV_USD_PER_INPUT_TOKEN = 0.042 / 1_000_000


def run_costs(conn: psycopg.Connection, since: datetime) -> list[dict[str, Any]]:
    """What each analysis since `since` spent: model calls, tokens and Jev cost.

    Chat models cost $0 here (Ollama locally, Groq's free tier), so only Jev is priced.
    """
    rows = conn.execute(
        "SELECT id, status, input->>'accession', created_at, result->'usage',"
        " (result->>'jev_input_tokens')::int FROM analyses"
        " WHERE created_at >= %s ORDER BY created_at",
        (since,),
    ).fetchall()
    runs = []
    for analysis_id, status, accession, created_at, usage, jev_tokens in rows:
        usage = usage or {}
        jev_tokens = jev_tokens or 0
        runs.append(
            {
                "id": str(analysis_id),
                "status": status,
                "accession": accession,
                "created_at": created_at.isoformat(),
                "llm_calls": usage.get("llm_calls", 0),
                "tool_calls": usage.get("tool_calls", 0),
                "chat_tokens": usage.get("tokens", 0),
                "jev_input_tokens": jev_tokens,
                "jev_usd": round(jev_tokens * JEV_USD_PER_INPUT_TOKEN, 6),
                "chat_usd": 0.0,
            }
        )
    return runs
