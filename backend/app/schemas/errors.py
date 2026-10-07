"""The error envelope returned for every non-2xx API response."""

from typing import Any

from pydantic import BaseModel


class FieldError(BaseModel):
    field: str
    message: str


class ErrorBody(BaseModel):
    code: str
    message: str
    request_id: str
    field_errors: list[FieldError] | None = None
    retryable: bool | None = None
    details: dict[str, Any] | None = None


class ErrorEnvelope(BaseModel):
    error: ErrorBody
