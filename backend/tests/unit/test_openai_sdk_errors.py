"""Provider failures seen through the real OpenAI SDK.

``test_openai_client.py`` replaces the SDK object with a stub. Here the SDK is
real and only its HTTP transport is replaced, so these tests also cover what
the SDK does before our code sees anything: which exception it raises for a
status code or a timeout, how often it retries, and how it parses a refusal, a
truncated answer or JSON that does not match the schema. Nothing touches the
network and no API key is needed.
"""

import json
from collections.abc import Callable
from typing import Any

import httpx2
import pytest
from openai import AsyncOpenAI

from app.providers.base import (
    LLMModel,
    ProviderError,
    ProviderInvalidOutput,
    ProviderRateLimited,
    ProviderTimeout,
    ProviderUnavailable,
    Usage,
)
from app.providers.openai.client import OpenAIClient

Handler = Callable[[httpx2.Request], httpx2.Response]


class Answer(LLMModel):
    value: str


class RecordingTransport:
    """Answers every request with the next queued result and keeps the JSON
    bodies it was sent. A queued exception is raised instead of answered."""

    def __init__(self, *results: httpx2.Response | Exception) -> None:
        self.results = list(results)
        self.bodies: list[dict[str, Any]] = []

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        self.bodies.append(json.loads(request.content))
        result = self.results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


def client_over(transport: Handler, *, max_retries: int = 0, **overrides: Any) -> OpenAIClient:
    """A real OpenAIClient around a real AsyncOpenAI whose HTTP calls go to
    ``transport`` instead of the network."""
    sdk = AsyncOpenAI(
        api_key="test-key",
        base_url="https://api.openai.test/v1",
        http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(transport)),
        max_retries=max_retries,
    )
    options: dict[str, Any] = {
        "api_key": None,
        "model": "gpt-6-luna",
        "embedding_model": "text-embedding-3-small",
        "embedding_dimension": 3,
        "reasoning_effort": "low",
        "timeout_seconds": 5,
        "max_retries": max_retries,
        "sdk_client": sdk,
    }
    options.update(overrides)
    return OpenAIClient(**options)


async def call(client: OpenAIClient) -> tuple[Answer, Usage]:
    return await client.structured_call(
        "generate_documents", "Trusted instructions", '{"data": "untrusted"}', Answer, 900
    )


# ---- Response payloads in the shape the Responses API returns ----------------------


def answered(*output: dict[str, Any], status: str = "completed") -> httpx2.Response:
    body = {
        "id": "resp_test",
        "object": "response",
        "created_at": 0,
        "model": "gpt-6-luna",
        "status": status,
        "incomplete_details": ({"reason": "max_output_tokens"} if status == "incomplete" else None),
        "output": list(output),
        "usage": {"input_tokens": 50, "output_tokens": 20, "total_tokens": 70},
    }
    return httpx2.Response(200, json=body)


def message(content: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "message",
        "id": "msg_test",
        "role": "assistant",
        "status": "completed",
        "content": [content],
    }


def output_text(text: str) -> dict[str, Any]:
    return message({"type": "output_text", "text": text, "annotations": []})


REASONING_ONLY = {"type": "reasoning", "id": "rs_test", "summary": []}


def rejected(status: int, code: str) -> httpx2.Response:
    """An error response; the header makes an SDK retry wait 1 ms, not seconds."""
    body = {"error": {"message": "provider detail", "type": code, "code": code}}
    return httpx2.Response(status, json=body, headers={"retry-after-ms": "1"})


# ---- The request on the wire -------------------------------------------------------


async def test_request_asks_for_strict_structured_output_and_sends_no_sampling_parameters() -> None:
    transport = RecordingTransport(answered(output_text('{"value": "ok"}')))
    parsed, usage = await call(client_over(transport))

    assert parsed == Answer(value="ok")
    assert usage == Usage(input_tokens=50, output_tokens=20, provider_calls=1)
    (body,) = transport.bodies
    assert body["model"] == "gpt-6-luna"
    assert body["instructions"] == "Trusted instructions"
    assert body["input"] == '{"data": "untrusted"}'
    assert (body["store"], body["max_output_tokens"]) == (False, 900)
    assert body["reasoning"] == {"effort": "low"}
    output_format = body["text"]["format"]
    assert (output_format["type"], output_format["strict"]) == ("json_schema", True)
    assert output_format["schema"]["additionalProperties"] is False
    assert output_format["schema"]["required"] == ["value"]
    # gpt-6-luna rejects sampling parameters, so they must never be sent.
    assert "temperature" not in body and "top_p" not in body


async def test_empty_reasoning_effort_leaves_the_parameter_out_of_the_request() -> None:
    transport = RecordingTransport(answered(output_text('{"value": "ok"}')))
    await call(client_over(transport, reasoning_effort=""))
    assert "reasoning" not in transport.bodies[0]


# ---- Transport and status failures -------------------------------------------------


@pytest.mark.parametrize(
    ("result", "expected", "retryable"),
    [
        (httpx2.ReadTimeout("timed out"), ProviderTimeout, True),
        (httpx2.ConnectError("no route"), ProviderUnavailable, True),
        (rejected(429, "rate_limit_exceeded"), ProviderRateLimited, True),
        (rejected(429, "insufficient_quota"), ProviderRateLimited, True),
        (rejected(500, "server_error"), ProviderUnavailable, True),
        (rejected(503, "overloaded"), ProviderUnavailable, True),
        (rejected(400, "unsupported_parameter"), ProviderUnavailable, False),
        (rejected(401, "invalid_api_key"), ProviderUnavailable, False),
        (rejected(404, "model_not_found"), ProviderUnavailable, False),
    ],
)
async def test_http_failures_become_provider_errors(
    result: httpx2.Response | Exception, expected: type[ProviderError], retryable: bool
) -> None:
    with pytest.raises(ProviderError) as error:
        await call(client_over(RecordingTransport(result)))
    assert type(error.value) is expected
    assert error.value.retryable is retryable
    # The message shown to users is ours and never repeats the provider's text.
    assert "provider detail" not in error.value.message


async def test_a_rate_limited_call_is_retried_and_can_succeed() -> None:
    transport = RecordingTransport(
        rejected(429, "rate_limit_exceeded"), answered(output_text('{"value": "second try"}'))
    )
    parsed, _ = await call(client_over(transport, max_retries=2))
    assert parsed.value == "second try"
    assert len(transport.bodies) == 2


async def test_retries_are_capped() -> None:
    transport = RecordingTransport(*(rejected(500, "server_error") for _ in range(5)))
    with pytest.raises(ProviderUnavailable):
        await call(client_over(transport, max_retries=2))
    assert len(transport.bodies) == 3  # the first attempt and two retries, then give up


async def test_a_rejected_request_is_not_retried() -> None:
    transport = RecordingTransport(rejected(400, "unsupported_parameter"), answered())
    with pytest.raises(ProviderUnavailable):
        await call(client_over(transport, max_retries=2))
    assert len(transport.bodies) == 1


# ---- Answers that cannot be used ---------------------------------------------------


async def test_a_refusal_is_invalid_output() -> None:
    refusal = message({"type": "refusal", "refusal": "I cannot help with that."})
    with pytest.raises(ProviderInvalidOutput) as error:
        await call(client_over(RecordingTransport(answered(refusal))))
    assert "declined" in error.value.message
    assert "cannot help" not in error.value.message


async def test_an_answer_cut_off_at_the_output_limit_is_invalid_output() -> None:
    """max_output_tokens was reached while the JSON was being written."""
    truncated = answered(REASONING_ONLY, output_text('{"value": "half an ans'), status="incomplete")
    with pytest.raises(ProviderInvalidOutput) as error:
        await call(client_over(RecordingTransport(truncated)))
    assert error.value.retryable is True


async def test_a_response_that_spent_its_whole_limit_on_reasoning_is_invalid_output() -> None:
    """Reasoning tokens count against max_output_tokens: no message at all."""
    response = answered(REASONING_ONLY, status="incomplete")
    with pytest.raises(ProviderInvalidOutput) as error:
        await call(client_over(RecordingTransport(response)))
    assert "cut off" in error.value.message


@pytest.mark.parametrize(
    "text",
    [
        '{"value": 7}',  # wrong type
        '{"other": "field"}',  # required field missing
        '{"value": "ok", "extra": "field"}',  # field outside the schema
        "not json at all",
    ],
)
async def test_output_that_does_not_match_the_schema_is_invalid_output(text: str) -> None:
    with pytest.raises(ProviderInvalidOutput) as error:
        await call(client_over(RecordingTransport(answered(output_text(text)))))
    assert "invalid" in error.value.message
    assert text not in error.value.message


async def test_a_completed_response_without_a_message_is_invalid_output() -> None:
    with pytest.raises(ProviderInvalidOutput):
        await call(client_over(RecordingTransport(answered(REASONING_ONLY))))


# ---- Embeddings --------------------------------------------------------------------


async def test_embedding_failures_are_translated_too() -> None:
    client = client_over(RecordingTransport(rejected(429, "rate_limit_exceeded")))
    with pytest.raises(ProviderRateLimited):
        await client.embed(["some text"])


async def test_embeddings_of_another_dimension_are_refused() -> None:
    body = {
        "object": "list",
        "model": "text-embedding-3-small",
        "data": [{"object": "embedding", "index": 0, "embedding": [0.1, 0.2]}],
        "usage": {"prompt_tokens": 2, "total_tokens": 2},
    }
    client = client_over(RecordingTransport(httpx2.Response(200, json=body)))
    with pytest.raises(ProviderInvalidOutput):
        await client.embed(["some text"])
