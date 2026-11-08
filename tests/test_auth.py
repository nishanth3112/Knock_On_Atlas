import time

import jwt
import psycopg
import pytest
from fastapi.testclient import TestClient

from knock_on_atlas.api.app import create_app, get_conn
from knock_on_atlas.api.auth import AUDIENCE, Role, hash_password
from knock_on_atlas.config import get_settings
from knock_on_atlas.db.migrate import migrate

SECRET = "test-api-secret-at-least-32-bytes!!"
PASSWORD = "correct horse battery"


def api(db: psycopg.Connection, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """The real app on the test schema, with a known signing secret."""
    monkeypatch.setenv("JWT_SECRET", SECRET)
    get_settings.cache_clear()  # the db fixture already read the settings
    migrate(db)
    app = create_app()
    app.dependency_overrides[get_conn] = lambda: db
    return TestClient(app)


def add_user(db: psycopg.Connection, email: str, role: Role) -> None:
    db.execute(
        "INSERT INTO users (email, password_hash, role) VALUES (%s, %s, %s)",
        (email, hash_password(PASSWORD), role),
    )


def bearer(client: TestClient, email: str) -> dict[str, str]:
    response = client.post("/auth/login", json={"email": email, "password": PASSWORD})
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def test_login_returns_a_token_for_me(
    db: psycopg.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = api(db, monkeypatch)
    add_user(db, "ana@example.com", "analyst")
    # Emails match without regard to case or stray spaces.
    me = client.get("/auth/me", headers=bearer(client, " Ana@Example.com "))
    assert me.status_code == 200
    assert me.json()["email"] == "ana@example.com" and me.json()["role"] == "analyst"


@pytest.mark.parametrize(
    ("email", "password"),
    [("ana@example.com", "wrong password"), ("nobody@example.com", PASSWORD)],
)
def test_wrong_password_or_unknown_email_is_401(
    db: psycopg.Connection, monkeypatch: pytest.MonkeyPatch, email: str, password: str
) -> None:
    client = api(db, monkeypatch)
    add_user(db, "ana@example.com", "analyst")
    response = client.post("/auth/login", json={"email": email, "password": password})
    assert response.status_code == 401
    assert response.json()["detail"] == "wrong email or password"


@pytest.mark.parametrize(
    "claims",
    [
        {"exp": int(time.time()) - 1, "aud": AUDIENCE},  # expired
        {"exp": int(time.time()) + 60, "aud": "edgar-intel"},  # someone else's token
        {"exp": int(time.time()) + 60},  # no audience
    ],
)
def test_expired_or_foreign_tokens_are_401(
    db: psycopg.Connection, monkeypatch: pytest.MonkeyPatch, claims: dict[str, object]
) -> None:
    client = api(db, monkeypatch)
    token = jwt.encode(
        {"sub": "1", "email": "x@example.com", "role": "admin", **claims},
        SECRET,
        algorithm="HS256",
    )
    response = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 401


def test_a_token_signed_with_another_secret_is_401(
    db: psycopg.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = api(db, monkeypatch)
    claims = {"sub": "1", "email": "x@example.com", "role": "admin", "aud": AUDIENCE}
    token = jwt.encode(
        {**claims, "exp": int(time.time()) + 60},
        "another-secret-at-least-32-bytes!!!",
        algorithm="HS256",
    )
    response = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 401


def test_the_api_refuses_to_start_without_a_jwt_secret() -> None:
    with pytest.raises(RuntimeError, match="JWT_SECRET"):
        create_app()


def test_no_token_is_refused(
    db: psycopg.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert api(db, monkeypatch).get("/auth/me").status_code == 401
