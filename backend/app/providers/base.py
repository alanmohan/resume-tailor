"""The AI provider interface: protocol, usage accounting and error types.

Application code depends only on this module. Two implementations exist:
``OpenAIProvider`` (real) and ``FakeProvider`` (deterministic, tests and demo
mode only). The LLM-facing models live in ``extraction_models`` and
``generation_models`` and are re-exported here for convenience.
"""

from dataclasses import dataclass
from typing import Protocol

from app.providers.extraction_models import (
    LLMBullet,
    LLMConflict,
    LLMConflictValue,
    LLMContactItem,
    LLMExtraction,
    LLMJobAnalysis,
    LLMJobInput,
    LLMRecord,
    LLMRequirement,
    LLMSource,
)
from app.providers.generation_models import (
    LLMBulletOut,
    LLMClaimCheck,
    LLMContextEvidence,
    LLMContextRecord,
    LLMContextRequirement,
    LLMCoverageOut,
    LLMEntryOut,
    LLMGeneration,
    LLMGenerationContext,
    LLMJobBrief,
    LLMRegenResult,
    LLMRegenTarget,
    LLMSkillOut,
    LLMStatement,
    LLMVerdict,
    LLMVerification,
)
from app.providers.llm_model import LLMModel
from app.schemas.common import ProviderMode

__all__ = [
    "AIProvider",
    "LLMBullet",
    "LLMBulletOut",
    "LLMClaimCheck",
    "LLMConflict",
    "LLMConflictValue",
    "LLMContactItem",
    "LLMContextEvidence",
    "LLMContextRecord",
    "LLMContextRequirement",
    "LLMCoverageOut",
    "LLMEntryOut",
    "LLMExtraction",
    "LLMGeneration",
    "LLMGenerationContext",
    "LLMJobAnalysis",
    "LLMJobBrief",
    "LLMJobInput",
    "LLMModel",
    "LLMRecord",
    "LLMRegenResult",
    "LLMRegenTarget",
    "LLMRequirement",
    "LLMSkillOut",
    "LLMSource",
    "LLMStatement",
    "LLMVerdict",
    "LLMVerification",
    "ProviderError",
    "ProviderInvalidOutput",
    "ProviderNotConfigured",
    "ProviderRateLimited",
    "ProviderTimeout",
    "ProviderUnavailable",
    "Usage",
]


@dataclass(frozen=True)
class Usage:
    """Token and call counts for one or more provider calls. Add with ``+``."""

    input_tokens: int = 0
    output_tokens: int = 0
    embedding_tokens: int = 0
    provider_calls: int = 0

    def __add__(self, other: "Usage") -> "Usage":
        return Usage(
            input_tokens=self.input_tokens + other.input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
            embedding_tokens=self.embedding_tokens + other.embedding_tokens,
            provider_calls=self.provider_calls + other.provider_calls,
        )


class ProviderError(Exception):
    """A provider call failed. ``message`` is written by this application and is
    safe to show to users; it never contains provider payloads or secrets.
    ``code`` is the API error code; the HTTP status is chosen in app/errors.py."""

    code = "provider_unavailable"

    def __init__(self, message: str, *, retryable: bool = True) -> None:
        super().__init__(message)
        self.message = message
        self.retryable = retryable


class ProviderTimeout(ProviderError):
    code = "provider_timeout"


class ProviderRateLimited(ProviderError):
    code = "provider_rate_limited"


class ProviderUnavailable(ProviderError):
    code = "provider_unavailable"


class ProviderInvalidOutput(ProviderError):
    """The provider answered, but with a refusal or with incomplete/invalid output."""

    code = "provider_invalid_output"


class ProviderNotConfigured(ProviderError):
    """No API key is configured, so the real provider cannot be called."""

    code = "provider_unavailable"

    def __init__(self, message: str = "The AI provider is not configured on this server.") -> None:
        super().__init__(message, retryable=False)


class AIProvider(Protocol):
    """Everything the application asks of an AI provider.

    Each call returns its result together with the Usage it consumed and raises
    a ProviderError subclass on failure. A provider never substitutes fake
    output for a failed real call.
    """

    mode: ProviderMode
    generation_model: str
    embedding_model: str
    embedding_dimension: int

    async def embed(self, texts: list[str]) -> tuple[list[list[float]], Usage]:
        """One vector of ``embedding_dimension`` floats per input text, in order.
        Texts must be non-empty."""
        ...

    async def extract_profile(self, sources: list[LLMSource]) -> tuple[LLMExtraction, Usage]: ...

    async def analyze_job(self, job: LLMJobInput) -> tuple[LLMJobAnalysis, Usage]: ...

    async def generate_documents(
        self, ctx: LLMGenerationContext, feedback: list[str] | None
    ) -> tuple[LLMGeneration, Usage]:
        """``feedback`` is None on the first pass; on the single allowed
        regeneration pass it lists the concrete validation failures to fix."""
        ...

    async def regenerate_item(
        self, ctx: LLMGenerationContext, target: LLMRegenTarget
    ) -> tuple[LLMRegenResult, Usage]: ...

    async def verify_claims(self, claims: list[LLMClaimCheck]) -> tuple[LLMVerification, Usage]: ...

    async def aclose(self) -> None:
        """Release network resources at shutdown."""
        ...
