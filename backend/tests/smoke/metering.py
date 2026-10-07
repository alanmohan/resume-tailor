"""Measuring what the real provider does during a smoke run.

``MeteredProvider`` wraps the provider the application was built with. It
times every call and keeps what went in and what came out, so the tests can
report latency and token counts per operation and compare the model's raw
answer with what the server made of it.

It never answers on the provider's behalf. A provider error passes through
unchanged, so a failed real call fails the test that needed it.
"""

import json
import time
from collections.abc import Awaitable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from app.providers.base import (
    AIProvider,
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


@dataclass(frozen=True)
class ProviderCall:
    """One successful provider call."""

    flow: str  # which part of the smoke run made the call, e.g. "A"
    operation: str
    seconds: float
    usage: Usage
    request: Any
    result: Any


class MeteredProvider:
    """Implements AIProvider by passing every call to ``inner`` and recording it."""

    def __init__(self, inner: AIProvider) -> None:
        self._inner = inner
        self.mode = inner.mode
        self.generation_model = inner.generation_model
        self.embedding_model = inner.embedding_model
        self.embedding_dimension = inner.embedding_dimension
        self.calls: list[ProviderCall] = []
        # Set by the test fixtures before each step, so calls can be told apart.
        self.flow = "-"

    async def _metered[ResultT](
        self, operation: str, request: Any, call: Awaitable[tuple[ResultT, Usage]]
    ) -> tuple[ResultT, Usage]:
        started = time.perf_counter()
        result, usage = await call
        seconds = time.perf_counter() - started
        self.calls.append(ProviderCall(self.flow, operation, seconds, usage, request, result))
        return result, usage

    async def embed(self, texts: list[str]) -> tuple[list[list[float]], Usage]:
        return await self._metered("embed", texts, self._inner.embed(texts))

    async def extract_profile(self, sources: list[LLMSource]) -> tuple[LLMExtraction, Usage]:
        return await self._metered("extract_profile", sources, self._inner.extract_profile(sources))

    async def analyze_job(self, job: LLMJobInput) -> tuple[LLMJobAnalysis, Usage]:
        return await self._metered("analyze_job", job, self._inner.analyze_job(job))

    async def generate_documents(
        self, ctx: LLMGenerationContext, feedback: list[str] | None
    ) -> tuple[LLMGeneration, Usage]:
        request = {"context": ctx, "validation_feedback": feedback}
        return await self._metered(
            "generate_documents", request, self._inner.generate_documents(ctx, feedback)
        )

    async def regenerate_item(
        self, ctx: LLMGenerationContext, target: LLMRegenTarget
    ) -> tuple[LLMRegenResult, Usage]:
        request = {"context": ctx, "target": target}
        return await self._metered(
            "regenerate_item", request, self._inner.regenerate_item(ctx, target)
        )

    async def verify_claims(self, claims: list[LLMClaimCheck]) -> tuple[LLMVerification, Usage]:
        return await self._metered("verify_claims", claims, self._inner.verify_claims(claims))

    async def aclose(self) -> None:
        await self._inner.aclose()

    def results_of(self, flow: str, operation: str) -> list[Any]:
        """What the provider returned for one operation of one flow, in call order."""
        return [
            call.result for call in self.calls if (call.flow, call.operation) == (flow, operation)
        ]


def _plain(value: Any) -> Any:
    """``value`` as JSON-serialisable data (models become dictionaries)."""
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, dict):
        return {key: _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    return value


def write_transcript(calls: list[ProviderCall], directory: Path) -> None:
    """Save every call's input and output as one JSON file per call, for
    reading the model's raw answers after a run. The smoke tests send only the
    fictional fixtures, so a transcript holds no personal data; still, write it
    outside the repository."""
    directory.mkdir(parents=True, exist_ok=True)
    for number, call in enumerate(calls, start=1):
        document = {
            "flow": call.flow,
            "operation": call.operation,
            "seconds": round(call.seconds, 3),
            "usage": vars(call.usage),
            "request": _plain(call.request),
            "result": _plain(call.result),
        }
        path = directory / f"{number:02d}_{call.flow}_{call.operation}.json"
        path.write_text(json.dumps(document, ensure_ascii=False, indent=1), encoding="utf-8")
