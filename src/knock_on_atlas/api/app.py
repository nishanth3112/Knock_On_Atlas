"""The HTTP API (spec §16): login, analyses, reviews, health."""

import uuid
from collections.abc import Callable, Iterator
from typing import Annotated, Any, Literal

import jwt
import psycopg
from fastapi import Depends, FastAPI, HTTPException, Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from psycopg.types.json import Jsonb
from pydantic import BaseModel, Field

from knock_on_atlas.api.auth import (
    RANK,
    Role,
    User,
    check_password,
    issue_token,
    read_token,
)
from knock_on_atlas.api.demo import LiveGate, gate_from_settings
from knock_on_atlas.api.jobs import JobQueue, queue_from_settings
from knock_on_atlas.config import get_settings
from knock_on_atlas.workflow.worker import Job


def get_conn() -> Iterator[psycopg.Connection]:
    """One connection per request: demo traffic, and Lambda reuses nothing anyway."""
    url = get_settings().database_url.get_secret_value()
    with psycopg.connect(url, autocommit=True) as conn:
        yield conn


Conn = Annotated[psycopg.Connection, Depends(get_conn)]


class Login(BaseModel):
    email: str
    password: str


class StartAnalysis(BaseModel):
    hub_cik: str = Field(pattern=r"^\d{10}$")
    accession: str = Field(pattern=r"^\d{10}-\d{2}-\d{6}$")


class Review(BaseModel):
    decision: Literal["approved", "rejected"]
    note: str = Field(default="", max_length=2000)


# Statuses that mean a run for the filing already exists or is under way.
ACTIVE = ("queued", "paused", "published")
COLUMNS = "id, status, input, result, created_at, completed_at"


def analysis(row: tuple[Any, ...]) -> dict[str, Any]:
    return dict(zip(COLUMNS.split(", "), row, strict=True))


def create_app(queue: JobQueue | None = None, gate: LiveGate | None = None) -> FastAPI:
    settings = get_settings()
    if settings.jwt_secret is None:
        raise RuntimeError("JWT_SECRET is required to run the API")
    secret = settings.jwt_secret.get_secret_value()
    jobs = queue if queue is not None else queue_from_settings(settings)
    live_gate = gate if gate is not None else gate_from_settings(settings)
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

    def live() -> None:
        # Starting or resuming a run spends model calls; reading never does.
        if not live_gate.is_live():
            raise HTTPException(503, "live runs are off")

    Analyst = Annotated[User, Depends(require("analyst"))]
    Reviewer = Annotated[User, Depends(require("reviewer"))]
    Anyone = Annotated[User, Depends(current_user)]

    @app.get("/health")
    def health() -> dict[str, Any]:
        return {"ok": True, "live": live_gate.is_live()}

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
    def me(user: Anyone) -> User:
        return user

    @app.post("/analyses", status_code=202, dependencies=[Depends(live)])
    def start_analysis(
        body: StartAnalysis, user: Analyst, conn: Conn, response: Response
    ) -> dict[str, Any]:
        with conn.transaction():
            # Two clicks on the same filing must not start two paid runs.
            conn.execute(
                "SELECT pg_advisory_xact_lock(hashtext(%s))", (body.accession,)
            )
            hub = conn.execute(
                "SELECT 1 FROM companies WHERE cik = %s AND is_hub", (body.hub_cik,)
            ).fetchone()
            if hub is None:
                raise HTTPException(422, "hub_cik is not a hub customer")
            existing = conn.execute(
                "SELECT id, status FROM analyses WHERE input->>'accession' = %s"
                " AND status = ANY(%s) ORDER BY created_at DESC LIMIT 1",
                (body.accession, list(ACTIVE)),
            ).fetchone()
            if existing is not None:
                response.status_code = 200
                return {"id": str(existing[0]), "status": existing[1]}
            analysis_id = str(uuid.uuid4())
            conn.execute(
                "INSERT INTO analyses (id, user_id, input, status)"
                " VALUES (%s, %s, %s, 'queued')",
                (analysis_id, user.id, Jsonb(body.model_dump())),
            )
            # Inside the transaction: if the queue refuses, no orphan row stays.
            jobs.send(Job(analysis_id=analysis_id, **body.model_dump()))
        return {"id": analysis_id, "status": "queued"}

    @app.get("/analyses")
    def list_analyses(user: Anyone, conn: Conn, mine: bool = False) -> list[Any]:
        rows = conn.execute(
            f"SELECT {COLUMNS} FROM analyses"
            " WHERE (NOT %s OR user_id = %s) ORDER BY created_at DESC LIMIT 50",
            (mine, user.id),
        ).fetchall()
        return [analysis(row) for row in rows]

    @app.get("/analyses/{analysis_id}")
    def get_analysis(
        analysis_id: uuid.UUID, user: Anyone, conn: Conn
    ) -> dict[str, Any]:
        row = conn.execute(
            f"SELECT {COLUMNS} FROM analyses WHERE id = %s", (analysis_id,)
        ).fetchone()
        if row is None:
            raise HTTPException(404, "no such analysis")
        return analysis(row)

    @app.post("/reviews/{analysis_id}", status_code=202, dependencies=[Depends(live)])
    def review(
        analysis_id: uuid.UUID, body: Review, user: Reviewer, conn: Conn
    ) -> dict[str, Any]:
        with conn.transaction():
            row = conn.execute(
                "SELECT status, input FROM analyses WHERE id = %s FOR UPDATE",
                (analysis_id,),
            ).fetchone()
            if row is None:
                raise HTTPException(404, "no such analysis")
            if row[0] != "paused":
                raise HTTPException(409, f"the analysis is {row[0]}, not paused")
            recorded = conn.execute(
                "INSERT INTO human_reviews"
                " (analysis_id, reviewer_id, decision, comment)"
                " VALUES (%s, %s, %s, %s) ON CONFLICT (analysis_id) DO NOTHING"
                " RETURNING id",
                (analysis_id, user.id, body.decision, body.note),
            ).fetchone()
            if recorded is None:
                raise HTTPException(409, "the analysis is already reviewed")
            # The worker resumes the paused run with this (state.ReviewDecision).
            decision = {
                "decision": body.decision,
                "reviewer": user.email,
                "note": body.note,
            }
            jobs.send(
                Job(
                    analysis_id=str(analysis_id),
                    hub_cik=row[1]["hub_cik"],
                    accession=row[1]["accession"],
                    decision=decision,
                )
            )
        return {"id": str(analysis_id), "decision": body.decision}

    return app
