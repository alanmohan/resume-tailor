"""Settings validation. No test here reads the real project .env file."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from app import config
from app.config import ENV_FILE_PATH, ConfigError, Settings, load_settings

FAKE_KEY = "sk-unit-test-not-a-real-key-0000"


@pytest.fixture(autouse=True)
def clean_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Remove every settings variable from the environment so the shell that
    runs the tests cannot influence them."""
    for field_name in Settings.model_fields:
        monkeypatch.delenv(field_name.upper(), raising=False)


def make(**overrides: object) -> Settings:
    return Settings(_env_file=None, **overrides)


def production(**overrides: object) -> Settings:
    values = {
        "app_env": "production",
        "openai_api_key": FAKE_KEY,
        "cors_origins": "https://resume-tailor.example.com",
    }
    values.update(overrides)
    return make(**values)


def test_defaults_match_the_documented_values() -> None:
    settings = make()
    assert settings.app_env == "development"
    assert settings.mongodb_database == "resume_tailor_dev"
    assert settings.ai_provider == "openai"
    assert settings.openai_model == "gpt-6-luna"
    assert settings.openai_embedding_model == "text-embedding-3-small"
    assert settings.openai_embedding_dimensions == 1536
    assert settings.openai_reasoning_effort == "low"
    assert settings.retrieval_mode == "python"
    assert settings.session_ttl_hours == 24
    assert settings.max_profile_chars == 60_000
    assert settings.max_job_chars == 25_000
    assert settings.cors_origins == ["http://127.0.0.1:5173", "http://localhost:5173"]
    assert settings.enable_semantic_verifier is False
    assert settings.trust_proxy_headers is False


def test_env_file_path_is_derived_from_the_code_location() -> None:
    expected = Path(config.__file__).resolve().parents[2] / ".env"
    assert expected == ENV_FILE_PATH
    assert ENV_FILE_PATH.is_absolute()
    assert Settings.model_config["env_file"] == ENV_FILE_PATH
    # The repository root is the folder that contains backend/app/config.py.
    assert (ENV_FILE_PATH.parent / "backend" / "app" / "config.py").is_file()


def test_env_file_path_does_not_depend_on_the_working_directory(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    (tmp_path / ".env").write_text("MONGODB_DATABASE=from_the_wrong_env_file\n")
    monkeypatch.chdir(tmp_path)
    env_file = tmp_path / "elsewhere" / ".env"
    env_file.parent.mkdir()
    env_file.write_text("MONGODB_DATABASE=from_configured_env_file\n")
    monkeypatch.setitem(Settings.model_config, "env_file", env_file)

    assert load_settings().mongodb_database == "from_configured_env_file"


def test_environment_variables_override_the_env_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("MONGODB_DATABASE=from_file\nSESSION_TTL_HOURS=12\n")
    monkeypatch.setenv("MONGODB_DATABASE", "from_environment")

    settings = Settings(_env_file=env_file)

    assert settings.mongodb_database == "from_environment"
    assert settings.session_ttl_hours == 12


def test_cors_origins_are_parsed_from_a_comma_separated_string(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CORS_ORIGINS", "https://a.example.com/ , https://b.example.com")
    assert make().cors_origins == ["https://a.example.com", "https://b.example.com"]


@pytest.mark.parametrize("origins", ["*", "https://*.example.com", "example.com", ""])
def test_invalid_cors_origins_are_rejected(origins: str) -> None:
    with pytest.raises(ValidationError):
        make(cors_origins=origins)


def test_retrieval_mode_other_than_python_fails_with_a_clear_message() -> None:
    with pytest.raises(ValidationError) as error:
        make(retrieval_mode="atlas")
    assert "RETRIEVAL_MODE must be 'python'" in str(error.value)


def test_embedding_dimension_must_match_the_model() -> None:
    with pytest.raises(ValidationError) as error:
        make(openai_embedding_dimensions=256)
    assert "OPENAI_EMBEDDING_DIMENSIONS must be 1536" in str(error.value)


def test_unknown_embedding_model_is_rejected() -> None:
    with pytest.raises(ValidationError) as error:
        make(openai_embedding_model="mystery-embedding")
    assert "not a known embedding model" in str(error.value)


def test_only_the_two_supported_generation_models_are_accepted() -> None:
    assert make(openai_model="gpt-5.6-terra").openai_model == "gpt-5.6-terra"
    with pytest.raises(ValidationError):
        make(openai_model="some-other-model")


def test_reasoning_effort_may_be_empty_and_is_normalised() -> None:
    assert make(openai_reasoning_effort="").openai_reasoning_effort == ""
    assert make(openai_reasoning_effort=" LOW ").openai_reasoning_effort == "low"
    with pytest.raises(ValidationError):
        make(openai_reasoning_effort="turbo")


def test_test_environment_requires_a_test_database_name() -> None:
    with pytest.raises(ValidationError) as error:
        make(app_env="test", mongodb_database="resume_tailor_dev")
    assert "resume_tailor_test" in str(error.value)
    assert make(app_env="test", mongodb_database="resume_tailor_test_abc").app_env == "test"


def test_valid_production_configuration_is_accepted() -> None:
    settings = production()
    assert settings.provider_configured is True
    assert settings.cors_origins == ["https://resume-tailor.example.com"]


def test_production_rejects_the_fake_provider() -> None:
    with pytest.raises(ValidationError) as error:
        production(ai_provider="fake")
    assert "AI_PROVIDER=fake is not allowed" in str(error.value)


def test_production_requires_an_api_key() -> None:
    with pytest.raises(ValidationError) as error:
        production(openai_api_key=None)
    assert "OPENAI_API_KEY is required" in str(error.value)


def test_production_requires_explicit_cors_origins() -> None:
    with pytest.raises(ValidationError) as error:
        make(app_env="production", openai_api_key=FAKE_KEY)
    assert "CORS_ORIGINS must be set explicitly" in str(error.value)


def test_blank_api_key_counts_as_not_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "   ")
    settings = make()
    assert settings.openai_api_key is None
    assert settings.provider_configured is False
    assert make(ai_provider="fake").provider_configured is True


def test_secrets_are_not_shown_when_settings_are_printed() -> None:
    settings = make(openai_api_key=FAKE_KEY, mongodb_uri="mongodb://user:hunter2@db.example/x")
    printed = repr(settings) + str(settings)
    assert FAKE_KEY not in printed
    assert "hunter2" not in printed


def test_load_settings_reports_the_setting_name_but_never_values(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setitem(Settings.model_config, "env_file", tmp_path / "missing.env")
    monkeypatch.setenv("OPENAI_API_KEY", FAKE_KEY)
    monkeypatch.setenv("MONGODB_URI", "mongodb://user:hunter2@db.example/x")
    monkeypatch.setenv("RETRIEVAL_MODE", "atlas")
    monkeypatch.setenv("SESSION_TTL_HOURS", "not-a-number")

    with pytest.raises(ConfigError) as error:
        load_settings()

    message = str(error.value)
    assert "RETRIEVAL_MODE" in message
    assert "SESSION_TTL_HOURS" in message
    assert FAKE_KEY not in message
    assert "hunter2" not in message
    assert "not-a-number" not in message
    # The pydantic error, which does contain input values, must not be chained.
    assert error.value.__cause__ is None
    assert error.value.__suppress_context__ is True


def test_ip_hash_key_is_stable_and_uses_the_salt_when_given() -> None:
    derived = make().ip_hash_key
    assert derived == make().ip_hash_key
    assert derived != make(mongodb_uri="mongodb://other-host:27017").ip_hash_key
    assert make(ip_hash_salt="pepper").ip_hash_key == b"pepper"


def test_production_accepts_cors_origins_from_the_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("OPENAI_API_KEY", FAKE_KEY)
    monkeypatch.setenv("CORS_ORIGINS", "https://resume-tailor.example.com")
    settings = make()
    assert settings.app_env == "production"
    assert settings.cors_origins == ["https://resume-tailor.example.com"]
