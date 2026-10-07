"""OpenAI implementations of document generation, single-item regeneration and
optional semantic verification.

Each function builds the instructions from a prompt template
(``load_prompt``), serialises its input as JSON data and makes one
``client.structured_call``. The signatures are final; OpenAIProvider calls them.
"""

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


async def generate_documents(
    client: OpenAIClient,
    ctx: LLMGenerationContext,
    feedback: list[str] | None,
    max_output_tokens: int,
) -> tuple[LLMGeneration, Usage]:
    raise NotImplementedError("generate_documents is implemented by the generation feature")


async def regenerate_item(
    client: OpenAIClient,
    ctx: LLMGenerationContext,
    target: LLMRegenTarget,
    max_output_tokens: int,
) -> tuple[LLMRegenResult, Usage]:
    raise NotImplementedError("regenerate_item is implemented by the generation feature")


async def verify_claims(
    client: OpenAIClient, claims: list[LLMClaimCheck], max_output_tokens: int
) -> tuple[LLMVerification, Usage]:
    raise NotImplementedError("verify_claims is implemented by the generation feature")
