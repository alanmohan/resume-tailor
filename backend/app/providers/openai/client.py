"""Thin wrapper around the OpenAI SDK. This is the only module that calls it.

Centralising the calls here means every request gets the same treatment: a
hard time limit, capped retries, ``store=False``, strict structured output,
translation of SDK exceptions into the application's ProviderError types, and
a usage log line that contains token counts but never prompt or response text.
"""

import asyncio
import json
import logging
import time
from collections.abc import Awaitable
from typing import Any

import openai
from openai import AsyncOpenAI
from pydantic import BaseModel, ValidationError

from app.logging_config import log_event
from app.providers.base import (
    ProviderError,
    ProviderInvalidOutput,
    ProviderNotConfigured,
    ProviderRateLimited,
    ProviderTimeout,
    ProviderUnavailable,
    Usage,
)

logger = logging.getLogger(__name__)

EMBEDDING_BATCH_SIZE = 64

# Failures that mean "the provider answered, but not with usable output".
_INVALID_OUTPUT_ERRORS = (
    ValidationError,
    json.JSONDecodeError,
    openai.LengthFinishReasonError,
    openai.ContentFilterFinishReasonError,
    openai.APIResponseValidationError,
)


def translate_sdk_error(error: Exception) -> ProviderError:
    """Map an SDK (or timeout/parsing) exception to a ProviderError with a
    message that is safe to show to users."""
    # APITimeoutError is a subclass of APIConnectionError, so it is checked first.
    if isinstance(error, (TimeoutError, openai.APITimeoutError)):
        return ProviderTimeout("The AI provider took too long to respond. Please try again.")
    if isinstance(error, openai.RateLimitError):
        return ProviderRateLimited(
            "The AI provider is rate limiting requests or out of quota. Please try again shortly."
        )
    if isinstance(error, openai.APIConnectionError):
        return ProviderUnavailable("Could not reach the AI provider. Please try again.")
    if isinstance(error, (openai.AuthenticationError, openai.PermissionDeniedError)):
        return ProviderUnavailable(
            "The AI provider rejected this server's credentials.", retryable=False
        )
    if isinstance(error, openai.APIStatusError):
        if error.status_code >= 500:
            return ProviderUnavailable(
                "The AI provider is temporarily unavailable. Please try again."
            )
        return ProviderUnavailable("The AI provider rejected the request.", retryable=False)
    if isinstance(error, _INVALID_OUTPUT_ERRORS):
        return ProviderInvalidOutput(
            "The AI provider returned an incomplete or invalid response. Please try again."
        )
    return ProviderUnavailable("The AI provider call failed. Please try again.")


class OpenAIClient:
    def __init__(
        self,
        *,
        api_key: str | None,
        model: str,
        embedding_model: str,
        embedding_dimension: int,
        reasoning_effort: str,
        timeout_seconds: float,
        max_retries: int,
        sdk_client: AsyncOpenAI | None = None,
    ) -> None:
        """``sdk_client`` lets tests pass a stub instead of a real AsyncOpenAI.
        Without an API key no SDK client is created and every call raises
        ProviderNotConfigured."""
        self.model = model
        self.embedding_model = embedding_model
        self.embedding_dimension = embedding_dimension
        self._reasoning_effort = reasoning_effort
        self._timeout_seconds = timeout_seconds
        self._sdk = sdk_client
        if self._sdk is None and api_key:
            # The SDK retries connection errors, 408, 409, 429 and 5xx responses
            # with exponential backoff, at most ``max_retries`` times.
            self._sdk = AsyncOpenAI(
                api_key=api_key, timeout=timeout_seconds, max_retries=max_retries
            )

    def _require_sdk(self) -> AsyncOpenAI:
        if self._sdk is None:
            raise ProviderNotConfigured()
        return self._sdk

    async def structured_call[OutputT: BaseModel](
        self,
        operation: str,
        instructions: str,
        input_text: str,
        output_model: type[OutputT],
        max_output_tokens: int,
    ) -> tuple[OutputT, Usage]:
        """Ask the generation model for output that must match ``output_model``.

        ``instructions`` holds the trusted prompt; ``input_text`` holds the
        untrusted data (as delimited JSON). They travel in separate request
        fields and are never concatenated. The Responses API enforces the JSON
        schema of ``output_model`` (strict structured output) and the SDK
        validates the result into that model.

        ``temperature`` / ``top_p`` are deliberately not sent: the configured
        models reject them. Reasoning tokens count against
        ``max_output_tokens``.
        """
        sdk = self._require_sdk()
        request: dict[str, Any] = {
            "model": self.model,
            "instructions": instructions,
            "input": input_text,
            "text_format": output_model,
            "max_output_tokens": max_output_tokens,
            "store": False,
        }
        if self._reasoning_effort:
            request["reasoning"] = {"effort": self._reasoning_effort}

        started = time.perf_counter()
        response = await self._send(operation, self.model, started, sdk.responses.parse(**request))
        usage = Usage(
            input_tokens=response.usage.input_tokens if response.usage else 0,
            output_tokens=response.usage.output_tokens if response.usage else 0,
            provider_calls=1,
        )
        problem = _output_problem(response)
        self._log_call(operation, self.model, started, usage, outcome=problem or "ok")
        if problem:
            raise ProviderInvalidOutput(_OUTPUT_PROBLEM_MESSAGES[problem])
        return response.output_parsed, usage

    async def embed(self, texts: list[str]) -> tuple[list[list[float]], Usage]:
        """Embed ``texts`` in batches; returns one vector per text, in order.

        Raises ValueError for blank input (the API rejects it) and
        ProviderInvalidOutput if a vector does not have the configured
        dimension, so mismatched vectors can never be stored.
        """
        if any(not text.strip() for text in texts):
            raise ValueError("texts to embed must not be blank")
        if not texts:
            return [], Usage()
        sdk = self._require_sdk()

        vectors: list[list[float]] = []
        usage = Usage()
        for first in range(0, len(texts), EMBEDDING_BATCH_SIZE):
            batch = texts[first : first + EMBEDDING_BATCH_SIZE]
            started = time.perf_counter()
            response = await self._send(
                "embed",
                self.embedding_model,
                started,
                sdk.embeddings.create(
                    model=self.embedding_model, input=batch, encoding_format="float"
                ),
            )
            batch_usage = Usage(embedding_tokens=response.usage.total_tokens, provider_calls=1)
            batch_vectors = [item.embedding for item in sorted(response.data, key=_by_index)]
            sizes_ok = len(batch_vectors) == len(batch) and all(
                len(vector) == self.embedding_dimension for vector in batch_vectors
            )
            outcome = "ok" if sizes_ok else "unexpected_size"
            self._log_call("embed", self.embedding_model, started, batch_usage, outcome=outcome)
            if not sizes_ok:
                raise ProviderInvalidOutput(
                    "The AI provider returned embeddings of an unexpected size."
                )
            vectors.extend(batch_vectors)
            usage += batch_usage
        return vectors, usage

    async def aclose(self) -> None:
        if self._sdk is not None:
            await self._sdk.close()

    async def _send[ResultT](
        self, operation: str, model: str, started: float, request: Awaitable[ResultT]
    ) -> ResultT:
        """Await one SDK call under an overall deadline (retries included) and
        translate any failure into a ProviderError."""
        try:
            async with asyncio.timeout(self._timeout_seconds):
                return await request
        except (openai.OpenAIError, TimeoutError, ValidationError, json.JSONDecodeError) as error:
            log_event(
                logger,
                logging.WARNING,
                "provider_call",
                operation=operation,
                model=model,
                outcome="error",
                error_type=type(error).__name__,
                # Short machine-readable codes only, e.g. 429 / "rate_limit_exceeded".
                status=getattr(error, "status_code", None),
                error_code=getattr(error, "code", None),
                duration_ms=round((time.perf_counter() - started) * 1000, 1),
            )
            raise translate_sdk_error(error) from error

    def _log_call(
        self, operation: str, model: str, started: float, usage: Usage, *, outcome: str
    ) -> None:
        log_event(
            logger,
            logging.INFO,
            "provider_call",
            operation=operation,
            model=model,
            outcome=outcome,
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            embedding_tokens=usage.embedding_tokens,
            duration_ms=round((time.perf_counter() - started) * 1000, 1),
        )


_OUTPUT_PROBLEM_MESSAGES = {
    "refusal": "The AI provider declined to process this content.",
    "incomplete": "The AI response was cut off before it finished. Please try again.",
    "empty": "The AI provider returned no usable output. Please try again.",
}


def _by_index(item: Any) -> int:
    return item.index


def _output_problem(response: Any) -> str | None:
    """Why a structured response cannot be used: "refusal", "incomplete",
    "empty", or None when it is fine."""
    for output in response.output or []:
        if output.type == "message" and any(
            part.type == "refusal" for part in output.content or []
        ):
            return "refusal"
    if response.status == "incomplete":
        return "incomplete"
    if response.output_parsed is None:
        return "empty"
    return None
