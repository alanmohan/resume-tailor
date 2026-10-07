"""OpenAI implementations of profile extraction and job analysis.

Each function builds the instructions from a prompt template
(``load_prompt``), serialises its input as JSON data and makes one
``client.structured_call``. The signatures are final; OpenAIProvider calls them.
"""

from app.providers.base import LLMExtraction, LLMJobAnalysis, LLMJobInput, LLMSource, Usage
from app.providers.openai.client import OpenAIClient


async def extract_profile(
    client: OpenAIClient, sources: list[LLMSource], max_output_tokens: int
) -> tuple[LLMExtraction, Usage]:
    raise NotImplementedError("extract_profile is implemented by the profile/jobs feature")


async def analyze_job(
    client: OpenAIClient, job: LLMJobInput, max_output_tokens: int
) -> tuple[LLMJobAnalysis, Usage]:
    raise NotImplementedError("analyze_job is implemented by the profile/jobs feature")
