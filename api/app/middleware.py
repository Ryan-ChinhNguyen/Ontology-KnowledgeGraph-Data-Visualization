"""Rejects an oversized request while it is still arriving.

The per-batch size rule is checked again once the files are parsed, against
their exact content. That check alone comes too late to protect the service:
by then the whole request has been received and spooled. This middleware
turns an oversized request away as early as the request allows — before any
of the body is read when the client declares its length, and as soon as the
limit is crossed when it does not.
"""

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.exceptions import FileTooLargeError

#: Room for the multipart envelope — boundaries and per-part headers — which
#: counts towards the request size but not towards the files' content.
MULTIPART_OVERHEAD_BYTES = 64 * 1024


class RequestSizeLimit:
    def __init__(self, app: ASGIApp, *, max_content_bytes: int, limit_mb: int) -> None:
        self._app = app
        self._max_body_bytes = max_content_bytes + MULTIPART_OVERHEAD_BYTES
        self._limit_mb = limit_mb

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return

        declared = _declared_length(scope)
        if declared is not None and declared > self._max_body_bytes:
            await self._reject(scope, receive, send)
            return

        await self._app(scope, self._counting(receive), send)

    def _counting(self, receive: Receive) -> Receive:
        """Wrap ``receive`` to stop once the body passes the limit.

        This covers a client that omits ``Content-Length`` or sends more than
        it declared. Raising an HTTP error here is deliberate: FastAPI passes
        HTTP errors raised while reading the body straight through, whereas
        any other exception would be reported as a generic 400.
        """
        received = 0

        async def receive_within_limit() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self._max_body_bytes:
                    raise FileTooLargeError(self._limit_mb)
            return message

        return receive_within_limit

    async def _reject(self, scope: Scope, receive: Receive, send: Send) -> None:
        error = FileTooLargeError(self._limit_mb)
        response = JSONResponse({"detail": error.detail}, status_code=error.status_code)
        await response(scope, receive, send)


def _declared_length(scope: Scope) -> int | None:
    for name, value in scope.get("headers", []):
        if name == b"content-length":
            try:
                return int(value)
            except ValueError:
                return None
    return None
