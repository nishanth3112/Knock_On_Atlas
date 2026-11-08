"""The analyses table: where a run's outcome, and its brief, is published."""

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
