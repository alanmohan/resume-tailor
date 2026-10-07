"""EM-10: no library may write request URLs to the application log.

The OpenAI SDK sends its requests with ``httpx2``, which logs one INFO line
per request ('HTTP Request: POST https://... "HTTP/1.1 200 OK"'). Only
``httpx`` was silenced, so those lines went through the JSON handler.
"""

import logging
from collections.abc import Iterator
from contextlib import contextmanager

import openai
import pytest

from app.logging_config import HANDLER_NAME, QUIET_LIBRARY_LOGGERS, configure_logging

UVICORN_LOGGERS = ("uvicorn", "uvicorn.error", "uvicorn.access")
REQUEST_LINE = 'HTTP Request: POST https://api.openai.com/v1/responses "HTTP/1.1 200 OK"'


@contextmanager
def configured_logging() -> Iterator[None]:
    """configure_logging() for the duration of a block, undone afterwards.

    Entered inside the test, after ``capsys`` took over stdout, so the JSON
    handler writes to the captured stream. Only the JSON handler is swapped on
    the root logger: pytest keeps handlers of its own there.
    """
    root = logging.getLogger()
    root_level = root.level
    earlier = [handler for handler in root.handlers if handler.get_name() == HANDLER_NAME]
    others = [logging.getLogger(name) for name in (*QUIET_LIBRARY_LOGGERS, *UVICORN_LOGGERS)]
    saved = [(lg, lg.level, lg.disabled, lg.propagate, list(lg.handlers)) for lg in others]
    configure_logging()
    try:
        yield
    finally:
        for handler in [h for h in root.handlers if h.get_name() == HANDLER_NAME]:
            root.removeHandler(handler)
        for handler in earlier:
            root.addHandler(handler)
        root.setLevel(root_level)
        for logger, level, disabled, propagate, handlers in saved:
            logger.setLevel(level)
            logger.disabled = disabled
            logger.propagate = propagate
            logger.handlers = handlers


def sdk_http_library() -> str:
    """Top-level package of the HTTP client class the OpenAI SDK builds on."""
    packages = [cls.__module__.split(".")[0] for cls in openai.DefaultAsyncHttpxClient.__mro__]
    return next(name for name in packages if name not in {"openai", "builtins"})


@pytest.mark.parametrize(
    "library", ["httpx", "httpx2", "httpcore", "httpcore2", "openai", "pymongo"]
)
def test_library_info_lines_do_not_reach_the_json_handler(
    library: str, capsys: pytest.CaptureFixture[str]
) -> None:
    with configured_logging():
        for name in (library, f"{library}._client"):
            logging.getLogger(name).info(REQUEST_LINE)
            logging.getLogger(name).debug(REQUEST_LINE)
        quiet_output = capsys.readouterr().out

        # Warnings are still reported: a library problem must stay visible.
        logging.getLogger(library).warning("connection pool is full")
        warning_output = capsys.readouterr().out

    assert quiet_output == ""
    assert "connection pool is full" in warning_output


def test_application_info_lines_still_reach_the_json_handler(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with configured_logging():
        logging.getLogger("app.fix_platform_probe").info("profile_indexed")
        output = capsys.readouterr().out

    assert '"message": "profile_indexed"' in output


def test_the_http_library_the_openai_sdk_uses_is_silenced() -> None:
    """Guards the root cause: the SDK changed its HTTP library once already.
    If it does so again, this fails until the new name is added."""
    library = sdk_http_library()

    assert library in QUIET_LIBRARY_LOGGERS
    with configured_logging():
        assert logging.getLogger(f"{library}._client").getEffectiveLevel() >= logging.WARNING
