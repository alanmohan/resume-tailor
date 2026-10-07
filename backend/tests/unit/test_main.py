"""The ASGI entry point: ``uvicorn app.main:app`` must find the app, and
importing the module must not load configuration."""

import sys

import pytest
from fastapi import FastAPI

import app.main as main_module
from tests.conftest import build_test_settings


def test_importing_the_module_does_not_build_the_app() -> None:
    assert "app" not in vars(main_module)


def test_app_attribute_is_built_once_on_first_access(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = []

    def fake_load_settings():
        calls.append(1)
        return build_test_settings()

    monkeypatch.setattr(main_module, "load_settings", fake_load_settings)
    try:
        first = main_module.app
        second = main_module.app
    finally:
        vars(main_module).pop("app", None)

    assert isinstance(first, FastAPI)
    assert first is second
    assert len(calls) == 1
    assert first.state.settings.app_env == "test"


def test_other_missing_attributes_raise_attribute_error() -> None:
    with pytest.raises(AttributeError):
        _ = main_module.does_not_exist
    assert sys.modules["app.main"] is main_module


def test_create_app_registers_routes_and_state() -> None:
    application = main_module.create_app(build_test_settings())
    paths = set(application.openapi()["paths"])
    assert {"/healthz", "/readyz", "/api/sessions", "/api/session"} <= paths
    assert application.state.provider.mode == "fake"
    assert application.state.settings.mongodb_database.startswith("resume_tailor_test")
