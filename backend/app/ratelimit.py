"""Persistent rate limits and usage quotas (cost controls for a public deployment).

Three independent limits, all stored in MongoDB so they survive restarts:

1. Session creation: fixed one-hour window per client IP (``rate_limited``).
2. Per-session quota for each expensive operation, counted on the session
   document (``quota_exceeded``).
3. A global cap on AI provider calls per UTC day (``quota_exceeded``).

Quota is charged on attempt, before the provider is called, so a request that
later fails still counts. That is deliberate: failed provider calls cost money
too, and it stops a client from retrying a failing request without bound.

How feature code uses it (``quotas`` comes from the QuotasDep dependency):

    await quotas.charge_operation(session, "generation")  # per-session quota
    await quotas.charge_ai_calls(2)                       # before 2 provider calls
"""

import hashlib
import hmac
import math
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from fastapi import Request

from app.config import Settings
from app.errors import QuotaExceeded, RateLimited, SessionExpired, Unauthorized
from app.repositories.counters import CounterRepository
from app.repositories.sessions import SessionRepository
from app.schemas.common import utc_now
from app.schemas.documents import QuotaOperation
from app.security import SessionContext

SESSION_CREATE_WINDOW = timedelta(hours=1)
DAY = timedelta(days=1)

_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


# Headers in which a platform reports the client address in a single value
# that it overwrites itself: Cloudflare (in front of Render), then Akamai and
# Cloudflare Enterprise.
PLATFORM_IP_HEADERS = ("cf-connecting-ip", "true-client-ip")
FORWARDED_FOR_HEADER = "x-forwarded-for"
SOCKET_SOURCE = "socket"


@dataclass(frozen=True)
class ClientAddress:
    """The caller's IP address and where it was read from. ``source`` is a
    header name or "socket": safe to log, unlike the address itself."""

    ip: str
    source: str


def _forwarded_hop(request: Request, trusted_hops: int) -> str | None:
    """The address the outermost trusted proxy saw, taken from X-Forwarded-For.

    Every proxy APPENDS the address it received the request from, so only the
    last ``trusted_hops`` entries were written by proxies we trust. Everything
    to their left arrived with the request and can be forged by the client:
    the leftmost entry is never used. None when the header is shorter than
    the number of trusted proxies.
    """
    values = request.headers.getlist(FORWARDED_FOR_HEADER)
    hops = [hop.strip() for value in values for hop in value.split(",") if hop.strip()]
    return hops[-trusted_hops] if len(hops) >= trusted_hops else None


def client_address(request: Request, settings: Settings) -> ClientAddress:
    """The caller's IP address for the session rate limit.

    Without TRUST_PROXY_HEADERS the socket peer is used and every header is
    ignored, because with no trusted proxy in front a client could send any
    header it likes. Behind a platform proxy the socket peer is the proxy, so
    the address is read from what the platform itself sets, in this order:

    1. the header named by CLIENT_IP_HEADER, if configured;
    2. a single-value platform header (PLATFORM_IP_HEADERS);
    3. X-Forwarded-For, counted from the right (see _forwarded_hop);
    4. the socket peer.
    """
    if settings.trust_proxy_headers:
        configured = [settings.client_ip_header] if settings.client_ip_header else []
        for name in (*configured, *PLATFORM_IP_HEADERS):
            value = request.headers.get(name, "").strip()
            if value:
                return ClientAddress(value, name)
        hop = _forwarded_hop(request, settings.trusted_proxy_hops)
        if hop:
            return ClientAddress(hop, FORWARDED_FOR_HEADER)
    return ClientAddress(request.client.host if request.client else "unknown", SOCKET_SOURCE)


def hash_client_ip(ip: str, key: bytes) -> str:
    """Keyed hash (HMAC-SHA256) of an IP address. Counters are stored under
    this value, so raw IP addresses never reach the database."""
    return hmac.new(key, ip.encode(), hashlib.sha256).hexdigest()[:32]


def window_bounds(now: datetime, window: timedelta) -> tuple[datetime, datetime]:
    """Start and end of the fixed window containing ``now``.

    Windows are aligned to the Unix epoch, so every process computes the same
    boundaries (hour windows start on the hour, day windows at 00:00 UTC).
    """
    elapsed_windows = (now - _EPOCH) // window
    start = _EPOCH + elapsed_windows * window
    return start, start + window


def seconds_until(now: datetime, moment: datetime) -> int:
    """Whole seconds from ``now`` until ``moment``, rounded up, at least 1."""
    return max(1, math.ceil((moment - now).total_seconds()))


class QuotaService:
    def __init__(
        self,
        counters: CounterRepository,
        sessions: SessionRepository,
        settings: Settings,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self._counters = counters
        self._sessions = sessions
        self._settings = settings
        self._clock = clock

    async def check_session_creation(self, ip: str) -> None:
        """Count one session creation for this IP; raise RateLimited (429 with
        Retry-After) once the hourly limit is exceeded."""
        now = self._clock()
        window_start, window_end = window_bounds(now, SESSION_CREATE_WINDOW)
        ip_hash = hash_client_ip(ip, self._settings.ip_hash_key)
        key = f"session_create:{ip_hash}:{int(window_start.timestamp())}"
        count = await self._counters.increment(key, expires_at=window_end)
        if count > self._settings.session_create_limit_per_hour:
            raise RateLimited(
                seconds_until(now, window_end),
                "Too many sessions were started from this network. Try again later.",
            )

    async def charge_operation(self, session: SessionContext, operation: QuotaOperation) -> None:
        """Count one attempt of ``operation`` against the session's quota.

        Raises QuotaExceeded (429) when the quota is used up, or the usual 401
        errors if the session was revoked or expired in the meantime.
        """
        limit: int = getattr(self._settings, f"quota_{operation}")
        now = self._clock()
        if await self._sessions.use_quota(session.session_id, operation, limit, now):
            return
        stored = await self._sessions.find_by_id(session.session_id)
        if stored is None or stored.revoked_at is not None:
            raise Unauthorized()
        if stored.expires_at <= now:
            raise SessionExpired()
        raise QuotaExceeded(
            f"This session has used all {limit} of its {operation.replace('_', ' ')} requests. "
            "Start a new session to continue.",
            details={"operation": operation, "limit": limit},
        )

    async def charge_ai_calls(self, count: int = 1) -> None:
        """Count ``count`` provider calls against the global daily cap; raise
        QuotaExceeded (429) once the cap is passed. Call before the provider."""
        now = self._clock()
        day_start, day_end = window_bounds(now, DAY)
        key = f"ai_calls:{day_start:%Y-%m-%d}"
        total = await self._counters.increment(key, expires_at=day_end, amount=count)
        if total > self._settings.global_daily_ai_call_limit:
            raise QuotaExceeded(
                "This service has reached its daily AI usage limit. Try again tomorrow.",
                details={"scope": "global_daily"},
            )
