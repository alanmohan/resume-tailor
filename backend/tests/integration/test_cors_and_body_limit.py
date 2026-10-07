"""CORS with exact origins, and the request body size limit."""

from collections.abc import AsyncIterator, Awaitable, Callable

import httpx
import pytest
from fastapi import FastAPI, Request

from app.config import Settings
from tests.conftest import TEST_ORIGIN, client_for
from tests.helpers import assert_error

MakeApp = Callable[[Settings], Awaitable[FastAPI]]
MakeSettings = Callable[..., Settings]

OTHER_ORIGIN = "https://evil.example.com"
PREFLIGHT = {
    "Access-Control-Request-Method": "POST",
    "Access-Control-Request-Headers": "authorization,content-type,idempotency-key",
}

# ---- CORS --------------------------------------------------------------------------


async def test_allowed_origin_receives_exact_cors_headers(client: httpx.AsyncClient) -> None:
    response = await client.get("/healthz", headers={"Origin": TEST_ORIGIN})

    assert response.headers["access-control-allow-origin"] == TEST_ORIGIN
    exposed = response.headers["access-control-expose-headers"].lower()
    assert "x-request-id" in exposed and "retry-after" in exposed
    # Tokens travel in the Authorization header; cookies are never allowed.
    assert "access-control-allow-credentials" not in response.headers


async def test_disallowed_origin_receives_no_cors_headers(client: httpx.AsyncClient) -> None:
    response = await client.get("/healthz", headers={"Origin": OTHER_ORIGIN})
    assert response.status_code == 200
    assert "access-control-allow-origin" not in response.headers


async def test_similar_looking_origins_are_not_allowed(client: httpx.AsyncClient) -> None:
    for origin in (TEST_ORIGIN + ".evil.example.com", TEST_ORIGIN.replace("http://", "https://")):
        response = await client.get("/healthz", headers={"Origin": origin})
        assert "access-control-allow-origin" not in response.headers


async def test_preflight_from_allowed_origin_permits_the_api_headers(
    client: httpx.AsyncClient,
) -> None:
    response = await client.options(
        "/api/generations", headers={"Origin": TEST_ORIGIN, **PREFLIGHT}
    )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == TEST_ORIGIN
    allowed_headers = response.headers["access-control-allow-headers"].lower()
    for header in ("authorization", "content-type", "idempotency-key"):
        assert header in allowed_headers
    allowed_methods = response.headers["access-control-allow-methods"]
    for method in ("GET", "POST", "PATCH", "DELETE"):
        assert method in allowed_methods


async def test_preflight_from_disallowed_origin_is_refused(client: httpx.AsyncClient) -> None:
    response = await client.options("/api/sessions", headers={"Origin": OTHER_ORIGIN, **PREFLIGHT})
    assert response.status_code == 400
    assert "access-control-allow-origin" not in response.headers


async def test_preflight_with_an_unlisted_header_is_refused(client: httpx.AsyncClient) -> None:
    headers = {
        "Origin": TEST_ORIGIN,
        "Access-Control-Request-Method": "POST",
        "Access-Control-Request-Headers": "x-owner-id",
    }
    assert (await client.options("/api/sessions", headers=headers)).status_code == 400


async def test_error_responses_also_carry_cors_headers(client: httpx.AsyncClient) -> None:
    response = await client.get("/api/session", headers={"Origin": TEST_ORIGIN})
    assert_error(response, 401, "unauthorized")
    assert response.headers["access-control-allow-origin"] == TEST_ORIGIN


async def test_several_configured_origins_are_each_allowed(
    make_app: MakeApp, make_settings: MakeSettings
) -> None:
    origins = ["https://app.example.com", "http://localhost:5173"]
    application = await make_app(make_settings(cors_origins=origins))
    async with client_for(application) as client:
        for origin in origins:
            response = await client.get("/healthz", headers={"Origin": origin})
            assert response.headers["access-control-allow-origin"] == origin
        refused = await client.get("/healthz", headers={"Origin": TEST_ORIGIN})
        assert "access-control-allow-origin" not in refused.headers


# ---- body size limit ---------------------------------------------------------------

LIMIT = 2_000


@pytest.fixture
async def small_limit_client(
    make_app: MakeApp, make_settings: MakeSettings
) -> AsyncIterator[httpx.AsyncClient]:
    application = await make_app(make_settings(max_request_bytes=LIMIT))

    @application.post("/api/_probe/echo")
    async def echo(request: Request) -> dict[str, int]:
        return {"received": len(await request.body())}

    @application.post("/api/_probe/json")
    async def echo_json(body: dict[str, str]) -> dict[str, int]:
        return {"received": len(body["text"])}

    async with client_for(application) as client:
        yield client


async def chunks(total: int, size: int = 500) -> AsyncIterator[bytes]:
    for _ in range(total // size):
        yield b"x" * size


async def test_body_within_the_limit_is_accepted(small_limit_client: httpx.AsyncClient) -> None:
    response = await small_limit_client.post("/api/_probe/echo", content=b"x" * LIMIT)
    assert response.status_code == 200
    assert response.json() == {"received": LIMIT}


async def test_declared_oversized_body_is_rejected_before_it_is_read(
    small_limit_client: httpx.AsyncClient,
) -> None:
    response = await small_limit_client.post(
        "/api/_probe/echo", content=b"x" * (LIMIT + 1), headers={"Origin": TEST_ORIGIN}
    )
    error = assert_error(response, 413, "input_too_large")
    assert "2,000 byte limit" in error["message"]
    assert response.headers["access-control-allow-origin"] == TEST_ORIGIN


async def test_streamed_oversized_body_without_content_length_is_rejected(
    small_limit_client: httpx.AsyncClient,
) -> None:
    response = await small_limit_client.post("/api/_probe/echo", content=chunks(LIMIT * 3))
    assert "content-length" not in response.request.headers
    assert_error(response, 413, "input_too_large")


async def test_streamed_oversized_json_body_is_rejected_with_the_same_error(
    small_limit_client: httpx.AsyncClient,
) -> None:
    """FastAPI reads JSON bodies itself; the limit must still surface as 413
    rather than as a generic "error parsing the body"."""

    async def json_chunks() -> AsyncIterator[bytes]:
        yield b'{"text": "'
        async for chunk in chunks(LIMIT * 2):
            yield chunk
        yield b'"}'

    response = await small_limit_client.post(
        "/api/_probe/json", content=json_chunks(), headers={"Content-Type": "application/json"}
    )
    error = assert_error(response, 413, "input_too_large")
    assert "2,000 byte limit" in error["message"]


async def test_streamed_body_within_the_limit_is_accepted(
    small_limit_client: httpx.AsyncClient,
) -> None:
    response = await small_limit_client.post("/api/_probe/echo", content=chunks(LIMIT))
    assert response.status_code == 200
    assert response.json() == {"received": LIMIT}


async def test_default_limit_is_the_configured_400_kilobytes(client: httpx.AsyncClient) -> None:
    response = await client.post("/api/sessions", content=b"x" * 400_001)
    assert_error(response, 413, "input_too_large")
