"""Deterministic, rule-based document generation, single-item regeneration and
claim verification for the fake provider (tests, end-to-end tests and demo mode).

These are pure functions: no randomness, no network, no clock. FakeProvider
adds call counting, failure injection and usage accounting around them. The
signatures are final.
"""

from app.providers.base import (
    LLMClaimCheck,
    LLMGeneration,
    LLMGenerationContext,
    LLMRegenResult,
    LLMRegenTarget,
    LLMVerification,
)


def generate_documents(ctx: LLMGenerationContext, feedback: list[str] | None) -> LLMGeneration:
    raise NotImplementedError("generate_documents is implemented by the generation feature")


def regenerate_item(ctx: LLMGenerationContext, target: LLMRegenTarget) -> LLMRegenResult:
    raise NotImplementedError("regenerate_item is implemented by the generation feature")


def verify_claims(claims: list[LLMClaimCheck]) -> LLMVerification:
    raise NotImplementedError("verify_claims is implemented by the generation feature")
