"""OpenAIClient behaviour, tested with a stub in place of the SDK client.
Nothing here touches the network or needs an API key."""

import asyncio
from types import SimpleNamespace
from typing import Any

import httpx
import openai
import pytest
from pydantic import ValidationError

from app.providers.base import (
    LLMModel,
    ProviderError,
    ProviderInvalidOutput,
    ProviderNotConfigured,
    ProviderRateLimited,
    ProviderTimeout,
    ProviderUnavailable,
    Usage,
)
from app.providers.openai.client import EMBEDDING_BATCH_SIZE, OpenAIClient

REQUEST = httpx.Request("POST", "https://api.openai.com/v1/responses")


class Answer(LLMModel):
    value: str


def status_error(error_class: type[openai.APIStatusError], status: int) -> openai.APIStatusError:
    return error_class(
        "provider said no", response=httpx.Response(status, request=REQUEST), body=None
    )


def completed(parsed: Any, *, input_tokens: int = 120, output_tokens: int = 30) -> SimpleNamespace:
    """The parts of an SDK ParsedResponse that the client reads."""
    return SimpleNamespace(
        status="completed",
        output=[SimpleNamespace(type="message", content=[SimpleNamespace(type="output_text")])],
        output_parsed=parsed,
        usage=SimpleNamespace(input_tokens=input_tokens, output_tokens=output_tokens),
    )


class StubSDK:
    """Stands in for AsyncOpenAI: records requests and replays queued results."""

    def __init__(self, *results: Any, dimension: int = 4, delay: float = 0) -> None:
        self.results = list(results)
        self.dimension = dimension
        self.delay = delay
        self.parse_requests: list[dict[str, Any]] = []
        self.embedding_requests: list[dict[str, Any]] = []
        self.closed = False
        self.responses = SimpleNamespace(parse=self._parse)
        self.embeddings = SimpleNamespace(create=self._create_embeddings)

    async def _parse(self, **request: Any) -> Any:
        self.parse_requests.append(request)
        await asyncio.sleep(self.delay)
        result = self.results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result

    async def _create_embeddings(self, **request: Any) -> Any:
        self.embedding_requests.append(request)
        if self.results:
            raise self.results.pop(0)
        batch = request["input"]
        # Returned out of order on purpose: the client must sort by index.
        data = [
            SimpleNamespace(index=index, embedding=[float(len(text))] * self.dimension)
            for index, text in reversed(list(enumerate(batch)))
        ]
        return SimpleNamespace(data=data, usage=SimpleNamespace(total_tokens=len(batch) * 3))

    async def close(self) -> None:
        self.closed = True


def make_client(sdk: StubSDK | None, **overrides: Any) -> OpenAIClient:
    options: dict[str, Any] = {
        "api_key": None,
        "model": "gpt-6-luna",
        "embedding_model": "text-embedding-3-small",
        "embedding_dimension": 4,
        "reasoning_effort": "low",
        "timeout_seconds": 5,
        "max_retries": 0,
        "sdk_client": sdk,
    }
    options.update(overrides)
    return OpenAIClient(**options)


async def call(client: OpenAIClient) -> tuple[Answer, Usage]:
    return await client.structured_call(
        "extract_profile", "Trusted instructions", '{"data": "untrusted"}', Answer, 900
    )


# ---- structured_call ---------------------------------------------------------------


async def test_structured_call_returns_the_parsed_model_and_usage() -> None:
    sdk = StubSDK(completed(Answer(value="ok")))
    parsed, usage = await call(make_client(sdk))
    assert parsed == Answer(value="ok")
    assert usage == Usage(input_tokens=120, output_tokens=30, provider_calls=1)


async def test_structured_call_sends_a_strict_bounded_unstored_request() -> None:
    sdk = StubSDK(completed(Answer(value="ok")))
    await call(make_client(sdk))
    request = sdk.parse_requests[0]
    assert request == {
        "model": "gpt-6-luna",
        "instructions": "Trusted instructions",
        "input": '{"data": "untrusted"}',
        "text_format": Answer,
        "max_output_tokens": 900,
        "store": False,
        "reasoning": {"effort": "low"},
    }
    # The configured models reject sampling parameters.
    assert "temperature" not in request
    assert "top_p" not in request


async def test_reasoning_parameter_is_omitted_when_effort_is_empty() -> None:
    sdk = StubSDK(completed(Answer(value="ok")))
    await call(make_client(sdk, reasoning_effort=""))
    assert "reasoning" not in sdk.parse_requests[0]


async def test_refusal_is_reported_as_invalid_output() -> None:
    response = completed(None)
    response.output = [SimpleNamespace(type="message", content=[SimpleNamespace(type="refusal")])]
    with pytest.raises(ProviderInvalidOutput) as error:
        await call(make_client(StubSDK(response)))
    assert "declined" in error.value.message


async def test_incomplete_response_is_reported_as_invalid_output() -> None:
    response = completed(None)
    response.status = "incomplete"
    response.output = [SimpleNamespace(type="reasoning")]
    with pytest.raises(ProviderInvalidOutput) as error:
        await call(make_client(StubSDK(response)))
    assert "cut off" in error.value.message


async def test_response_without_parsed_output_is_reported_as_invalid_output() -> None:
    with pytest.raises(ProviderInvalidOutput):
        await call(make_client(StubSDK(completed(None))))


def schema_violation() -> ValidationError:
    try:
        Answer.model_validate({"value": 1, "resume_text": "Jordan Rivera"})
    except ValidationError as error:
        return error
    raise AssertionError("expected a validation error")


@pytest.mark.parametrize(
    ("sdk_error", "expected", "retryable"),
    [
        (openai.APITimeoutError(request=REQUEST), ProviderTimeout, True),
        (status_error(openai.RateLimitError, 429), ProviderRateLimited, True),
        (openai.APIConnectionError(request=REQUEST), ProviderUnavailable, True),
        (status_error(openai.InternalServerError, 500), ProviderUnavailable, True),
        (status_error(openai.AuthenticationError, 401), ProviderUnavailable, False),
        (status_error(openai.PermissionDeniedError, 403), ProviderUnavailable, False),
        (status_error(openai.BadRequestError, 400), ProviderUnavailable, False),
        (schema_violation(), ProviderInvalidOutput, True),
    ],
)
async def test_sdk_failures_are_translated(
    sdk_error: Exception, expected: type[ProviderError], retryable: bool
) -> None:
    with pytest.raises(ProviderError) as error:
        await call(make_client(StubSDK(sdk_error)))
    assert type(error.value) is expected
    assert error.value.retryable is retryable
    assert error.value.__cause__ is sdk_error
    # The user-facing message is ours; it never repeats provider or input text.
    assert "provider said no" not in error.value.message
    assert "Jordan" not in error.value.message


async def test_a_call_that_exceeds_the_deadline_times_out() -> None:
    sdk = StubSDK(completed(Answer(value="late")), delay=0.5)
    with pytest.raises(ProviderTimeout):
        await call(make_client(sdk, timeout_seconds=0.02))


async def test_unexpected_exceptions_are_not_disguised_as_provider_errors() -> None:
    with pytest.raises(KeyError):
        await call(make_client(StubSDK(KeyError("bug in our own code"))))


async def test_calls_without_an_api_key_raise_not_configured() -> None:
    client = make_client(None)
    with pytest.raises(ProviderNotConfigured):
        await call(client)
    with pytest.raises(ProviderNotConfigured):
        await client.embed(["text"])


# ---- embed -------------------------------------------------------------------------


async def test_embed_batches_requests_and_keeps_input_order() -> None:
    texts = [("x" * (index + 1)) for index in range(EMBEDDING_BATCH_SIZE * 2 + 2)]
    sdk = StubSDK()
    vectors, usage = await make_client(sdk).embed(texts)

    assert [len(request["input"]) for request in sdk.embedding_requests] == [
        EMBEDDING_BATCH_SIZE,
        EMBEDDING_BATCH_SIZE,
        2,
    ]
    assert all(request["model"] == "text-embedding-3-small" for request in sdk.embedding_requests)
    # The stub encodes each text's length in its vector, which proves the order.
    assert [vector[0] for vector in vectors] == [float(len(text)) for text in texts]
    assert usage == Usage(embedding_tokens=len(texts) * 3, provider_calls=3)


async def test_embed_with_no_texts_makes_no_call() -> None:
    sdk = StubSDK()
    assert await make_client(sdk).embed([]) == ([], Usage())
    assert sdk.embedding_requests == []


async def test_embed_rejects_blank_text() -> None:
    with pytest.raises(ValueError):
        await make_client(StubSDK()).embed(["fine", " \n"])


async def test_embed_rejects_vectors_of_the_wrong_dimension() -> None:
    sdk = StubSDK(dimension=3)
    with pytest.raises(ProviderInvalidOutput) as error:
        await make_client(sdk, embedding_dimension=4).embed(["text"])
    assert "unexpected size" in error.value.message


async def test_embed_translates_sdk_failures() -> None:
    sdk = StubSDK(status_error(openai.RateLimitError, 429))
    with pytest.raises(ProviderRateLimited):
        await make_client(sdk).embed(["text"])


async def test_aclose_closes_the_sdk_client() -> None:
    sdk = StubSDK()
    await make_client(sdk).aclose()
    assert sdk.closed is True
    await make_client(None).aclose()


# ---- usage logging -----------------------------------------------------------------


async def test_usage_is_logged_without_prompt_or_response_content(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level("INFO", logger="app.providers.openai.client")
    await call(make_client(StubSDK(completed(Answer(value="private answer")))))

    records = [record for record in caplog.records if record.getMessage() == "provider_call"]
    assert len(records) == 1
    fields = records[0].fields
    assert fields["operation"] == "extract_profile"
    assert fields["model"] == "gpt-6-luna"
    assert fields["outcome"] == "ok"
    assert fields["input_tokens"] == 120
    assert fields["output_tokens"] == 30
    logged = repr(fields)
    assert "Trusted instructions" not in logged
    assert "untrusted" not in logged
    assert "private answer" not in logged


async def test_failed_calls_log_the_error_type_but_not_the_provider_message(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level("INFO", logger="app.providers.openai.client")
    with pytest.raises(ProviderRateLimited):
        await call(make_client(StubSDK(status_error(openai.RateLimitError, 429))))

    fields = caplog.records[-1].fields
    assert fields["outcome"] == "error"
    assert fields["error_type"] == "RateLimitError"
    assert fields["status"] == 429
    assert "provider said no" not in repr(fields)
