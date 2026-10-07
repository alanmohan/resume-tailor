"""The generation routes as the application registers them (no database needed:
the OpenAPI document is built from the route table alone)."""

from typing import Any

import pytest

from app.main import create_app
from tests.conftest import build_test_settings

ITEM_PATH = "/api/generations/{generation_id}/items/{item_id}/regenerate"


@pytest.fixture(scope="module")
def paths() -> dict[str, Any]:
    return create_app(build_test_settings()).openapi()["paths"]


def test_all_generation_routes_are_registered(paths: dict[str, Any]) -> None:
    registered = {
        (method.upper(), path)
        for path, operations in paths.items()
        if path.startswith("/api/generations")
        for method in operations
    }
    assert registered == {
        ("POST", "/api/generations"),
        ("GET", "/api/generations"),
        ("GET", "/api/generations/{generation_id}"),
        ("PATCH", "/api/generations/{generation_id}"),
        ("POST", "/api/generations/{generation_id}/validate"),
        ("POST", ITEM_PATH),
    }


def header_names(operation: dict[str, Any]) -> set[str]:
    return {param["name"] for param in operation.get("parameters", []) if param["in"] == "header"}


def test_paid_operations_document_the_idempotency_key_header(paths: dict[str, Any]) -> None:
    assert "idempotency-key" in header_names(paths["/api/generations"]["post"])
    assert "idempotency-key" in header_names(paths[ITEM_PATH]["post"])
    assert "idempotency-key" not in header_names(paths["/api/generations/{generation_id}"]["patch"])


def test_creation_answers_201_and_every_route_requires_a_bearer_token(
    paths: dict[str, Any],
) -> None:
    assert "201" in paths["/api/generations"]["post"]["responses"]
    for path, operations in paths.items():
        if path.startswith("/api/generations"):
            for operation in operations.values():
                assert operation["security"] == [{"HTTPBearer": []}], path


def test_regeneration_body_is_optional(paths: dict[str, Any]) -> None:
    assert paths[ITEM_PATH]["post"]["requestBody"].get("required", False) is False
    assert paths["/api/generations"]["post"]["requestBody"]["required"] is True
