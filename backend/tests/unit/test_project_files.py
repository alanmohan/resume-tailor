"""Guards for the configuration files in the repository root: the env
template, the compose file and the Render blueprint. They keep these files in
step with the settings class and make sure no secret value is committed."""

import re
from pathlib import Path
from typing import Any

import pytest
import yaml

from app.config import REPO_ROOT, Settings

ENV_EXAMPLE = REPO_ROOT / ".env.example"
RENDER_BLUEPRINT = REPO_ROOT / "render.yaml"
COMPOSE_FILE = REPO_ROOT / "compose.yaml"
GITIGNORE = REPO_ROOT / ".gitignore"

SECRET_SETTINGS = {"MONGODB_URI", "OPENAI_API_KEY"}
# Looks like a real credential: an OpenAI-style key or a URI with a password.
CREDENTIAL_PATTERN = re.compile(r"sk-[A-Za-z0-9_-]{16,}|://[^/\s:@]+:[^@\s]+@")


@pytest.fixture(autouse=True)
def clean_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for field_name in Settings.model_fields:
        monkeypatch.delenv(field_name.upper(), raising=False)


def env_example_keys() -> list[str]:
    lines = ENV_EXAMPLE.read_text(encoding="utf-8").splitlines()
    return [line.split("=", 1)[0] for line in lines if line and not line.startswith("#")]


def load_yaml(path: Path) -> Any:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def service(name: str) -> dict[str, Any]:
    services = {item["name"]: item for item in load_yaml(RENDER_BLUEPRINT)["services"]}
    return services[name]


# ---- .env.example ------------------------------------------------------------------


def test_env_example_lists_every_setting_exactly_once() -> None:
    keys = env_example_keys()
    assert len(keys) == len(set(keys))
    assert set(keys) == {name.upper() for name in Settings.model_fields}


def test_env_example_loads_and_matches_the_built_in_defaults() -> None:
    from_template = Settings(_env_file=ENV_EXAMPLE)
    defaults = Settings(_env_file=None)
    differing = {
        name
        for name in Settings.model_fields
        if getattr(from_template, name) != getattr(defaults, name)
    }
    # Only the API key placeholder differs from the code defaults.
    assert differing == {"openai_api_key"}


def test_env_example_contains_no_real_credentials() -> None:
    text = ENV_EXAMPLE.read_text(encoding="utf-8")
    assert not CREDENTIAL_PATTERN.search(text)
    assert "OPENAI_API_KEY=replace-with-your-openai-api-key" in text


def test_every_setting_in_env_example_has_a_comment() -> None:
    lines = ENV_EXAMPLE.read_text(encoding="utf-8").splitlines()
    for index, line in enumerate(lines):
        if line and not line.startswith("#"):
            preceding = lines[index - 1]
            assert preceding.startswith("#") or "=" in preceding, line


# ---- render.yaml -------------------------------------------------------------------


def test_blueprint_defines_the_api_service_as_specified() -> None:
    api = service("resume-tailor-api")
    assert api["type"] == "web"
    assert api["runtime"] == "python"
    assert api["plan"] == "free"
    assert api["rootDir"] == "backend"
    assert api["buildCommand"] == "pip install -r requirements.txt"
    assert api["startCommand"] == "uvicorn app.main:app --host 0.0.0.0 --port $PORT"
    assert "--reload" not in api["startCommand"]
    assert api["healthCheckPath"] == "/healthz"


def test_blueprint_environment_is_valid_production_configuration() -> None:
    env = {item["key"]: item for item in service("resume-tailor-api")["envVars"]}
    assert env["APP_ENV"]["value"] == "production"
    assert env["TRUST_PROXY_HEADERS"]["value"] == "true"
    assert re.fullmatch(r"3\.12\.\d+", env["PYTHON_VERSION"]["value"])

    setting_names = {name.upper() for name in Settings.model_fields}
    assert set(env) - {"PYTHON_VERSION"} <= setting_names

    # The fixed values, plus stand-ins for the dashboard-only ones, must load.
    values = {key: item["value"] for key, item in env.items() if "value" in item}
    values.pop("PYTHON_VERSION")
    settings = Settings(
        _env_file=None,
        **{key.lower(): value for key, value in values.items()},
        mongodb_uri="mongodb://placeholder.invalid/db",
        openai_api_key="placeholder-key",
        cors_origins="https://resume-tailor-web.onrender.com",
    )
    assert settings.app_env == "production"
    assert settings.ai_provider == "openai"
    assert settings.openai_model == "gpt-6-luna"


def test_blueprint_never_contains_secret_values() -> None:
    env = {item["key"]: item for item in service("resume-tailor-api")["envVars"]}
    for name in SECRET_SETTINGS | {"CORS_ORIGINS"}:
        assert env[name] == {"key": name, "sync": False}
    assert env["IP_HASH_SALT"] == {"key": "IP_HASH_SALT", "generateValue": True}
    assert not CREDENTIAL_PATTERN.search(RENDER_BLUEPRINT.read_text(encoding="utf-8"))


def test_blueprint_defines_the_static_site_with_spa_rewrite() -> None:
    web = service("resume-tailor-web")
    assert web["runtime"] == "static"
    assert web["rootDir"] == "frontend"
    assert web["buildCommand"] == "npm ci && npm run build"
    assert web["staticPublishPath"] == "dist"
    assert web["routes"] == [{"type": "rewrite", "source": "/*", "destination": "/index.html"}]
    assert web["envVars"] == [{"key": "VITE_API_BASE_URL", "sync": False}]


# ---- compose.yaml and .gitignore ---------------------------------------------------


def test_compose_database_is_pinned_and_bound_to_loopback_only() -> None:
    mongo = load_yaml(COMPOSE_FILE)["services"]["mongo"]
    assert re.fullmatch(r"mongo:\d+\.\d+\.\d+", mongo["image"])
    assert mongo["ports"] == ["127.0.0.1:27017:27017"]
    assert mongo["volumes"] == ["mongo_data:/data/db"]
    assert "healthcheck" in mongo


def test_gitignore_covers_secrets_and_personal_files() -> None:
    patterns = set(GITIGNORE.read_text(encoding="utf-8").splitlines())
    for pattern in (
        ".env",
        ".env.*",
        "!.env.example",
        "Experience Master.*",
        "*.docx",
        "*.pdf",
        "uploads/",
        "*.mov",
        "*.mp4",
        "backend/.venv/",
        "node_modules/",
        "dist/",
        "playwright-report/",
        "test-results/",
        "coverage/",
        ".DS_Store",
        ".vscode/",
        "backend/fixtures/jobs/live/_local_full/",
    ):
        assert pattern in patterns, pattern
