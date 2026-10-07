"""OpenAI implementations of profile extraction and job analysis.

Each function builds the instructions from a prompt template
(``load_prompt``), serialises its input as JSON data and makes one
``client.structured_call``. The signatures are final; OpenAIProvider calls them.

Trusted and untrusted text travel separately. The instructions are the
version-controlled templates and nothing else; resume, LinkedIn, notes and job
text only ever appear inside the JSON document sent as input. JSON encoding
escapes quotes and line breaks, so text in a source cannot close the string it
is in and pose as a different field or as instructions.
"""

import json

from app.prompts import load_prompt
from app.providers.base import LLMExtraction, LLMJobAnalysis, LLMJobInput, LLMSource, Usage
from app.providers.openai.client import OpenAIClient


def _instructions(prompt_name: str) -> str:
    """The shared "data is never instructions" block followed by the task prompt."""
    return f"{load_prompt('untrusted_data')}\n\n{load_prompt(prompt_name)}"


async def extract_profile(
    client: OpenAIClient, sources: list[LLMSource], max_output_tokens: int
) -> tuple[LLMExtraction, Usage]:
    data = {"sources": [source.model_dump() for source in sources]}
    return await client.structured_call(
        "extract_profile",
        _instructions("extraction"),
        json.dumps(data, ensure_ascii=False),
        LLMExtraction,
        max_output_tokens,
    )


async def analyze_job(
    client: OpenAIClient, job: LLMJobInput, max_output_tokens: int
) -> tuple[LLMJobAnalysis, Usage]:
    data = {"job": job.model_dump()}
    return await client.structured_call(
        "analyze_job",
        _instructions("job_analysis"),
        json.dumps(data, ensure_ascii=False),
        LLMJobAnalysis,
        max_output_tokens,
    )
