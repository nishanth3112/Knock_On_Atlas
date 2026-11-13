"""The HTTP API (spec §16): login, analyses, reviews, health."""

import uuid
from collections.abc import Callable, Iterator
from dataclasses import asdict
from datetime import date
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
from knock_on_atlas.graph_store.review import list_edges, set_status
from knock_on_atlas.graph_store.traversal import exposure
from knock_on_atlas.workflow.analyses import Status
from knock_on_atlas.workflow.worker import Job


def get_conn() -> Iterator[psycopg.Connection]:
    """One connection per request: demo traffic, and Lambda reuses nothing anyway."""
    url = get_settings().database_url.get_secret_value()
    with psycopg.connect(url, autocommit=True) as conn:
        yield conn


Conn = Annotated[psycopg.Connection, Depends(get_conn)]


class Login(BaseModel):
    # Bounded so a huge body is refused before the 64 MiB Argon2 check runs.
    email: str = Field(max_length=256)
    password: str = Field(max_length=256)


class StartAnalysis(BaseModel):
    hub_cik: str = Field(pattern=r"^\d{10}$")
    accession: str = Field(pattern=r"^\d{10}-\d{2}-\d{6}$")


class EdgeDecision(BaseModel):
    decision: Literal["approved", "rejected"]


class Review(BaseModel):
    decision: Literal["approved", "rejected"]
    note: str = Field(default="", max_length=2000)


# A filing with a run in any of these states is not run again: each run can
# spend paid Jev calls. Only a rejected run may be retried.
ACTIVE = ("queued", "paused", "published", "ended")
RUNS_PER_DAY = 5  # per user; one leaked login must not drain the Jev budget
MIN_SECRET_LENGTH = 32  # a short HS256 secret can be brute-forced from any token
COLUMNS = "id, status, input, result, created_at, completed_at"
# Each analysis with its hub's name and its 8-K, for the UI.
SELECT_ANALYSES = (
    f"SELECT {COLUMNS}, c.name, e.accepted_at, e.items, e.url FROM analyses"
    " LEFT JOIN companies c ON c.cik = input->>'hub_cik'"
    " LEFT JOIN events e ON e.accession = input->>'accession'"
)


def analysis(row: tuple[Any, ...]) -> dict[str, Any]:
    found = dict(zip(COLUMNS.split(", "), row[:6], strict=True))
    found["hub_name"] = row[6]
    event = dict(zip(("accepted_at", "items", "url"), row[7:], strict=True))
    found["event"] = event if row[9] is not None else None
    return found


def create_app(queue: JobQueue | None = None, gate: LiveGate | None = None) -> FastAPI:
    settings = get_settings()
    if settings.jwt_secret is None:
        raise RuntimeError("JWT_SECRET is required to run the API")
    secret = settings.jwt_secret.get_secret_value()
    if len(secret) < MIN_SECRET_LENGTH:
        raise RuntimeError(
            f"JWT_SECRET must be at least {MIN_SECRET_LENGTH} characters"
        )
    jobs = queue if queue is not None else queue_from_settings(settings)
    live_gate = gate if gate is not None else gate_from_settings(settings)
    # No public schema: the UI is the only client.
    app = FastAPI(
        title="Knock-On Atlas", docs_url=None, redoc_url=None, openapi_url=None
    )

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
            # Two clicks on the same filing must not start two paid runs, and one
            # user's parallel requests must not slip past the daily cap. Locks are
            # taken in one order (user, then filing), so they can't deadlock.
            conn.execute("SELECT pg_advisory_xact_lock(1, %s)", (user.id,))
            conn.execute(
                "SELECT pg_advisory_xact_lock(2, hashtext(%s))", (body.accession,)
            )
            hub = conn.execute(
                "SELECT 1 FROM companies WHERE cik = %s AND is_hub", (body.hub_cik,)
            ).fetchone()
            if hub is None:
                raise HTTPException(422, "hub_cik is not a hub customer")
            # A queued row an hour old lost its job (API restart, demo switched
            # off), since runs start within minutes: it no longer blocks a new run.
            existing = conn.execute(
                "SELECT id, status FROM analyses WHERE input->>'accession' = %s"
                " AND status = ANY(%s) AND NOT (status = 'queued'"
                " AND created_at < now() - interval '1 hour')"
                " ORDER BY created_at DESC LIMIT 1",
                (body.accession, list(ACTIVE)),
            ).fetchone()
            if existing is not None:
                response.status_code = 200
                return {"id": str(existing[0]), "status": existing[1]}
            started = conn.execute(
                "SELECT count(*) FROM analyses"
                " WHERE user_id = %s AND created_at > now() - interval '1 day'",
                (user.id,),
            ).fetchone()
            if started is not None and started[0] >= RUNS_PER_DAY:
                raise HTTPException(429, f"at most {RUNS_PER_DAY} new analyses a day")
            analysis_id = str(uuid.uuid4())
            conn.execute(
                "INSERT INTO analyses (id, user_id, input, status)"
                " VALUES (%s, %s, %s, 'queued')",
                (analysis_id, user.id, Jsonb(body.model_dump())),
            )
            # Inside the transaction: if the queue refuses, no orphan row stays.
            jobs.send(Job(analysis_id=analysis_id, **body.model_dump()))
        return {"id": analysis_id, "status": "queued"}

    @app.get("/events")
    def list_events(user: Anyone, conn: Conn) -> list[dict[str, Any]]:
        # Each filing with its hub's name and its latest run, for the inbox.
        rows = conn.execute(
            "SELECT e.accession, e.cik, c.name, e.accepted_at, e.items, e.url,"
            " a.id, a.status, a.result->'triage'"
            " FROM events e LEFT JOIN companies c ON c.cik = e.cik"
            " LEFT JOIN LATERAL (SELECT id, status, result FROM analyses"
            "  WHERE input->>'accession' = e.accession"
            "  ORDER BY created_at DESC LIMIT 1) a ON true"
            " ORDER BY e.accepted_at DESC LIMIT 100"
        ).fetchall()
        keys = "accession hub_cik hub_name accepted_at items url"
        events = []
        for row in rows:
            event = dict(zip(keys.split(), row[:6], strict=True))
            run = {"id": str(row[6]), "status": row[7], "triage": row[8]}
            events.append({**event, "run": run if row[6] else None})
        return events

    @app.get("/analyses")
    def list_analyses(
        user: Anyone, conn: Conn, mine: bool = False, status: Status | None = None
    ) -> list[Any]:
        rows = conn.execute(
            f"{SELECT_ANALYSES} WHERE (NOT %s OR user_id = %s)"
            " AND (%s::text IS NULL OR status = %s)"
            " ORDER BY created_at DESC LIMIT 50",
            (mine, user.id, status, status),
        ).fetchall()
        return [analysis(row) for row in rows]

    @app.get("/analyses/{analysis_id}")
    def get_analysis(
        analysis_id: uuid.UUID, user: Anyone, conn: Conn
    ) -> dict[str, Any]:
        row = conn.execute(
            f"{SELECT_ANALYSES} WHERE id = %s", (analysis_id,)
        ).fetchone()
        if row is None:
            raise HTTPException(404, "no such analysis")
        return analysis(row)

    @app.get("/companies/{cik}/exposure")
    def company_exposure(
        cik: str, user: Anyone, conn: Conn, as_of: date | None = None
    ) -> dict[str, Any]:
        """Who sells to the hub, and to them, through reviewed edges valid on as_of."""
        hub = conn.execute(
            "SELECT cik, name, tickers FROM companies WHERE cik = %s AND is_hub",
            (cik,),
        ).fetchone()
        if hub is None:
            raise HTTPException(404, "not a hub customer")
        day = as_of or date.today()
        paths = exposure(conn, cik, day)
        # The last edge of each path links the supplier to the company it sells to.
        rows = conn.execute(
            "SELECT e.id, c.name, c.tickers, e.pct::float8, e.member_label,"
            " e.source_accession, e.confidence, e.period_end, e.valid_from,"
            " e.valid_to, e.reviewed_at, e.extraction_method"
            " FROM entity_edges e JOIN companies c ON c.cik = e.src_cik"
            " WHERE e.id = ANY(%s)",
            ([p.edge_ids[-1] for p in paths],),
        ).fetchall()
        keys = (
            "id",
            "pct",
            "member_label",
            "source_accession",
            "confidence",
            "period_end",
            "valid_from",
            "valid_to",
            "reviewed_at",
            "extraction_method",
        )
        edges = {
            r[0]: (r[1], r[2], dict(zip(keys, r[:1] + r[3:], strict=True)))
            for r in rows
        }
        suppliers = []
        for p in paths:
            name, tickers, edge = edges[p.edge_ids[-1]]
            suppliers.append(
                {**asdict(p), "name": name, "tickers": tickers, "edge": edge}
            )
        return {
            "hub": {"cik": hub[0], "name": hub[1], "tickers": hub[2]},
            "as_of": day,
            "suppliers": suppliers,
        }

    @app.get("/edges/review")
    def edges_to_review(user: Reviewer, conn: Conn) -> list[dict[str, Any]]:
        return [
            {**asdict(edge), "filing_url": edge.filing_url}
            for edge in list_edges(conn, "pending")
        ]

    @app.post("/edges/{edge_id}/decision")
    def decide_edge(
        edge_id: int, body: EdgeDecision, user: Reviewer, conn: Conn
    ) -> dict[str, Any]:
        # A reviewer's write, the only kind the API makes to entity_edges.
        with conn.transaction():
            row = conn.execute(
                "SELECT status FROM entity_edges WHERE id = %s FOR UPDATE", (edge_id,)
            ).fetchone()
            if row is None:
                raise HTTPException(404, "no such relationship")
            if row[0] != "pending":
                raise HTTPException(409, f"the relationship is already {row[0]}")
            set_status(conn, [edge_id], body.decision, reviewer=user.email)
        return {"id": edge_id, "status": body.decision}

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
