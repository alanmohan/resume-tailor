"""SEC-3 through the API: a forged first X-Forwarded-For hop no longer buys a
fresh session allowance, and the log says where the address was read from
without containing it."""

import logging
from collections.abc import Awaitable, Callable

import pytest
from fastapi import FastAPI

from app.config import Settings
from tests.conftest import client_for

MakeApp = Callable[[Settings], Awaitable[FastAPI]]
MakeSettings = Callable[..., Settings]

REAL_CLIENT = "203.0.113.7"


def sources_logged(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [
        record.fields["client_ip_source"]
        for record in caplog.records
        if record.getMessage() == "session_create"
    ]


async def test_rotating_a_forged_first_hop_still_reaches_the_limit(
    make_app: MakeApp, make_settings: MakeSettings, caplog: pytest.LogCaptureFixture
) -> None:
    application = await make_app(
        make_settings(session_create_limit_per_hour=3, trust_proxy_headers=True)
    )
    caplog.set_level(logging.INFO)
    async with client_for(application) as client:
        statuses = [
            (
                await client.post(
                    "/api/sessions", headers={"X-Forwarded-For": f"10.9.8.{n}, {REAL_CLIENT}"}
                )
            ).status_code
            for n in range(5)
        ]
        # Another visitor behind the same proxy is not affected by those requests.
        other = await client.post(
            "/api/sessions", headers={"X-Forwarded-For": f"{REAL_CLIENT}, 198.51.100.20"}
        )

    assert statuses == [201, 201, 201, 429, 429]
    assert other.status_code == 201
    assert sources_logged(caplog) == ["x-forwarded-for"] * 6
    assert REAL_CLIENT not in caplog.text and "10.9.8." not in caplog.text


async def test_the_platform_header_decides_when_it_is_present(
    make_app: MakeApp, make_settings: MakeSettings, caplog: pytest.LogCaptureFixture
) -> None:
    application = await make_app(
        make_settings(
            session_create_limit_per_hour=1,
            trust_proxy_headers=True,
            client_ip_header="cf-connecting-ip",
        )
    )
    caplog.set_level(logging.INFO)
    async with client_for(application) as client:
        first = await client.post(
            "/api/sessions",
            headers={"CF-Connecting-IP": REAL_CLIENT, "X-Forwarded-For": "198.51.100.1"},
        )
        again = await client.post(
            "/api/sessions",
            headers={"CF-Connecting-IP": REAL_CLIENT, "X-Forwarded-For": "198.51.100.2"},
        )

    assert (first.status_code, again.status_code) == (201, 429)
    assert sources_logged(caplog) == ["cf-connecting-ip", "cf-connecting-ip"]
    assert REAL_CLIENT not in caplog.text


async def test_without_a_trusted_proxy_the_socket_is_logged_as_the_source(
    make_app: MakeApp, make_settings: MakeSettings, caplog: pytest.LogCaptureFixture
) -> None:
    application = await make_app(make_settings())
    caplog.set_level(logging.INFO)
    async with client_for(application) as client:
        response = await client.post("/api/sessions", headers={"CF-Connecting-IP": REAL_CLIENT})

    assert response.status_code == 201
    assert sources_logged(caplog) == ["socket"]
