"""The real provider: OpenAI for generation and embeddings."""

from app.config import Settings
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
from app.providers.openai import extraction_ops, generation_ops
from app.providers.openai.client import OpenAIClient
from app.schemas.common import ProviderMode


class OpenAIProvider:
    """Implements AIProvider. Embedding is handled here; every other operation
    is delegated to an ops module together with its output-token limit."""

    mode: ProviderMode = "openai"

    def __init__(self, settings: Settings, client: OpenAIClient | None = None) -> None:
        self._settings = settings
        self.generation_model = settings.openai_model
        self.embedding_model = settings.openai_embedding_model
        self.embedding_dimension = settings.openai_embedding_dimensions
        api_key = settings.openai_api_key
        self._client = client or OpenAIClient(
            api_key=api_key.get_secret_value() if api_key else None,
            model=settings.openai_model,
            embedding_model=settings.openai_embedding_model,
            embedding_dimension=settings.openai_embedding_dimensions,
            reasoning_effort=settings.openai_reasoning_effort,
            timeout_seconds=settings.provider_timeout_seconds,
            max_retries=settings.provider_max_retries,
        )

    async def embed(self, texts: list[str]) -> tuple[list[list[float]], Usage]:
        return await self._client.embed(texts)

    async def extract_profile(self, sources: list[LLMSource]) -> tuple[LLMExtraction, Usage]:
        return await extraction_ops.extract_profile(
            self._client, sources, self._settings.max_output_tokens_extraction
        )

    async def analyze_job(self, job: LLMJobInput) -> tuple[LLMJobAnalysis, Usage]:
        return await extraction_ops.analyze_job(
            self._client, job, self._settings.max_output_tokens_job_analysis
        )

    async def generate_documents(
        self, ctx: LLMGenerationContext, feedback: list[str] | None
    ) -> tuple[LLMGeneration, Usage]:
        return await generation_ops.generate_documents(
            self._client, ctx, feedback, self._settings.max_output_tokens_generation
        )

    async def regenerate_item(
        self, ctx: LLMGenerationContext, target: LLMRegenTarget
    ) -> tuple[LLMRegenResult, Usage]:
        return await generation_ops.regenerate_item(
            self._client, ctx, target, self._settings.max_output_tokens_regeneration
        )

    async def verify_claims(self, claims: list[LLMClaimCheck]) -> tuple[LLMVerification, Usage]:
        return await generation_ops.verify_claims(
            self._client, claims, self._settings.max_output_tokens_verification
        )

    async def aclose(self) -> None:
        await self._client.aclose()
