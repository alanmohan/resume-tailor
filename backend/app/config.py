"""Application settings.

Values come from, in order of priority: explicit constructor arguments (tests),
process environment variables, then the project-root ``.env`` file. The ``.env``
path is derived from this file's location so it does not depend on the
directory the server was started from.
"""

import hashlib
from pathlib import Path
from typing import Annotated, Literal, Self

from fastapi import Request
from pydantic import Field, SecretStr, ValidationError, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

# backend/app/config.py -> parents[0]=app, parents[1]=backend, parents[2]=repository root
REPO_ROOT = Path(__file__).resolve().parents[2]
ENV_FILE_PATH = REPO_ROOT / ".env"

TEST_DATABASE_PREFIX = "resume_tailor_test"

# Native vector sizes of the embedding models this application knows about.
# The configured dimension must match, so vectors of different sizes are never
# stored side by side. Only text-embedding-3-small was verified on the account.
EMBEDDING_MODEL_DIMENSIONS: dict[str, int] = {
    "text-embedding-3-small": 1536,
    "text-embedding-3-large": 3072,
}

REASONING_EFFORTS = {"", "none", "minimal", "low", "medium", "high", "xhigh", "max"}

DEFAULT_CORS_ORIGINS = ["http://127.0.0.1:5173", "http://localhost:5173"]

# How the template .env.example marks a secret that still has to be filled in.
PLACEHOLDER_PREFIX = "replace-with"


class ConfigError(RuntimeError):
    """Raised at startup when the configuration is invalid. Never contains secret values."""


def is_loopback_mongodb_uri(uri: str) -> bool:
    """True when every host in a ``mongodb://`` URI is this machine.

    ``mongodb+srv://`` always names a hosted cluster, so it is never loopback.
    Used to keep test runs away from a real database: the root ``.env`` may
    hold a production connection string, and a test server started without
    overriding it would otherwise write test data there.
    """
    if not uri.startswith("mongodb://"):
        return False
    authority = uri.removeprefix("mongodb://").split("/", 1)[0]
    hosts = authority.rsplit("@", 1)[-1].split(",")
    return all(host.rsplit(":", 1)[0] in {"127.0.0.1", "localhost", "[::1]"} for host in hosts)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=ENV_FILE_PATH,
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_env: Literal["development", "test", "production"] = "development"

    mongodb_uri: SecretStr = SecretStr("mongodb://127.0.0.1:27017")
    mongodb_database: str = "resume_tailor_dev"
    mongodb_server_selection_timeout_ms: int = Field(default=5000, ge=100, le=60000)
    # APP_ENV=test refuses a non-local MONGODB_URI unless this is switched on.
    allow_remote_test_database: bool = False

    # NoDecode: read the raw comma-separated string instead of expecting JSON.
    cors_origins: Annotated[list[str], NoDecode] = DEFAULT_CORS_ORIGINS

    ai_provider: Literal["openai", "fake"] = "openai"
    openai_api_key: SecretStr | None = None
    openai_model: Literal["gpt-6-luna", "gpt-5.6-terra"] = "gpt-6-luna"
    openai_embedding_model: str = "text-embedding-3-small"
    openai_embedding_dimensions: int = 1536
    openai_reasoning_effort: str = "low"

    retrieval_mode: str = "python"
    session_ttl_hours: int = Field(default=24, ge=1, le=168)

    max_profile_chars: int = Field(default=60_000, ge=1)
    max_job_chars: int = Field(default=25_000, ge=1)
    max_sources: int = Field(default=5, ge=1)
    max_request_bytes: int = Field(default=400_000, ge=1_000)
    max_evidence_chunks: int = Field(default=200, ge=1)
    max_requirements: int = Field(default=25, ge=1)

    # Extracting a long profile is the slowest call: 47 s was measured for
    # 18,000 characters, and the time grows with the length of the text.
    provider_timeout_seconds: float = Field(default=180, gt=0)
    provider_max_retries: int = Field(default=2, ge=0, le=5)
    # Reasoning tokens count against these limits, so they are sized generously.
    max_output_tokens_extraction: int = Field(default=32_000, ge=256)
    max_output_tokens_job_analysis: int = Field(default=8_000, ge=256)
    max_output_tokens_generation: int = Field(default=16_000, ge=256)
    max_output_tokens_regeneration: int = Field(default=4_000, ge=256)
    max_output_tokens_verification: int = Field(default=6_000, ge=256)

    retrieval_per_requirement: int = Field(default=4, ge=1, le=10)
    retrieval_max_context: int = Field(default=18, ge=1, le=50)
    retrieval_token_budget: int = Field(default=6_000, ge=200)

    enable_semantic_verifier: bool = False

    session_create_limit_per_hour: int = Field(default=20, ge=1)
    quota_ingest: int = Field(default=8, ge=0)
    quota_confirm: int = Field(default=15, ge=0)
    quota_job_analysis: int = Field(default=15, ge=0)
    quota_generation: int = Field(default=12, ge=0)
    quota_regeneration: int = Field(default=40, ge=0)
    quota_validation: int = Field(default=40, ge=0)
    global_daily_ai_call_limit: int = Field(default=600, ge=0)

    # The three settings below decide where the client IP for the session
    # rate limit is read from; see ratelimit.client_address.
    trust_proxy_headers: bool = False
    # Header in which the platform itself reports the client IP, e.g.
    # "cf-connecting-ip" on Render. Only read when TRUST_PROXY_HEADERS is on.
    client_ip_header: str | None = None
    # Number of trusted proxies that append to X-Forwarded-For.
    trusted_proxy_hops: int = Field(default=1, ge=1, le=10)
    # Key for hashing client IPs in rate-limit counters. Optional: see ip_hash_key.
    ip_hash_salt: SecretStr | None = None

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_origins(cls, value: object) -> object:
        """Accept "https://a.example,https://b.example" from the environment."""
        if isinstance(value, str):
            return [origin.strip().rstrip("/") for origin in value.split(",") if origin.strip()]
        return value

    @field_validator("cors_origins")
    @classmethod
    def _check_origins(cls, origins: list[str]) -> list[str]:
        if not origins:
            raise ValueError("CORS_ORIGINS must list at least one exact origin")
        for origin in origins:
            if "*" in origin:
                raise ValueError("CORS_ORIGINS must list exact origins; wildcards are not allowed")
            if not origin.startswith(("http://", "https://")):
                raise ValueError("each CORS origin must start with http:// or https://")
        return origins

    @field_validator("openai_api_key", "ip_hash_salt", mode="before")
    @classmethod
    def _blank_secret_is_missing(cls, value: object) -> object:
        """Treat an empty value (for example ``OPENAI_API_KEY=``) and the
        template's "replace-with-..." placeholder as not configured. A copied
        .env.example must not look like a working key: the first provider call
        would fail instead of the readiness check saying what is missing."""
        if isinstance(value, str):
            stripped = value.strip()
            if not stripped or stripped.lower().startswith(PLACEHOLDER_PREFIX):
                return None
        return value

    @field_validator("client_ip_header", mode="before")
    @classmethod
    def _normalise_header_name(cls, value: object) -> object:
        """Header names are case-insensitive; an empty value means "not set"."""
        if isinstance(value, str):
            return value.strip().lower() or None
        return value

    @field_validator("retrieval_mode")
    @classmethod
    def _check_retrieval_mode(cls, mode: str) -> str:
        if mode != "python":
            raise ValueError(
                "RETRIEVAL_MODE must be 'python'; it is the only implemented retrieval backend"
            )
        return mode

    @field_validator("openai_reasoning_effort")
    @classmethod
    def _check_reasoning_effort(cls, effort: str) -> str:
        effort = effort.strip().lower()
        if effort not in REASONING_EFFORTS:
            raise ValueError("OPENAI_REASONING_EFFORT must be empty or a known effort level")
        return effort

    @model_validator(mode="after")
    def _check_consistency(self) -> Self:
        expected = EMBEDDING_MODEL_DIMENSIONS.get(self.openai_embedding_model)
        if expected is None:
            raise ValueError(
                "OPENAI_EMBEDDING_MODEL is not a known embedding model; "
                "add its dimension to EMBEDDING_MODEL_DIMENSIONS first"
            )
        if self.openai_embedding_dimensions != expected:
            raise ValueError(
                f"OPENAI_EMBEDDING_DIMENSIONS must be {expected} for {self.openai_embedding_model}"
            )
        if self.app_env == "test" and not self.mongodb_database.startswith(TEST_DATABASE_PREFIX):
            raise ValueError(
                f"APP_ENV=test requires MONGODB_DATABASE to start with '{TEST_DATABASE_PREFIX}'"
            )
        if (
            self.app_env == "test"
            and not self.allow_remote_test_database
            and not is_loopback_mongodb_uri(self.mongodb_uri.get_secret_value())
        ):
            raise ValueError(
                "APP_ENV=test requires MONGODB_URI to point at 127.0.0.1 or localhost, so a "
                "test run can never touch a hosted database (for example one configured in "
                ".env); set MONGODB_URI=mongodb://127.0.0.1:27017, or "
                "ALLOW_REMOTE_TEST_DATABASE=true for a dedicated test cluster"
            )
        if self.app_env == "production":
            self._check_production_rules()
        return self

    def _check_production_rules(self) -> None:
        if self.ai_provider != "openai":
            raise ValueError("AI_PROVIDER=fake is not allowed when APP_ENV=production")
        if self.openai_api_key is None:
            raise ValueError("OPENAI_API_KEY is required when APP_ENV=production")
        if "cors_origins" not in self.model_fields_set:
            raise ValueError("CORS_ORIGINS must be set explicitly when APP_ENV=production")
        # Without this rule a deployment that lacks the variable starts anyway,
        # looks for a database on its own machine and only ever reports
        # "database unavailable".
        uri_is_set = "mongodb_uri" in self.model_fields_set
        if not uri_is_set or not self.mongodb_uri.get_secret_value().strip():
            raise ValueError("MONGODB_URI must be set explicitly when APP_ENV=production")

    @property
    def provider_configured(self) -> bool:
        """True when the selected provider has everything it needs to be called."""
        return self.ai_provider == "fake" or self.openai_api_key is not None

    @property
    def ip_hash_key(self) -> bytes:
        """Secret key for hashing client IP addresses.

        Uses IP_HASH_SALT when set. Otherwise it is derived from the database
        URI, which is already a deployment secret and is stable across restarts,
        so rate-limit counters keep matching the same client after a restart.
        """
        if self.ip_hash_salt is not None:
            return self.ip_hash_salt.get_secret_value().encode()
        uri = self.mongodb_uri.get_secret_value()
        return hashlib.sha256(f"ip-hash-key:{uri}".encode()).digest()


def load_settings() -> Settings:
    """Build settings from the environment and root .env, failing with a safe message.

    Pydantic's own error text repeats the rejected input, which could be a
    secret, so only the setting name and the reason are reported.
    """
    try:
        return Settings()
    except ValidationError as error:
        problems = []
        for item in error.errors(include_input=False, include_url=False):
            name = ".".join(str(part) for part in item["loc"]).upper()
            reason = item["msg"].removeprefix("Value error, ")
            problems.append(f"{name}: {reason}" if name else reason)
        raise ConfigError("Invalid configuration - " + "; ".join(problems)) from None


def get_settings(request: Request) -> Settings:
    """FastAPI dependency: the settings object the running app was created with."""
    return request.app.state.settings
