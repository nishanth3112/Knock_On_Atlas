import uuid
from datetime import UTC, datetime

import psycopg

from knock_on_atlas.db.migrate import migrate
from knock_on_atlas.workflow.analyses import publish, run_costs

JOB = {"hub_cik": "0000037996", "accession": "0000037996-25-000238"}


def rows(db: psycopg.Connection, analysis_id: str) -> list[tuple[object, ...]]:
    return db.execute(
        "SELECT status, result, completed_at IS NOT NULL FROM analyses WHERE id = %s",
        (analysis_id,),
    ).fetchall()


def test_a_published_brief_is_never_published_again(db: psycopg.Connection) -> None:
    migrate(db)
    run = str(uuid.uuid4())
    assert publish(db, run, JOB, "paused")
    assert rows(db, run) == [("paused", None, False)]

    assert publish(db, run, JOB, "published", {"headline": "first"})
    # A redelivered job finishing the same run again changes nothing.
    assert not publish(db, run, JOB, "published", {"headline": "second"})
    assert not publish(db, run, JOB, "ended")
    assert rows(db, run) == [("published", {"headline": "first"}, True)]


def test_each_runs_spend_is_reported_with_its_jev_cost(db: psycopg.Connection) -> None:
    migrate(db)
    priced, unpriced = str(uuid.uuid4()), str(uuid.uuid4())
    usage = {"llm_calls": 6, "tool_calls": 9, "tokens": 1400}
    publish(db, priced, JOB, "published", {"usage": usage, "jev_input_tokens": 50_000})
    publish(db, unpriced, JOB, "ended")  # ended at triage: nothing stored
    costs = {r["id"]: r for r in run_costs(db, datetime(2000, 1, 1, tzinfo=UTC))}
    assert costs[priced]["chat_tokens"] == 1400 and costs[priced]["llm_calls"] == 6
    # 50,000 input tokens at $0.042 per million.
    assert costs[priced]["jev_usd"] == 0.0021 and costs[priced]["chat_usd"] == 0.0
    assert (
        costs[unpriced]["jev_input_tokens"] == 0 and costs[unpriced]["llm_calls"] == 0
    )
