"""Provider interface pieces: Usage, the fake provider's hooks and embeddings,
the factory, and the strict-schema rules for LLM-facing models."""

import asyncio
import math
from typing import Any

import pytest
from pydantic import BaseModel

from app.providers import base
from app.providers.base import (
    LLMClaimCheck,
    LLMExtraction,
    LLMGeneration,
    LLMGenerationContext,
    LLMJobAnalysis,
    LLMJobInput,
    LLMModel,
    LLMRegenResult,
    LLMRegenTarget,
    LLMSource,
    LLMVerification,
    ProviderError,
    ProviderInvalidOutput,
    ProviderNotConfigured,
    ProviderRateLimited,
    ProviderTimeout,
    ProviderUnavailable,
    Usage,
)
from app.providers.factory import build_provider
from app.providers.fake.embeddings import fake_embedding
from app.providers.fake.provider import OPERATIONS, FakeProvider
from app.providers.openai.provider import OpenAIProvider
from tests.conftest import build_test_settings


def cosine(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b, strict=True))


# ---- Usage and errors --------------------------------------------------------------


def test_usage_values_add_field_by_field() -> None:
    total = Usage(input_tokens=10, output_tokens=5, provider_calls=1) + Usage(
        input_tokens=1, embedding_tokens=7, provider_calls=2
    )
    assert total == Usage(input_tokens=11, output_tokens=5, embedding_tokens=7, provider_calls=3)
    assert Usage() + Usage() == Usage()


def test_provider_errors_carry_code_message_and_retryable_flag() -> None:
    cases = [
        (ProviderTimeout("slow"), "provider_timeout", True),
        (ProviderRateLimited("busy"), "provider_rate_limited", True),
        (ProviderUnavailable("down"), "provider_unavailable", True),
        (ProviderInvalidOutput("bad"), "provider_invalid_output", True),
        (ProviderUnavailable("rejected", retryable=False), "provider_unavailable", False),
        (ProviderNotConfigured(), "provider_unavailable", False),
    ]
    for error, code, retryable in cases:
        assert isinstance(error, ProviderError)
        assert error.code == code
        assert error.retryable is retryable
        assert error.message == str(error)


# ---- Fake embeddings ---------------------------------------------------------------


def test_fake_embedding_is_deterministic_normalised_and_256_wide() -> None:
    vector = fake_embedding("Built data pipelines with Python and Airflow")
    assert vector == fake_embedding("Built data pipelines with Python and Airflow")
    assert len(vector) == 256
    assert math.isclose(math.sqrt(sum(value * value for value in vector)), 1.0)


def test_fake_embedding_of_text_without_tokens_is_the_zero_vector() -> None:
    assert fake_embedding("") == [0.0] * 256
    assert fake_embedding("... !!!") == [0.0] * 256


def test_fake_embeddings_rank_related_text_above_unrelated_text() -> None:
    query = fake_embedding("Python data pipelines")
    related = fake_embedding("Built data pipelines in Python for analytics")
    unrelated = fake_embedding("Organised the annual charity bake sale")
    assert cosine(query, related) > cosine(query, unrelated)


# ---- FakeProvider ------------------------------------------------------------------


async def test_fake_provider_embeds_and_counts_calls() -> None:
    provider = FakeProvider()
    vectors, usage = await provider.embed(["first text", "second text"])
    assert vectors == [fake_embedding("first text"), fake_embedding("second text")]
    assert usage.provider_calls == 1
    assert usage.embedding_tokens > 0
    assert provider.calls["embed"] == 1
    assert provider.embedding_dimension == 256
    assert provider.embedding_model == "fake-embedding-256"
    assert provider.mode == "fake"


async def test_fake_provider_embedding_edge_cases() -> None:
    provider = FakeProvider()
    assert await provider.embed([]) == ([], Usage())
    assert provider.calls["embed"] == 0
    with pytest.raises(ValueError):
        await provider.embed(["ok", "   "])


async def test_queued_failures_are_raised_in_order_then_calls_succeed() -> None:
    provider = FakeProvider()
    provider.fail_next("embed", ProviderTimeout("timed out"), ProviderRateLimited("429"))

    with pytest.raises(ProviderTimeout):
        await provider.embed(["text"])
    with pytest.raises(ProviderRateLimited):
        await provider.embed(["text"])
    vectors, _ = await provider.embed(["text"])

    assert len(vectors) == 1
    # Failed attempts are counted too: they are real calls to the provider.
    assert provider.calls["embed"] == 3


async def test_failures_only_affect_the_named_operation() -> None:
    provider = FakeProvider()
    provider.fail_next("generate_documents", ProviderUnavailable("down"))
    vectors, _ = await provider.embed(["text"])
    assert len(vectors) == 1


async def test_delay_makes_a_call_slow_until_removed() -> None:
    provider = FakeProvider()
    provider.set_delay("embed", 0.05)
    loop = asyncio.get_running_loop()

    started = loop.time()
    await provider.embed(["text"])
    assert loop.time() - started >= 0.045

    provider.set_delay("embed", None)
    started = loop.time()
    await provider.embed(["text"])
    assert loop.time() - started < 0.04


async def test_reset_clears_counters_failures_and_delays() -> None:
    provider = FakeProvider()
    await provider.embed(["text"])
    provider.fail_next("embed", ProviderTimeout("timed out"))
    provider.set_delay("embed", 5)
    provider.reset()

    await asyncio.wait_for(provider.embed(["text"]), timeout=1)
    assert provider.calls["embed"] == 1


def test_unknown_operation_names_are_rejected() -> None:
    provider = FakeProvider()
    with pytest.raises(ValueError):
        provider.fail_next("generate", ProviderTimeout("x"))
    with pytest.raises(ValueError):
        provider.set_delay("embedding", 1)
    assert set(OPERATIONS) == {
        "embed",
        "extract_profile",
        "analyze_job",
        "generate_documents",
        "regenerate_item",
        "verify_claims",
    }


# ---- Factory -----------------------------------------------------------------------


def test_factory_builds_the_fake_provider_only_when_requested() -> None:
    assert isinstance(build_provider(build_test_settings(ai_provider="fake")), FakeProvider)
    real = build_provider(build_test_settings(ai_provider="openai", openai_model="gpt-5.6-terra"))
    assert isinstance(real, OpenAIProvider)
    assert real.mode == "openai"
    assert real.generation_model == "gpt-5.6-terra"
    assert real.embedding_model == "text-embedding-3-small"
    assert real.embedding_dimension == 1536


async def test_openai_provider_without_a_key_reports_not_configured() -> None:
    provider = build_provider(build_test_settings(ai_provider="openai", openai_api_key=None))
    with pytest.raises(ProviderNotConfigured) as error:
        await provider.embed(["text"])
    assert error.value.retryable is False
    await provider.aclose()


# ---- Strict structured-output compatibility ----------------------------------------

OUTPUT_MODELS = [LLMExtraction, LLMJobAnalysis, LLMGeneration, LLMRegenResult, LLMVerification]
INPUT_MODELS = [LLMSource, LLMJobInput, LLMGenerationContext, LLMRegenTarget, LLMClaimCheck]


def object_schemas(schema: dict[str, Any]) -> list[dict[str, Any]]:
    """Every object schema in a JSON schema document, including $defs."""
    found = []
    if schema.get("type") == "object":
        found.append(schema)
    for definition in schema.get("$defs", {}).values():
        found.extend(object_schemas(definition))
    return found


@pytest.mark.parametrize("model", OUTPUT_MODELS + INPUT_MODELS)
def test_llm_models_satisfy_strict_schema_rules(model: type[BaseModel]) -> None:
    """Strict structured outputs need every property required and no extra keys."""
    assert issubclass(model, LLMModel)
    schemas = object_schemas(model.model_json_schema())
    assert schemas
    for schema in schemas:
        properties = schema.get("properties", {})
        assert properties, f"{schema.get('title')} has no fixed properties"
        assert set(schema.get("required", [])) == set(properties), schema.get("title")
        assert schema.get("additionalProperties") is False, schema.get("title")


def test_every_llm_model_is_exported_from_base() -> None:
    exported = {name for name in base.__all__ if name.startswith("LLM")}
    defined = {
        name
        for name, value in vars(base).items()
        if isinstance(value, type) and issubclass(value, LLMModel)
    }
    assert exported == defined
