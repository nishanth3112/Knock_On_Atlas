"""API users: Argon2 password hashes and 15-minute JWTs carrying a role (spec §16)."""

import time
from typing import Literal

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from pydantic import BaseModel

Role = Literal["analyst", "reviewer", "admin"]
RANK: dict[Role, int] = {"analyst": 1, "reviewer": 2, "admin": 3}
TOKEN_TTL_SECONDS = 15 * 60
# MCP service tokens are HS256 too, with audience koa-mcp; neither side accepts
# the other's tokens.
AUDIENCE = "koa-api"

_hasher = PasswordHasher()
# Checked when the email is unknown, so a miss takes as long as a wrong password.
_DUMMY_HASH = _hasher.hash("no such user")


class User(BaseModel):
    id: int
    email: str
    role: Role


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def check_password(stored_hash: str | None, password: str) -> bool:
    try:
        _hasher.verify(stored_hash or _DUMMY_HASH, password)
    except (VerificationError, InvalidHashError):
        return False
    return stored_hash is not None


def issue_token(user: User, secret: str) -> str:
    now = int(time.time())
    claims = {
        "sub": str(user.id),
        "email": user.email,
        "role": user.role,
        "aud": AUDIENCE,
        "iat": now,
        "exp": now + TOKEN_TTL_SECONDS,
    }
    return jwt.encode(claims, secret, algorithm="HS256")


def read_token(token: str, secret: str) -> User:
    """The token's user; raises jwt.InvalidTokenError if bad, expired or foreign."""
    claims = jwt.decode(
        token,
        secret,
        algorithms=["HS256"],
        audience=AUDIENCE,
        options={"require": ["sub", "exp", "aud"]},
    )
    return User(id=int(claims["sub"]), email=claims["email"], role=claims["role"])
