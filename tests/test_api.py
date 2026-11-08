import uuid
from datetime import UTC, datetime

import psycopg
import pytest
from fastapi.testclient import TestClient
from test_auth import add_user, api, bearer

from knock_on_atlas.api.demo import LiveGate
from knock_on_atlas.api.jobs import LocalQueue

HUB, OTHER = "0000037996", "0000012345"
FILING = "0000037996-25-000238"
ON = LiveGate(None, datetime(2100, 1, 1, tzinfo=UTC))
OFF = LiveGate(None, None)


def app_with(
    db: psycopg.Connection, monkeypatch: pytest.MonkeyPatch, gate: LiveGate = ON
) -> tuple[TestClient, LocalQueue]:
    queue = LocalQueue()
    client = api(db, monkeypatch, queue, gate)
    db.execute(
        "INSERT INTO companies (cik, name, is_hub) VALUES"
        " (%s, 'Ford Motor Co', true), (%s, 'Gentherm Inc', false)",
        (HUB, OTHER),
    )
    for email, role in [
        ("ana@example.com", "analyst"),
        ("bo@example.com", "analyst"),
        ("rev@example.com", "reviewer"),
    ]:
        add_user(db, email, role)  # type: ignore[arg-type]
    return client, queue


def start(client: TestClient, email: str, accession: str = FILING) -> dict[str, str]:
    response = client.post(
        "/analyses",
        json={"hub_cik": HUB, "accession": accession},
        headers=bearer(client, email),
    )
    assert response.status_code in (200, 202), response.text
    return response.json()


def test_one_filing_starts_one_run(
    db: psycopg.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, queue = app_with(db, monkeypatch)
    first = start(client, "ana@example.com")
    assert first["status"] == "queued"
    [job] = queue.jobs
    assert (job.analysis_id, job.hub_cik, job.accession) == (first["id"], HUB, FILING)
    assert job.decision is None

    # A second click, by anyone, returns the same analysis and queues nothing.
    again = client.post(
        "/analyses",
        json={"hub_cik": HUB, "accession": FILING},
        headers=bearer(client, "bo@example.com"),
    )
    assert again.status_code == 200 and again.json()["id"] == first["id"]
    assert len(queue.jobs) == 1


@pytest.mark.parametrize(
    ("body", "status"),
    [
        ({"hub_cik": HUB, "accession": "not-a-filing"}, 422),
        ({"hub_cik": OTHER, "accession": FILING}, 422),  # not a hub customer
        ({"hub_cik": "37996", "accession": FILING}, 422),
    ],
)
def test_bad_starts_are_refused(
    db: psycopg.Connection,
    monkeypatch: pytest.MonkeyPatch,
    body: dict[str, str],
    status: int,
) -> None:
    client, queue = app_with(db, monkeypatch)
    headers = bearer(client, "ana@example.com")
    assert client.post("/analyses", json=body, headers=headers).status_code == status
    assert queue.jobs == []


def test_listing_and_reading_analyses(
    db: psycopg.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, _ = app_with(db, monkeypatch)
    started = start(client, "ana@example.com")
    ana, bo = bearer(client, "ana@example.com"), bearer(client, "bo@example.com")
    assert [a["id"] for a in client.get("/analyses", headers=bo).json()] == [
        started["id"]
    ]
    assert client.get("/analyses?mine=true", headers=bo).json() == []
    assert len(client.get("/analyses?mine=true", headers=ana).json()) == 1

    one = client.get(f"/analyses/{started['id']}", headers=bo).json()
    assert one["status"] == "queued" and one["input"]["accession"] == FILING
    assert client.get(f"/analyses/{uuid.uuid4()}", headers=bo).status_code == 404
    assert client.get("/analyses/nope", headers=bo).status_code == 422
    assert client.get("/analyses").status_code == 401


def test_only_a_reviewer_decides_and_only_once(
    db: psycopg.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, queue = app_with(db, monkeypatch)
    analysis_id = start(client, "ana@example.com")["id"]
    queue.jobs.clear()
    review = {"decision": "approved", "note": "shares match the 10-K"}
    url = f"/reviews/{analysis_id}"
    rev = bearer(client, "rev@example.com")

    analyst = client.post(url, json=review, headers=bearer(client, "ana@example.com"))
    assert analyst.status_code == 403
    # Still queued: there is nothing to decide yet.
    assert client.post(url, json=review, headers=rev).status_code == 409

    db.execute("UPDATE analyses SET status = 'paused' WHERE id = %s", (analysis_id,))
    assert client.post(url, json=review, headers=rev).status_code == 202
    [job] = queue.jobs
    assert job.analysis_id == analysis_id and job.accession == FILING
    assert job.decision == {
        "decision": "approved",
        "reviewer": "rev@example.com",
        "note": "shares match the 10-K",
    }
    # A second, conflicting decision is refused and queues nothing.
    second = client.post(url, json={"decision": "rejected"}, headers=rev)
    assert second.status_code == 409 and len(queue.jobs) == 1
    reviews = db.execute(
        "SELECT decision, comment FROM human_reviews WHERE analysis_id = %s",
        (analysis_id,),
    ).fetchall()
    assert reviews == [("approved", "shares match the 10-K")]

    missing = client.post(f"/reviews/{uuid.uuid4()}", json=review, headers=rev)
    assert missing.status_code == 404


def test_when_live_runs_are_off_only_reading_works(
    db: psycopg.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, queue = app_with(db, monkeypatch, gate=OFF)
    ana = bearer(client, "ana@example.com")
    body = {"hub_cik": HUB, "accession": FILING}
    assert client.post("/analyses", json=body, headers=ana).status_code == 503
    review = client.post(
        f"/reviews/{uuid.uuid4()}",
        json={"decision": "approved"},
        headers=bearer(client, "rev@example.com"),
    )
    assert review.status_code == 503 and queue.jobs == []
    assert client.get("/analyses", headers=ana).status_code == 200
    assert client.get("/health").json() == {"ok": True, "live": False}


def test_an_analyst_starts_at_most_five_runs_a_day(
    db: psycopg.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Every run can spend paid Jev calls; one leaked login must not drain them.
    client, queue = app_with(db, monkeypatch)
    ana = bearer(client, "ana@example.com")
    filings = [f"0000037996-25-00{n:04d}" for n in range(6)]
    for filing in filings[:5]:
        assert start(client, "ana@example.com", filing)["status"] == "queued"
    sixth = client.post(
        "/analyses", json={"hub_cik": HUB, "accession": filings[5]}, headers=ana
    )
    assert sixth.status_code == 429 and len(queue.jobs) == 5
    # Asking for a filing that already has a run is free.
    assert start(client, "ana@example.com", filings[0])["status"] == "queued"
    assert start(client, "bo@example.com", filings[5])["status"] == "queued"


def test_a_filing_whose_run_ended_is_not_run_again(
    db: psycopg.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, queue = app_with(db, monkeypatch)
    first = start(client, "ana@example.com")
    db.execute("UPDATE analyses SET status = 'ended' WHERE id = %s", (first["id"],))
    again = start(client, "bo@example.com")
    assert again == {"id": first["id"], "status": "ended"} and len(queue.jobs) == 1


def test_a_run_queued_an_hour_ago_no_longer_blocks_its_filing(
    db: psycopg.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, queue = app_with(db, monkeypatch)
    first = start(client, "ana@example.com")
    db.execute(
        "UPDATE analyses SET created_at = now() - interval '61 minutes' WHERE id = %s",
        (first["id"],),
    )
    again = start(client, "bo@example.com")
    assert again["status"] == "queued" and again["id"] != first["id"]
    assert len(queue.jobs) == 2


def test_oversized_login_fields_are_refused_before_hashing(
    db: psycopg.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, _ = app_with(db, monkeypatch)
    body = {"email": "ana@example.com", "password": "x" * 10_000}
    assert client.post("/auth/login", json=body).status_code == 422
