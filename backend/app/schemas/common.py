"""Shared building blocks for API and document models: IDs, UTC timestamps, enums."""

import uuid
from datetime import UTC, datetime
from typing import Annotated, Literal

from pydantic import BeforeValidator, PlainSerializer, StringConstraints


def new_id() -> str:
    """Random 128-bit identifier as 32 hex characters."""
    return uuid.uuid4().hex


def utc_now() -> datetime:
    """Current UTC time truncated to milliseconds.

    MongoDB stores dates with millisecond precision, so truncating here makes a
    value compare equal before and after a database round trip.
    """
    now = datetime.now(UTC)
    return now.replace(microsecond=(now.microsecond // 1000) * 1000)


def iso_z(value: datetime) -> str:
    """Format a datetime as ISO-8601 UTC with millisecond precision and a Z suffix."""
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


# A datetime that stays a datetime in Python (and in MongoDB) but is written as
# an ISO string ending in Z whenever a model is serialised to JSON.
IsoDateTime = Annotated[datetime, PlainSerializer(iso_z, return_type=str, when_used="json")]

ProviderMode = Literal["openai", "fake"]
Provenance = Literal["extracted", "user_edited", "user_added"]


def _blank_to_none(value: object) -> object:
    """Turn "" or whitespace-only input into None so optional fields have one empty form."""
    if isinstance(value, str) and not value.strip():
        return None
    return value


BROKEN_CHARACTER_MESSAGE = (
    "The text contains a broken character (half of an emoji or symbol). Remove it and try again."
)


def require_valid_unicode(text: str) -> str:
    """Reject text that cannot be encoded as UTF-8.

    JSON may carry half of a surrogate pair (the escape "\\ud83d" without its
    partner, typically a cut-off emoji). Python accepts it in a string, but it
    can be neither sent to the AI provider nor serialised again, so it would
    fail with a server error after the quota was charged. Used by request
    fields that are plain ``str``; the length-limited types reject it already.
    """
    try:
        text.encode("utf-8")
    except UnicodeEncodeError:
        raise ValueError(BROKEN_CHARACTER_MESSAGE) from None
    return text


def required_text(max_length: int) -> type[str]:
    """Request field type: trimmed, non-empty string of at most ``max_length`` characters."""
    return Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=max_length)
    ]


def optional_text(max_length: int) -> type[str | None]:
    """Request field type: trimmed string or None; blank input becomes None."""
    trimmed = Annotated[str, StringConstraints(strip_whitespace=True, max_length=max_length)]
    return Annotated[trimmed | None, BeforeValidator(_blank_to_none)]
