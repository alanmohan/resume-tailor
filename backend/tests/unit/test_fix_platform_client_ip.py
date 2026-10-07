"""SEC-3: which address the session rate limit is keyed on.

Verified on the deployed service: the platform's edge APPENDS to
X-Forwarded-For, so its leftmost entry is whatever the client sent. Keying on
it let one client open unlimited sessions by changing a forged first hop.
"""

import pytest
from starlette.requests import Request

from app.config import Settings
from app.ratelimit import ClientAddress, client_address
from tests.conftest import build_test_settings

SOCKET_PEER = "10.0.0.5"  # the platform's proxy, as the application sees it
REAL_CLIENT = "203.0.113.7"


def request_with(*headers: tuple[str, str], peer: str | None = SOCKET_PEER) -> Request:
    raw = [(name.lower().encode(), value.encode()) for name, value in headers]
    return Request({"type": "http", "headers": raw, "client": (peer, 50000) if peer else None})


def trusted(**overrides: object) -> Settings:
    return build_test_settings(trust_proxy_headers=True, **overrides)


@pytest.mark.parametrize("forged", ["198.51.100.1", "198.51.100.2", "10.9.8.7, 192.0.2.44"])
def test_a_forged_leftmost_hop_does_not_change_the_address(forged: str) -> None:
    request = request_with(("X-Forwarded-For", f"{forged}, {REAL_CLIENT}"))

    assert client_address(request, trusted()) == ClientAddress(REAL_CLIENT, "x-forwarded-for")


def test_a_forged_header_line_of_its_own_does_not_change_the_address() -> None:
    """Some proxies add a second X-Forwarded-For line instead of extending the
    client's one; the lines are read as one list, so the last entry still wins."""
    request = request_with(("X-Forwarded-For", "198.51.100.1"), ("X-Forwarded-For", REAL_CLIENT))

    assert client_address(request, trusted()).ip == REAL_CLIENT


def test_the_hop_is_counted_from_the_right_for_several_trusted_proxies() -> None:
    request = request_with(("X-Forwarded-For", f"198.51.100.1, {REAL_CLIENT}, 172.16.0.9"))

    assert client_address(request, trusted(trusted_proxy_hops=2)).ip == REAL_CLIENT
    assert client_address(request, trusted(trusted_proxy_hops=1)).ip == "172.16.0.9"


def test_a_forwarded_header_shorter_than_the_trusted_proxies_is_not_used() -> None:
    request = request_with(("X-Forwarded-For", "198.51.100.1"))

    address = client_address(request, trusted(trusted_proxy_hops=2))

    assert address == ClientAddress(SOCKET_PEER, "socket")


def test_platform_headers_are_preferred_over_the_forwarded_chain() -> None:
    forwarded = ("X-Forwarded-For", "198.51.100.1, 172.16.0.9")
    cloudflare = ("CF-Connecting-IP", REAL_CLIENT)
    akamai = ("True-Client-IP", "192.0.2.10")

    assert client_address(request_with(forwarded, akamai, cloudflare), trusted()) == ClientAddress(
        REAL_CLIENT, "cf-connecting-ip"
    )
    assert client_address(request_with(forwarded, akamai), trusted()) == ClientAddress(
        "192.0.2.10", "true-client-ip"
    )


def test_the_configured_header_comes_first() -> None:
    request = request_with(("Fly-Client-IP", REAL_CLIENT), ("CF-Connecting-IP", "192.0.2.10"))
    settings = trusted(client_ip_header="Fly-Client-IP")

    assert settings.client_ip_header == "fly-client-ip"
    assert client_address(request, settings) == ClientAddress(REAL_CLIENT, "fly-client-ip")
    # Missing on a request: the next source in the order is used.
    assert client_address(request_with(("CF-Connecting-IP", "192.0.2.10")), settings).source == (
        "cf-connecting-ip"
    )


def test_every_header_is_ignored_unless_proxies_are_trusted() -> None:
    request = request_with(
        ("CF-Connecting-IP", REAL_CLIENT),
        ("True-Client-IP", REAL_CLIENT),
        ("X-Forwarded-For", REAL_CLIENT),
    )
    settings = build_test_settings(trust_proxy_headers=False, client_ip_header="cf-connecting-ip")

    assert client_address(request, settings) == ClientAddress(SOCKET_PEER, "socket")


def test_without_any_header_the_socket_peer_is_used() -> None:
    assert client_address(request_with(), trusted()) == ClientAddress(SOCKET_PEER, "socket")
    assert client_address(request_with(peer=None), trusted()) == ClientAddress("unknown", "socket")


def test_proxy_settings_have_safe_defaults_and_bounds() -> None:
    defaults = build_test_settings()
    assert (defaults.client_ip_header, defaults.trusted_proxy_hops) == (None, 1)
    assert build_test_settings(client_ip_header="  ").client_ip_header is None
    with pytest.raises(ValueError):
        build_test_settings(trusted_proxy_hops=0)
