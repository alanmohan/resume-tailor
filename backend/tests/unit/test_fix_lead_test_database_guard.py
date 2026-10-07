"""APP_ENV=test must never reach a hosted database.

The project-root .env may hold a production MONGODB_URI. A test server started
with only APP_ENV and MONGODB_DATABASE overridden would inherit it, so the
settings refuse any non-local URI in test mode unless that is requested
explicitly.
"""

import pytest
from pydantic import ValidationError

from app.config import Settings, is_loopback_mongodb_uri

TEST_DATABASE = "resume_tailor_test_guard"
HOSTED_URI = "mongodb+srv://app_user:not-a-real-password@cluster0.example.mongodb.net/"


@pytest.mark.parametrize(
    "uri",
    [
        "mongodb://127.0.0.1:27017",
        "mongodb://localhost",
        "mongodb://localhost:27017/?directConnection=true",
        "mongodb://user:pass@127.0.0.1:27017/admin",
        "mongodb://[::1]:27017",
        "mongodb://127.0.0.1:27017,localhost:27018",
    ],
)
def test_local_uris_are_recognised(uri: str) -> None:
    assert is_loopback_mongodb_uri(uri)


@pytest.mark.parametrize(
    "uri",
    [
        HOSTED_URI,
        "mongodb://db.internal.example:27017",
        "mongodb://127.0.0.1:27017,db.internal.example:27017",
        "mongodb://user:pass@10.0.0.5:27017",
    ],
)
def test_remote_uris_are_not_local(uri: str) -> None:
    assert not is_loopback_mongodb_uri(uri)


def test_test_mode_refuses_a_hosted_database_without_echoing_the_uri() -> None:
    with pytest.raises(ValidationError) as raised:
        Settings(
            _env_file=None,
            app_env="test",
            ai_provider="fake",
            mongodb_database=TEST_DATABASE,
            mongodb_uri=HOSTED_URI,
        )
    messages = [item["msg"] for item in raised.value.errors(include_input=False)]
    assert any("APP_ENV=test requires MONGODB_URI to point at 127.0.0.1" in m for m in messages)
    assert all("not-a-real-password" not in m and "cluster0" not in m for m in messages)


def test_test_mode_accepts_a_hosted_database_only_when_asked_explicitly() -> None:
    settings = Settings(
        _env_file=None,
        app_env="test",
        ai_provider="fake",
        mongodb_database=TEST_DATABASE,
        mongodb_uri=HOSTED_URI,
        allow_remote_test_database=True,
    )
    assert settings.allow_remote_test_database


def test_development_mode_is_not_restricted() -> None:
    settings = Settings(_env_file=None, app_env="development", mongodb_uri=HOSTED_URI)
    assert settings.app_env == "development"
