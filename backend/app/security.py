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
from app.schemas.documents import SessionDoc

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


async def _stored_session(
    db: Database, credentials: HTTPAuthorizationCredentials | None
) -> SessionDoc:
    """The stored session of the bearer token; 401 ``unauthorized`` when the
    token is missing, malformed or unknown."""
    if credentials is None or not is_well_formed_token(credentials.credentials):
        raise Unauthorized()
    session = await SessionRepository(db).find_by_token_hash(hash_token(credentials.credentials))
    if session is None:
        raise Unauthorized()
    return session


def _context_of(session: SessionDoc) -> SessionContext:
    return SessionContext(
        owner_id=session.owner_id, session_id=session.session_id, expires_at=session.expires_at
    )


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
    session = await _stored_session(db, credentials)
    if session.revoked_at is not None:
        raise Unauthorized()
    if session.expires_at <= utc_now():
        raise SessionExpired()
    return _context_of(session)


async def require_session_to_clear(
    db: Annotated[Database, Depends(get_db)],
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer_scheme)],
) -> SessionContext:
    """FastAPI dependency for DELETE /api/session only.

    Like require_session, except that a revoked session is accepted until it
    expires. "Clear my data" revokes the session before it deletes anything;
    if a delete then fails, the visitor must be able to send the request
    again, and by then the token is revoked. Nothing is gained by holding a
    revoked token: all it can do is delete that session's own data once more.
    """
    session = await _stored_session(db, credentials)
    if session.expires_at <= utc_now():
        raise SessionExpired()
    return _context_of(session)
