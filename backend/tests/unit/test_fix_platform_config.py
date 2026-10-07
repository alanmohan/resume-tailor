"""Configuration fixes: the template's placeholder key (SPEC-06), the explicit
database URI in production, the provider timeout and the deployed values in
render.yaml. No test here reads the real project .env file."""

from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from app.config import REPO_ROOT, ConfigError, Settings, load_settings

FAKE_KEY = "sk-unit-test-not-a-real-key-0000"
PLACEHOLDER_KEY = "replace-with-your-openai-api-key"
PRODUCTION = {
    "app_env": "production",
    "openai_api_key": FAKE_KEY,
    "cors_origins": "https://resume-tailor.example.com",
}


@pytest.fixture(autouse=True)
def clean_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for field_name in Settings.model_fields:
        monkeypatch.delenv(field_name.upper(), raising=False)


def make(**overrides: object) -> Settings:
    return Settings(_env_file=None, **overrides)


# ---- SPEC-06: the placeholder is not a key ------------------------------------------


@pytest.mark.parametrize("value", [PLACEHOLDER_KEY, "  Replace-With-your-key  ", "", "   "])
def test_placeholder_and_blank_keys_count_as_not_configured(value: str) -> None:
    settings = make(openai_api_key=value)

    assert settings.openai_api_key is None
    assert settings.provider_configured is False


def test_a_copied_env_example_does_not_report_a_configured_provider() -> None:
    from_template = Settings(_env_file=REPO_ROOT / ".env.example")

    assert from_template.ai_provider == "openai"
    assert from_template.provider_configured is False


def test_a_real_looking_key_and_the_fake_provider_are_configured() -> None:
    assert make(openai_api_key=FAKE_KEY).provider_configured is True
    assert make(ai_provider="fake", openai_api_key=PLACEHOLDER_KEY).provider_configured is True


def test_production_refuses_the_placeholder_key() -> None:
    with pytest.raises(ValidationError) as error:
        make(**PRODUCTION | {"openai_api_key": PLACEHOLDER_KEY}, mongodb_uri="mongodb://db.example")
    assert "OPENAI_API_KEY is required" in str(error.value)


def test_env_example_explains_demo_mode_without_a_key() -> None:
    text = (REPO_ROOT / ".env.example").read_text(encoding="utf-8")
    assert "AI_PROVIDER=fake runs the whole app without a key" in text


# ---- MONGODB_URI must be explicit in production -------------------------------------


def test_production_requires_an_explicit_database_uri() -> None:
    """Otherwise the service starts, looks for a database on its own machine
    and reports nothing but "database unavailable"."""
    with pytest.raises(ValidationError) as error:
        make(**PRODUCTION)
    assert "MONGODB_URI must be set explicitly when APP_ENV=production" in str(error.value)


def test_production_refuses_a_blank_database_uri() -> None:
    with pytest.raises(ValidationError) as error:
        make(**PRODUCTION, mongodb_uri="  ")
    assert "MONGODB_URI must be set explicitly" in str(error.value)


def test_production_starts_with_an_explicit_database_uri() -> None:
    settings = make(**PRODUCTION, mongodb_uri="mongodb://db.example.invalid:27017")
    assert settings.app_env == "production"


def test_other_environments_keep_the_local_default_database() -> None:
    assert make().mongodb_uri.get_secret_value() == "mongodb://127.0.0.1:27017"


def test_startup_names_the_missing_database_uri_without_any_value(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setitem(Settings.model_config, "env_file", tmp_path / "missing.env")
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("OPENAI_API_KEY", FAKE_KEY)
    monkeypatch.setenv("CORS_ORIGINS", "https://resume-tailor.example.com")

    with pytest.raises(ConfigError) as error:
        load_settings()

    assert "MONGODB_URI must be set explicitly when APP_ENV=production" in str(error.value)
    assert FAKE_KEY not in str(error.value)


# ---- Measured provider limits -------------------------------------------------------


def test_defaults_follow_the_real_provider_measurements() -> None:
    settings = make()
    assert settings.provider_timeout_seconds == 180
    assert settings.max_profile_chars == 60_000


def test_env_example_says_why_the_deployment_uses_a_lower_profile_limit() -> None:
    text = (REPO_ROOT / ".env.example").read_text(encoding="utf-8")
    assert "the public deployment sets 30000" in text
    assert "PROVIDER_TIMEOUT_SECONDS=180" in text


def test_blueprint_mirrors_the_deployed_values() -> None:
    blueprint = yaml.safe_load((REPO_ROOT / "render.yaml").read_text(encoding="utf-8"))
    api = next(item for item in blueprint["services"] if item["name"] == "resume-tailor-api")
    env = {item["key"]: item.get("value") for item in api["envVars"]}

    assert env["PROVIDER_TIMEOUT_SECONDS"] == "180"
    assert env["PROVIDER_MAX_RETRIES"] == "1"
    assert env["MAX_PROFILE_CHARS"] == "30000"
    assert env["CLIENT_IP_HEADER"] == "cf-connecting-ip"
    assert env["TRUST_PROXY_HEADERS"] == "true"
    # The database URI is a dashboard secret, never a value in the file.
    assert env["MONGODB_URI"] is None
