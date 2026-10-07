"""Deterministic fake provider for tests, end-to-end tests and demo mode.

It is only built when AI_PROVIDER=fake is set explicitly (never in
production) and the API reports ``provider_mode: "fake"`` so the UI can show a
"Demo mode" banner. It is not a fallback: a failing real provider is never
replaced by fake output.

Test hooks:

    fake_provider.calls["generate_documents"]          # calls made so far
    fake_provider.fail_next("embed", ProviderTimeout("timed out"))
    fake_provider.set_delay("generate_documents", 0.5)  # slow in-flight work
    fake_provider.reset()
"""

import asyncio
from collections import Counter, defaultdict, deque

from pydantic import BaseModel

from app.providers.base import (
    LLMClaimCheck,
    LLMExtraction,
    LLMGeneration,
    LLMGenerationContext,
    LLMJobAnalysis,
    LLMJobInput,
    LLMRegenResult,
    LLMRegenTarget,
    LLMSource,
    LLMVerification,
    Usage,
)
from app.providers.fake import extraction_ops, generation_ops
from app.providers.fake.embeddings import (
    FAKE_EMBEDDING_DIMENSION,
    FAKE_EMBEDDING_MODEL,
    fake_embedding,
)
from app.schemas.common import ProviderMode
from app.services.textutil import estimate_tokens

OPERATIONS = (
    "embed",
    "extract_profile",
    "analyze_job",
    "generate_documents",
    "regenerate_item",
    "verify_claims",
)


def _estimated_usage(inputs: list[BaseModel], output: BaseModel) -> Usage:
    """Plausible token counts derived from the JSON size, so usage totals are
    non-zero and repeatable in fake mode."""
    input_tokens = sum(estimate_tokens(item.model_dump_json()) for item in inputs)
    return Usage(
        input_tokens=input_tokens,
        output_tokens=estimate_tokens(output.model_dump_json()),
        provider_calls=1,
    )


class FakeProvider:
    """Implements AIProvider with rule-based operations and hashed embeddings."""

    mode: ProviderMode = "fake"
    generation_model = "fake-llm-1"
    embedding_model = FAKE_EMBEDDING_MODEL
    embedding_dimension = FAKE_EMBEDDING_DIMENSION

    def __init__(self) -> None:
        # Number of times each operation was called, including failed calls.
        self.calls: Counter[str] = Counter()
        self._failures: defaultdict[str, deque[Exception]] = defaultdict(deque)
        self._delays: dict[str, float] = {}

    # ---- Test hooks ----------------------------------------------------------

    def fail_next(self, operation: str, *errors: Exception) -> None:
        """Queue errors: each of the next calls of ``operation`` raises one of
        them, in order, instead of producing output."""
        self._check_operation(operation)
        self._failures[operation].extend(errors)

    def set_delay(self, operation: str, seconds: float | None) -> None:
        """Make every call of ``operation`` wait first; None removes the delay."""
        self._check_operation(operation)
        if seconds is None:
            self._delays.pop(operation, None)
        else:
            self._delays[operation] = seconds

    def reset(self) -> None:
        """Clear call counters, queued failures and delays."""
        self.calls.clear()
        self._failures.clear()
        self._delays.clear()

    @staticmethod
    def _check_operation(operation: str) -> None:
        if operation not in OPERATIONS:
            raise ValueError(f"unknown provider operation: {operation!r}")

    async def _begin(self, operation: str) -> None:
        """Count the call, apply the configured delay, then raise a queued failure."""
        self.calls[operation] += 1
        if operation in self._delays:
            await asyncio.sleep(self._delays[operation])
        if self._failures[operation]:
            raise self._failures[operation].popleft()

    # ---- AIProvider ----------------------------------------------------------

    async def embed(self, texts: list[str]) -> tuple[list[list[float]], Usage]:
        if any(not text.strip() for text in texts):
            raise ValueError("texts to embed must not be blank")
        if not texts:
            return [], Usage()
        await self._begin("embed")
        tokens = sum(estimate_tokens(text) for text in texts)
        vectors = [fake_embedding(text) for text in texts]
        return vectors, Usage(embedding_tokens=tokens, provider_calls=1)

    async def extract_profile(self, sources: list[LLMSource]) -> tuple[LLMExtraction, Usage]:
        await self._begin("extract_profile")
        result = extraction_ops.extract_profile(sources)
        return result, _estimated_usage(list(sources), result)

    async def analyze_job(self, job: LLMJobInput) -> tuple[LLMJobAnalysis, Usage]:
        await self._begin("analyze_job")
        result = extraction_ops.analyze_job(job)
        return result, _estimated_usage([job], result)

    async def generate_documents(
        self, ctx: LLMGenerationContext, feedback: list[str] | None
    ) -> tuple[LLMGeneration, Usage]:
        await self._begin("generate_documents")
        result = generation_ops.generate_documents(ctx, feedback)
        return result, _estimated_usage([ctx], result)

    async def regenerate_item(
        self, ctx: LLMGenerationContext, target: LLMRegenTarget
    ) -> tuple[LLMRegenResult, Usage]:
        await self._begin("regenerate_item")
        result = generation_ops.regenerate_item(ctx, target)
        return result, _estimated_usage([ctx, target], result)

    async def verify_claims(self, claims: list[LLMClaimCheck]) -> tuple[LLMVerification, Usage]:
        await self._begin("verify_claims")
        result = generation_ops.verify_claims(claims)
        return result, _estimated_usage(list(claims), result)

    async def aclose(self) -> None:
        """Nothing to release."""
