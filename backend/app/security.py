"""Anonymous bearer-token sessions.

A visitor gets a random token from POST /api/sessions. The server stores only
the SHA-256 hash of it, so a leaked database does not reveal usable tokens.
SHA-256 without salt or stretching is appropriate here because the token is
256 bits of randomness, not a human-chosen password: it cannot be guessed or
looked up in a table.
"""

import hashlib
import re
import secrets
from dataclasses import dataclass
from datetime import datetime
from typing import Annotated

from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.db import Database, get_db
from app.errors import SessionExpired, Unauthorized
from app.repositories.sessions import SessionRepository
from app.schemas.common import utc_now

TOKEN_BYTES = 32
# token_urlsafe(32) yields 43 characters from this URL-safe alphabet.
_TOKEN_PATTERN = re.compile(r"[A-Za-z0-9_-]{32,128}")

# auto_error=False: a missing or non-Bearer header yields None instead of
# FastAPI's default error, so every failure goes through our error envelope.
# Declaring the scheme also adds the "Authorize" button to /docs.
_bearer_scheme = HTTPBearer(auto_error=False, description="Token from POST /api/sessions")


@dataclass(frozen=True)
class SessionContext:
    """The authenticated caller. ``owner_id`` scopes every database query;
    ``expires_at`` is copied onto every document the caller creates."""

    owner_id: str
    session_id: str
    expires_at: datetime


def generate_token() -> str:
    """A new bearer token with 256 bits of entropy from the OS random source."""
    return secrets.token_urlsafe(TOKEN_BYTES)


def hash_token(token: str) -> str:
    """Hex SHA-256 of the token: the only form in which a token is stored."""
    return hashlib.sha256(token.encode()).hexdigest()


def is_well_formed_token(token: str) -> bool:
    """True if ``token`` could have been issued by generate_token(). Lets
    obviously malformed values be rejected without a database lookup."""
    return _TOKEN_PATTERN.fullmatch(token) is not None


async def require_session(
    db: Annotated[Database, Depends(get_db)],
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer_scheme)],
) -> SessionContext:
    """FastAPI dependency for every protected route.

    Missing, malformed, unknown and revoked tokens all get the same 401
    ``unauthorized`` answer. An expired session gets 401 ``session_expired``;
    expiry is checked here on every request because MongoDB's TTL cleanup can
    lag behind ``expires_at``.
    """
    if credentials is None or not is_well_formed_token(credentials.credentials):
        raise Unauthorized()
    session = await SessionRepository(db).find_by_token_hash(hash_token(credentials.credentials))
    if session is None or session.revoked_at is not None:
        raise Unauthorized()
    if session.expires_at <= utc_now():
        raise SessionExpired()
    return SessionContext(
        owner_id=session.owner_id, session_id=session.session_id, expires_at=session.expires_at
    )
