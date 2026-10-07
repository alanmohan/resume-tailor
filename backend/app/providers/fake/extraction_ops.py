"""Deterministic, rule-based profile extraction and job analysis for the fake
provider (tests, end-to-end tests and demo mode).

These are pure functions: no randomness, no network, no clock. FakeProvider
adds call counting, failure injection and usage accounting around them. The
signatures are final.
"""

from app.providers.base import LLMExtraction, LLMJobAnalysis, LLMJobInput, LLMSource


def extract_profile(sources: list[LLMSource]) -> LLMExtraction:
    raise NotImplementedError("extract_profile is implemented by the profile/jobs feature")


def analyze_job(job: LLMJobInput) -> LLMJobAnalysis:
    raise NotImplementedError("analyze_job is implemented by the profile/jobs feature")
