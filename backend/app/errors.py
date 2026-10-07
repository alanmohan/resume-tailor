"""Application errors and the handlers that turn every failure into one envelope:

    {"error": {"code", "message", "request_id", "field_errors"?, "retryable"?, "details"?}}

Raise an AppError subclass anywhere in request handling; the handler registered
here produces the response. Messages are written for end users and must never
contain secrets, stack traces or other users' data.
"""

import logging
from collections.abc import Mapping, Sequence
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pymongo.errors import ConnectionFailure
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.logging_config import log_event, request_id_var
from app.providers.base import ProviderError

logger = logging.getLogger(__name__)


class AppError(Exception):
    """Base class for errors with a stable API code and HTTP status."""

    code = "internal_error"
    status_code = 500
    default_message = "Something went wrong on the server."

    def __init__(
        self,
        message: str | None = None,
        *,
        field_errors: Sequence[Mapping[str, str]] | None = None,
        retryable: bool | None = None,
        details: Mapping[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> None:
        self.message = message or self.default_message
        self.field_errors = list(field_errors) if field_errors else None
        self.retryable = retryable
        self.details = dict(details) if details else None
        self.headers = dict(headers) if headers else None
        super().__init__(self.message)


class ValidationFailed(AppError):
    """Invalid input found by application code (schema errors are handled separately).

    Example: ``raise ValidationFailed.for_field("sources", "At most 5 sources are allowed")``
    """

    code = "validation_error"
    status_code = 422
    default_message = "The request is not valid."

    @classmethod
    def for_field(cls, field: str, message: str) -> "ValidationFailed":
        return cls(message, field_errors=[{"field": field, "message": message}])


class Unauthorized(AppError):
    code = "unauthorized"
    status_code = 401
    default_message = "A valid session token is required."


class SessionExpired(AppError):
    code = "session_expired"
    status_code = 401
    default_message = "This session has expired. Start a new session to continue."


class NotFound(AppError):
    """Used for both missing and non-owned IDs, so one cannot be told from the other."""

    code = "not_found"
    status_code = 404
    default_message = "The requested resource was not found."


class VersionConflict(AppError):
    code = "version_conflict"
    status_code = 409
    default_message = "This item was changed elsewhere. Reload it and try again."


class UnresolvedConflicts(AppError):
    code = "unresolved_conflicts"
    status_code = 409
    default_message = "Resolve or dismiss every conflict before confirming the profile."


class ProfileNotConfirmed(AppError):
    code = "profile_not_confirmed"
    status_code = 409
    default_message = "Confirm the profile before generating documents."


class ProfileNotIndexed(AppError):
    code = "profile_not_indexed"
    status_code = 409
    default_message = "The confirmed profile is not fully indexed yet. Confirm it again."


class TooManyChunks(AppError):
    code = "too_many_chunks"
    status_code = 422
    default_message = "The profile is too large to index. Shorten the profile and try again."


class GenerationInProgress(AppError):
    code = "generation_in_progress"
    status_code = 409
    default_message = "This generation is still running."

    def __init__(self, generation_id: str) -> None:
        super().__init__(details={"generation_id": generation_id})


class IdempotencyKeyRequired(AppError):
    code = "idempotency_key_required"
    status_code = 400
    default_message = "An Idempotency-Key header of 8 to 128 characters is required."


class InputTooLarge(AppError):
    code = "input_too_large"
    status_code = 413
    default_message = "The submitted content is too large."


class RateLimited(AppError):
    code = "rate_limited"
    status_code = 429
    default_message = "Too many requests. Try again later."

    def __init__(self, retry_after_seconds: int, message: str | None = None) -> None:
        super().__init__(message, headers={"Retry-After": str(max(1, retry_after_seconds))})


class QuotaExceeded(AppError):
    code = "quota_exceeded"
    status_code = 429
    default_message = "The usage limit for this action has been reached."


class DatabaseUnavailable(AppError):
    code = "database_unavailable"
    status_code = 503
    default_message = "The database is temporarily unavailable. Try again shortly."

    def __init__(self, message: str | None = None) -> None:
        super().__init__(message, retryable=True)


PROVIDER_STATUS_CODES = {
    "provider_timeout": 504,
    "provider_rate_limited": 503,
    "provider_unavailable": 502,
    "provider_invalid_output": 502,
}

_HTTP_ERROR_CODES = {
    401: "unauthorized",
    404: "not_found",
    405: "method_not_allowed",
    413: "input_too_large",
    422: "validation_error",
}
# Friendlier wording for the two errors the router itself raises.
_ROUTER_MESSAGES = {
    404: NotFound.default_message,
    405: "This method is not allowed for the requested URL.",
}


def error_response(
    status_code: int,
    code: str,
    message: str,
    *,
    field_errors: Sequence[Mapping[str, str]] | None = None,
    retryable: bool | None = None,
    details: Mapping[str, Any] | None = None,
    headers: Mapping[str, str] | None = None,
) -> JSONResponse:
    """Build the error envelope. Optional keys are omitted when not set."""
    body: dict[str, Any] = {"code": code, "message": message, "request_id": request_id_var.get()}
    if field_errors:
        body["field_errors"] = [dict(item) for item in field_errors]
    if retryable is not None:
        body["retryable"] = retryable
    if details:
        body["details"] = dict(details)
    return JSONResponse(
        {"error": body}, status_code=status_code, headers=dict(headers) if headers else None
    )


def _field_name(location: Sequence[Any]) -> str:
    """("body", "sources", 0, "label") -> "sources.0.label"; non-body parts keep
    their prefix, e.g. ("header", "idempotency-key") -> "header.idempotency-key"."""
    parts = [str(part) for part in location]
    if parts and parts[0] == "body":
        parts = parts[1:] or ["body"]
        if parts[0].isdigit():  # JSON syntax errors report ("body", <character position>)
            return "body"
    return ".".join(parts)


async def handle_app_error(_: Request, error: AppError) -> JSONResponse:
    return error_response(
        error.status_code,
        error.code,
        error.message,
        field_errors=error.field_errors,
        retryable=error.retryable,
        details=error.details,
        headers=error.headers,
    )


async def handle_provider_error(_: Request, error: ProviderError) -> JSONResponse:
    log_event(
        logger,
        logging.WARNING,
        "provider_error",
        code=error.code,
        error_type=type(error).__name__,
    )
    return error_response(
        PROVIDER_STATUS_CODES[error.code], error.code, error.message, retryable=error.retryable
    )


async def handle_validation_error(_: Request, error: RequestValidationError) -> JSONResponse:
    # Only the location and the reason are returned; the rejected input is not echoed.
    field_errors = [
        {"field": _field_name(item["loc"]), "message": item["msg"].removeprefix("Value error, ")}
        for item in error.errors()
    ]
    return error_response(
        422, "validation_error", ValidationFailed.default_message, field_errors=field_errors
    )


async def handle_http_exception(_: Request, error: StarletteHTTPException) -> JSONResponse:
    """Errors raised by the framework itself, such as unknown routes (404) and
    wrong methods (405)."""
    fallback_code = "internal_error" if error.status_code >= 500 else "validation_error"
    code = _HTTP_ERROR_CODES.get(error.status_code, fallback_code)
    detail = error.detail if isinstance(error.detail, str) else "Request failed."
    message = _ROUTER_MESSAGES.get(error.status_code, detail)
    return error_response(error.status_code, code, message, headers=error.headers)


async def handle_database_unavailable(_: Request, error: ConnectionFailure) -> JSONResponse:
    log_event(logger, logging.WARNING, "database_unavailable", error_type=type(error).__name__)
    return error_response(
        503, "database_unavailable", DatabaseUnavailable.default_message, retryable=True
    )


def register_error_handlers(app: FastAPI) -> None:
    """Unexpected exceptions are handled by UnhandledErrorMiddleware instead,
    because Starlette re-raises exceptions handled at the ``Exception`` level."""
    app.add_exception_handler(AppError, handle_app_error)
    app.add_exception_handler(ProviderError, handle_provider_error)
    app.add_exception_handler(RequestValidationError, handle_validation_error)
    app.add_exception_handler(StarletteHTTPException, handle_http_exception)
    app.add_exception_handler(ConnectionFailure, handle_database_unavailable)
