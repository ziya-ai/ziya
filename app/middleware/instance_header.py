"""
Stamp every HTTP response with this installation's instance id.

The frontend latches the first ``X-Ziya-Instance`` it sees and stops syncing
if a later response carries a different one -- the signal that the origin it
is talking to has changed servers underneath it (port reuse, a tunnel to
another host, or a half-bound dual-stack tunnel where 127.0.0.1 and ::1 are
served by different processes).

Implemented as a raw ASGI wrapper rather than BaseHTTPMiddleware so it adds
one header to ``http.response.start`` and otherwise stays out of the
response body path; SSE and chunked streams pass through unmodified.
"""
from __future__ import annotations

from app.utils.instance_id import HEADER_NAME, get_instance_id

_HEADER_KEY = HEADER_NAME.lower().encode("latin-1")


class InstanceHeaderMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        # Resolve per request, not at construction: the id is cached after
        # first read, and resolving lazily keeps import-time side effects
        # (creating ~/.ziya) out of app construction and out of tests that
        # build the app before pointing ZIYA_HOME at a temp dir.
        header = (_HEADER_KEY, get_instance_id().encode("latin-1"))

        async def send_with_header(message):
            if message.get("type") == "http.response.start":
                headers = list(message.get("headers") or [])
                # Replace rather than duplicate if an inner layer set it.
                headers = [h for h in headers if h[0].lower() != _HEADER_KEY]
                headers.append(header)
                message = {**message, "headers": headers}
            await send(message)

        await self.app(scope, receive, send_with_header)
