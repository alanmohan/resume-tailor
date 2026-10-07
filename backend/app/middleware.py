"""Pure ASGI middleware: request ID + access log, unexpected-error boundary and
request body size limit.

These are plain ASGI callables rather than Starlette's BaseHTTPMiddleware on
purpose: BaseHTTPMiddleware runs the endpoint in a task that is cancelled when
the client disconnects, which could abort a paid provider call or a database
write half way through. With pure ASGI the handler always runs to completion.

Order (outermost first), set up in app/main.py:
    RequestContextMiddleware -> CORSMiddleware -> UnhandledErrorMiddleware
    -> BodySizeLimitMiddleware -> routes
The error boundary and the size limit sit inside CORS so their responses carry
CORS headers and the browser can read the error envelope.
"""

import logging
import time
import uuid

from fastapi import HTTPException
from starlette.datastructures import Headers, MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.errors import InputTooLarge, error_response
from app.logging_config import exception_location, log_event, request_id_var

logger = logging.getLogger(__name__)

REQUEST_ID_HEADER = "X-Request-ID"


# Fixed framework pages that have no route template; safe to log as they are.
_DOCUMENTATION_PATHS = frozenset({"/docs", "/docs/oauth2-redirect", "/redoc", "/openapi.json"})


def _route_template(scope: Scope) -> str:
    """The matched route's path template (e.g. "/api/jobs/{job_id}").

    The raw URL is never logged: only the template, so IDs and query strings
    stay out of the logs. Unknown URLs are reported as "unmatched".
    """
    template = getattr(scope.get("route"), "path", None)
    if template:
        return template
    return scope["path"] if scope["path"] in _DOCUMENTATION_PATHS else "unmatched"


class RequestContextMiddleware:
    """Assigns a request ID, returns it as X-Request-ID and writes one access
    log line per request (method, route template, status, duration)."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request_id = uuid.uuid4().hex
        context_token = request_id_var.set(request_id)
        started = time.perf_counter()
        status_code = 500

        async def send_with_request_id(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
                MutableHeaders(scope=message)[REQUEST_ID_HEADER] = request_id
            await send(message)

        try:
            await self.app(scope, receive, send_with_request_id)
        finally:
            log_event(
                logger,
                logging.INFO,
                "request",
                method=scope["method"],
                route=_route_template(scope),
                status=status_code,
                duration_ms=round((time.perf_counter() - started) * 1000, 1),
            )
            request_id_var.reset(context_token)


class UnhandledErrorMiddleware:
    """Turns any exception the route handlers did not deal with into the
    standard 500 envelope; the client never sees exception details.

    In production only the exception type and code location are logged,
    because exception messages can contain user content (a validation error,
    for example, quotes its input). With ``log_tracebacks`` (development and
    tests) the full traceback is logged as well, so bugs can be diagnosed.
    """

    def __init__(self, app: ASGIApp, log_tracebacks: bool) -> None:
        self.app = app
        self.log_tracebacks = log_tracebacks

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        response_started = False

        async def tracking_send(message: Message) -> None:
            nonlocal response_started
            if message["type"] == "http.response.start":
                response_started = True
            await send(message)

        try:
            await self.app(scope, receive, tracking_send)
        except Exception as error:
            fields = {"error_type": type(error).__name__, "location": exception_location(error)}
            logger.error(
                "unhandled_exception",
                extra={"fields": fields},
                exc_info=error if self.log_tracebacks else None,
            )
            if not response_started:
                response = error_response(
                    500, "internal_error", "Something went wrong on the server."
                )
                await response(scope, receive, send)


class BodySizeLimitMiddleware:
    """Rejects request bodies larger than ``max_bytes`` with 413 input_too_large.

    A declared Content-Length is checked before anything is read. Bodies sent
    without one (chunked) are counted while they stream in.
    """

    def __init__(self, app: ASGIApp, max_bytes: int) -> None:
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        message = f"The request body is larger than the {self.max_bytes:,} byte limit."
        declared = Headers(scope=scope).get("content-length")
        if declared is not None and declared.isdigit() and int(declared) > self.max_bytes:
            response = error_response(InputTooLarge.status_code, InputTooLarge.code, message)
            await response(scope, receive, send)
            return

        received_bytes = 0

        async def counting_receive() -> Message:
            nonlocal received_bytes
            incoming = await receive()
            if incoming["type"] == "http.request":
                received_bytes += len(incoming.get("body", b""))
                if received_bytes > self.max_bytes:
                    # FastAPI re-raises HTTPException from body parsing unchanged;
                    # the HTTP exception handler turns 413 into the error envelope.
                    raise HTTPException(status_code=InputTooLarge.status_code, detail=message)
            return incoming

        await self.app(scope, counting_receive, send)
