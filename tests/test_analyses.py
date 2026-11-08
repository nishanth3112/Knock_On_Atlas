import uuid

import psycopg

from knock_on_atlas.db.migrate import migrate
from knock_on_atlas.workflow.analyses import publish

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
