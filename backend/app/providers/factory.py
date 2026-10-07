"""Chooses the AI provider. This is the only place that reads AI_PROVIDER.

One provider instance is built at startup and stored on ``app.state.provider``.
Request handlers receive it through ``get_provider`` (ProviderDep in
app/api/deps.py); tests either use the FakeProvider instance directly or
replace the dependency with ``app.dependency_overrides[get_provider]``.
"""

from fastapi import Request

from app.config import Settings
from app.providers.base import AIProvider
from app.providers.fake.provider import FakeProvider
from app.providers.openai.provider import OpenAIProvider


def build_provider(settings: Settings) -> AIProvider:
    """The fake provider only when it was requested explicitly; settings
    validation already rejects AI_PROVIDER=fake in production."""
    if settings.ai_provider == "fake":
        return FakeProvider()
    return OpenAIProvider(settings)


def get_provider(request: Request) -> AIProvider:
    """FastAPI dependency: the provider the app was started with."""
    return request.app.state.provider
