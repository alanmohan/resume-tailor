"""Application factory and ASGI entry point.

Run with ``uvicorn app.main:app``. Tests call ``create_app(settings)`` with
explicit settings instead, so they never read the real ``.env`` file.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import evidence, generations, health, jobs, profiles, sessions
from app.config import Settings, load_settings
from app.db import Mongo
from app.errors import register_error_handlers
from app.logging_config import configure_logging
from app.middleware import (
    REQUEST_ID_HEADER,
    BodySizeLimitMiddleware,
    RequestContextMiddleware,
    UnhandledErrorMiddleware,
)
from app.providers.factory import build_provider


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Open the database client for the lifetime of the process.

    An unreachable database is not fatal here: index creation is attempted,
    and if it fails it is retried by /readyz and by the first request that
    needs the database.
    """
    mongo = Mongo(app.state.settings)
    app.state.mongo = mongo
    await mongo.try_ensure_indexes()
    try:
        yield
    finally:
        await app.state.provider.aclose()
        await mongo.close()


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build the application. Without arguments the settings are loaded from
    the environment and the project-root .env, and invalid configuration stops
    startup with a message that names the setting but never its value."""
    settings = settings or load_settings()
    configure_logging()

    app = FastAPI(title="Resume Tailor API", version="1.0.0", lifespan=lifespan)
    app.state.settings = settings
    app.state.provider = build_provider(settings)

    register_error_handlers(app)

    # add_middleware puts each new middleware OUTSIDE the previous ones, so the
    # last one added here is the first to see a request (see app/middleware.py).
    app.add_middleware(BodySizeLimitMiddleware, max_bytes=settings.max_request_bytes)
    app.add_middleware(UnhandledErrorMiddleware, log_tracebacks=settings.app_env != "production")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        # Bearer tokens travel in the Authorization header, not in cookies, so
        # credentialed (cookie) requests are not needed and stay disabled.
        allow_credentials=False,
        allow_methods=["GET", "POST", "PATCH", "DELETE"],
        allow_headers=["Authorization", "Content-Type", "Idempotency-Key"],
        expose_headers=[REQUEST_ID_HEADER, "Retry-After"],
        max_age=600,
    )
    app.add_middleware(RequestContextMiddleware)

    # The API routers declare their own "/api" prefix, so each route object
    # knows its full path template (the access log reports it).
    for module in (health, sessions, profiles, evidence, jobs, generations):
        app.include_router(module.router)
    return app


def __getattr__(name: str) -> FastAPI:
    """Create the app the first time ``app.main.app`` is accessed (PEP 562).

    Uvicorn looks up the attribute ``app`` on this module, which lands here.
    Building it lazily, rather than with a module-level ``app = create_app()``,
    means that merely importing this module (as the tests do, to reach
    ``create_app``) does not load the real configuration.
    """
    if name == "app":
        application = create_app()
        globals()["app"] = application
        return application
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
