"""OpenAI implementations of document generation, single-item regeneration and
optional semantic verification.

Each function makes one ``client.structured_call`` with two separate parts:

- ``instructions``: trusted text only, read from the version-controlled prompt
  templates (the shared untrusted-data rules followed by the task prompt).
  Nothing from a user ever goes into it.
- ``input_text``: one JSON document holding all untrusted material (evidence,
  job text, the applicant's style preference, validator feedback that quotes
  the model's earlier draft). JSON encoding escapes quotes and line breaks, so
  text inside a field cannot end the document or pose as a new section.

The signatures are final; OpenAIProvider calls them.
"""

import json
from typing import Any

from app.prompts import load_prompt
from app.providers.base import (
    LLMClaimCheck,
    LLMGeneration,
    LLMGenerationContext,
    LLMRegenResult,
    LLMRegenTarget,
    LLMVerification,
    Usage,
)
from app.providers.openai.client import OpenAIClient


def build_instructions(prompt_name: str) -> str:
    """The trusted instructions for one operation."""
    return f"{load_prompt('untrusted_data')}\n\n{load_prompt(prompt_name)}"


def build_input(payload: dict[str, Any]) -> str:
    """The untrusted data for one operation as a single JSON document."""
    return json.dumps(payload, ensure_ascii=False)


async def generate_documents(
    client: OpenAIClient,
    ctx: LLMGenerationContext,
    feedback: list[str] | None,
    max_output_tokens: int,
) -> tuple[LLMGeneration, Usage]:
    payload = {"context": ctx.model_dump(mode="json"), "validation_feedback": feedback}
    return await client.structured_call(
        "generate_documents",
        build_instructions("generation"),
        build_input(payload),
        LLMGeneration,
        max_output_tokens,
    )


async def regenerate_item(
    client: OpenAIClient,
    ctx: LLMGenerationContext,
    target: LLMRegenTarget,
    max_output_tokens: int,
) -> tuple[LLMRegenResult, Usage]:
    payload = {"context": ctx.model_dump(mode="json"), "target": target.model_dump(mode="json")}
    return await client.structured_call(
        "regenerate_item",
        build_instructions("regeneration"),
        build_input(payload),
        LLMRegenResult,
        max_output_tokens,
    )


async def verify_claims(
    client: OpenAIClient, claims: list[LLMClaimCheck], max_output_tokens: int
) -> tuple[LLMVerification, Usage]:
    payload = {"claims": [claim.model_dump(mode="json") for claim in claims]}
    return await client.structured_call(
        "verify_claims",
        build_instructions("verification"),
        build_input(payload),
        LLMVerification,
        max_output_tokens,
    )
