"""Rate-limit and quota logic, tested against in-memory stand-ins for the two
repositories (the MongoDB-backed behaviour is covered in tests/integration)."""

from datetime import UTC, datetime, timedelta

import pytest
from starlette.requests import Request

from app.errors import QuotaExceeded, RateLimited, SessionExpired, Unauthorized
from app.ratelimit import (
    DAY,
    SESSION_CREATE_WINDOW,
    QuotaService,
    client_address,
    hash_client_ip,
    seconds_until,
    window_bounds,
)
from app.schemas.documents import SessionDoc
from app.security import SessionContext
from tests.conftest import build_test_settings

NOON = datetime(2026, 10, 7, 12, 20, 30, tzinfo=UTC)


class MemoryCounters:
    """Same contract as CounterRepository.increment, backed by a dict."""

    def __init__(self) -> None:
        self.counts: dict[str, int] = {}
        self.expiries: dict[str, datetime] = {}

    async def increment(self, key: str, expires_at: datetime, amount: int = 1) -> int:
        self.counts[key] = self.counts.get(key, 0) + amount
        self.expiries.setdefault(key, expires_at)
        return self.counts[key]


class MemorySessions:
    """Same contract as SessionRepository.use_quota / find_by_id."""

    def __init__(self, session: SessionDoc | None) -> None:
        self.session = session

    async def find_by_id(self, session_id: str) -> SessionDoc | None:
        return self.session

    async def use_quota(self, session_id: str, operation: str, limit: int, now: datetime) -> bool:
        session = self.session
        if session is None or session.revoked_at is not None or session.expires_at <= now:
            return False
        if session.quota.get(operation, 0) >= limit:
            return False
        session.quota[operation] = session.quota.get(operation, 0) + 1
        return True


class Clock:
    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


def make_session(**overrides: object) -> SessionDoc:
    values = {
        "session_id": "session-1",
        "token_hash": "hash",
        "owner_id": "owner-1",
        "created_at": NOON,
        "expires_at": NOON + timedelta(hours=24),
    }
    values.update(overrides)
    return SessionDoc(**values)


def make_service(
    session: SessionDoc | None = None, clock: Clock | None = None, **settings_overrides: object
) -> tuple[QuotaService, MemoryCounters, Clock]:
    counters = MemoryCounters()
    clock = clock or Clock(NOON)
    service = QuotaService(
        counters,  # type: ignore[arg-type]
        MemorySessions(session),  # type: ignore[arg-type]
        build_test_settings(**settings_overrides),
        clock=clock,
    )
    return service, counters, clock


CONTEXT = SessionContext(
    owner_id="owner-1", session_id="session-1", expires_at=NOON + timedelta(hours=24)
)

# ---- pure helpers ------------------------------------------------------------------


def test_hour_windows_start_on_the_hour() -> None:
    start, end = window_bounds(NOON, SESSION_CREATE_WINDOW)
    assert start == datetime(2026, 10, 7, 12, 0, 0, tzinfo=UTC)
    assert end == datetime(2026, 10, 7, 13, 0, 0, tzinfo=UTC)


def test_day_windows_start_at_midnight_utc() -> None:
    start, end = window_bounds(NOON, DAY)
    assert start == datetime(2026, 10, 7, tzinfo=UTC)
    assert end == datetime(2026, 10, 8, tzinfo=UTC)


def test_seconds_until_rounds_up_and_is_at_least_one() -> None:
    assert seconds_until(NOON, NOON + timedelta(seconds=90)) == 90
    assert seconds_until(NOON, NOON + timedelta(seconds=1.2)) == 2
    assert seconds_until(NOON, NOON) == 1
    assert seconds_until(NOON, NOON - timedelta(seconds=5)) == 1


def test_ip_hash_is_keyed_and_does_not_contain_the_address() -> None:
    first = hash_client_ip("203.0.113.7", b"key-one")
    assert first == hash_client_ip("203.0.113.7", b"key-one")
    assert first != hash_client_ip("203.0.113.8", b"key-one")
    assert first != hash_client_ip("203.0.113.7", b"key-two")
    assert "203.0.113.7" not in first
    assert len(first) == 32


def fake_request(peer: str | None, forwarded_for: str | None = None) -> Request:
    headers = [(b"x-forwarded-for", forwarded_for.encode())] if forwarded_for else []
    client = (peer, 50000) if peer else None
    return Request({"type": "http", "headers": headers, "client": client})


def test_forwarded_header_is_ignored_unless_proxies_are_trusted() -> None:
    request = fake_request("10.0.0.5", "198.51.100.9, 10.0.0.1")
    untrusted = build_test_settings(trust_proxy_headers=False)
    trusted = build_test_settings(trust_proxy_headers=True)
    assert client_address(request, untrusted).ip == "10.0.0.5"
    # The entry the trusted proxy appended (the last one), not the leftmost
    # entry, which arrives with the request and can be forged (SEC-3).
    assert client_address(request, trusted).ip == "10.0.0.1"


def test_client_ip_falls_back_when_information_is_missing() -> None:
    trusted = build_test_settings(trust_proxy_headers=True)
    untrusted = build_test_settings(trust_proxy_headers=False)
    assert client_address(fake_request("10.0.0.5"), trusted).ip == "10.0.0.5"
    assert client_address(fake_request(None), untrusted).ip == "unknown"


# ---- session creation limit --------------------------------------------------------


async def test_session_creation_is_allowed_up_to_the_limit_then_rate_limited() -> None:
    service, counters, _ = make_service(session_create_limit_per_hour=3)
    for _ in range(3):
        await service.check_session_creation("203.0.113.7")

    with pytest.raises(RateLimited) as error:
        await service.check_session_creation("203.0.113.7")

    # 12:20:30 -> the window ends at 13:00:00, i.e. in 2370 seconds.
    assert error.value.headers == {"Retry-After": "2370"}
    assert error.value.status_code == 429
    assert error.value.code == "rate_limited"
    # Only the hashed IP is used as the counter key.
    assert all("203.0.113.7" not in key for key in counters.counts)


async def test_session_creation_limit_is_per_ip() -> None:
    service, _, _ = make_service(session_create_limit_per_hour=1)
    await service.check_session_creation("203.0.113.7")
    await service.check_session_creation("203.0.113.8")
    with pytest.raises(RateLimited):
        await service.check_session_creation("203.0.113.7")


async def test_session_creation_limit_resets_in_the_next_window() -> None:
    service, counters, clock = make_service(session_create_limit_per_hour=1)
    await service.check_session_creation("203.0.113.7")
    with pytest.raises(RateLimited):
        await service.check_session_creation("203.0.113.7")

    clock.now = NOON + timedelta(hours=1)
    await service.check_session_creation("203.0.113.7")

    assert len(counters.counts) == 2
    assert sorted(counters.expiries.values()) == [
        datetime(2026, 10, 7, 13, tzinfo=UTC),
        datetime(2026, 10, 7, 14, tzinfo=UTC),
    ]


# ---- per-session quota -------------------------------------------------------------


async def test_operation_quota_allows_the_limit_then_raises() -> None:
    session = make_session()
    service, _, _ = make_service(session, quota_generation=2)
    await service.charge_operation(CONTEXT, "generation")
    await service.charge_operation(CONTEXT, "generation")

    with pytest.raises(QuotaExceeded) as error:
        await service.charge_operation(CONTEXT, "generation")

    assert session.quota["generation"] == 2
    assert error.value.status_code == 429
    assert error.value.code == "quota_exceeded"
    assert error.value.details == {"operation": "generation", "limit": 2}
    assert "2" in error.value.message


async def test_operation_quotas_are_independent() -> None:
    session = make_session()
    service, _, _ = make_service(session, quota_ingest=1, quota_job_analysis=1)
    await service.charge_operation(CONTEXT, "ingest")
    await service.charge_operation(CONTEXT, "job_analysis")
    with pytest.raises(QuotaExceeded):
        await service.charge_operation(CONTEXT, "ingest")


async def test_revoked_session_cannot_be_charged() -> None:
    service, _, _ = make_service(make_session(revoked_at=NOON))
    with pytest.raises(Unauthorized):
        await service.charge_operation(CONTEXT, "ingest")


async def test_missing_session_cannot_be_charged() -> None:
    service, _, _ = make_service(None)
    with pytest.raises(Unauthorized):
        await service.charge_operation(CONTEXT, "ingest")


async def test_expired_session_cannot_be_charged() -> None:
    service, _, _ = make_service(make_session(expires_at=NOON - timedelta(seconds=1)))
    with pytest.raises(SessionExpired):
        await service.charge_operation(CONTEXT, "ingest")


# ---- global daily cap --------------------------------------------------------------


async def test_global_ai_call_cap_counts_calls_and_blocks_beyond_the_limit() -> None:
    service, counters, _ = make_service(global_daily_ai_call_limit=5)
    await service.charge_ai_calls(3)
    await service.charge_ai_calls(2)

    with pytest.raises(QuotaExceeded) as error:
        await service.charge_ai_calls()

    assert error.value.details == {"scope": "global_daily"}
    assert counters.counts == {"ai_calls:2026-10-07": 6}
    assert counters.expiries["ai_calls:2026-10-07"] == datetime(2026, 10, 8, tzinfo=UTC)


async def test_global_ai_call_cap_resets_the_next_day() -> None:
    service, counters, clock = make_service(global_daily_ai_call_limit=1)
    await service.charge_ai_calls()
    with pytest.raises(QuotaExceeded):
        await service.charge_ai_calls()

    clock.now = NOON + timedelta(days=1)
    await service.charge_ai_calls()

    assert counters.counts["ai_calls:2026-10-08"] == 1
