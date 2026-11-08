"""The HTTP API (spec §16): login, analyses, reviews, health."""

from collections.abc import Callable, Iterator
from typing import Annotated, Any

import jwt
import psycopg
from fastapi import Depends, FastAPI, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel

from knock_on_atlas.api.auth import (
    RANK,
    Role,
    User,
    check_password,
    issue_token,
    read_token,
)
from knock_on_atlas.config import get_settings


def get_conn() -> Iterator[psycopg.Connection]:
    """One connection per request: demo traffic, and Lambda reuses nothing anyway."""
    url = get_settings().database_url.get_secret_value()
    with psycopg.connect(url, autocommit=True) as conn:
        yield conn


Conn = Annotated[psycopg.Connection, Depends(get_conn)]


class Login(BaseModel):
    email: str
    password: str


def create_app() -> FastAPI:
    secret_value = get_settings().jwt_secret
    if secret_value is None:
        raise RuntimeError("JWT_SECRET is required to run the API")
    secret = secret_value.get_secret_value()
    app = FastAPI(title="Knock-On Atlas")

    def current_user(
        credentials: Annotated[HTTPAuthorizationCredentials, Depends(HTTPBearer())],
    ) -> User:
        try:
            return read_token(credentials.credentials, secret)
        except (jwt.InvalidTokenError, KeyError, ValueError):
            raise HTTPException(401, "invalid or expired token") from None

    def require(role: Role) -> Callable[[User], User]:
        def check(user: Annotated[User, Depends(current_user)]) -> User:
            if RANK[user.role] < RANK[role]:
                raise HTTPException(403, f"needs the {role} role")
            return user

        return check

    @app.get("/health")
    def health() -> dict[str, Any]:
        return {"ok": True}

    @app.post("/auth/login")
    def login(body: Login, conn: Conn) -> dict[str, str]:
        row = conn.execute(
            "SELECT id, email, password_hash, role FROM users WHERE email = %s",
            (body.email.strip().lower(),),
        ).fetchone()
        # Unknown emails still pay for a hash check: no user enumeration by timing.
        ok = check_password(row[2] if row else None, body.password)
        if row is None or not ok:
            raise HTTPException(401, "wrong email or password")
        user = User(id=row[0], email=row[1], role=row[3])
        return {"access_token": issue_token(user, secret), "token_type": "bearer"}

    @app.get("/auth/me")
    def me(user: Annotated[User, Depends(current_user)]) -> User:
        return user

    return app
