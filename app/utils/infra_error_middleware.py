"""Turn an infrastructure outage that escapes a route into a 503.

``ServiceInfraError`` (app/utils/infra_errors.py) is a BaseException on
purpose, so the memory pipeline's ``except Exception`` fallbacks cannot
guess an answer during an outage.  The same property carries it past
Starlette's exception handling: ``ExceptionMiddleware``,
``ServerErrorMiddleware`` and every ``BaseHTTPMiddleware`` catch
``Exception`` only.  Measured 2026-11-16 on
``GET /api/v1/memory/search`` with expired credentials: status 500, an
empty body, and the only log line was ErrorHandlingMiddleware's
"No response returned" -- the cause was gone.

This is a pure ASGI middleware and must be the INNERMOST one (added to
the app first), so the outage is answered before any BaseHTTPMiddleware's
task group can wrap it in an exception group.  It unwraps groups anyway,
so a different position degrades to "still a 503" rather than to the
silent 500.

Streaming responses are not this middleware's job: once the response has
started nothing can be sent but body bytes.  The chat stream reports its
own failures as SSE error frames (chat_turn_relay._pump,
server._keepalive_wrapper); here a started response is logged and closed.
"""
from __future__ import annotations

import json
from typing import Any, Optional

from app.utils.infra_errors import ServiceInfraError
from app.utils.logging_utils import logger

_MESSAGES = {
    "authentication_error": (
        "The model service rejected or could not obtain credentials. "
        "Refresh your credentials and retry."),
    "throttling": "The model service is throttling requests. Retry shortly.",
    "timeout": "The model service did not answer in time. Retry shortly.",
    "connection_error": (
        "The model service could not be reached. Check your network or VPN "
        "connection and retry."),
}


def find_infra_error(exc: Optional[BaseException]) -> Optional[ServiceInfraError]:
    """The ServiceInfraError in ``exc``: itself, or a leaf of an exception
    group (anyio task groups wrap a child's BaseException in one)."""
    seen = set()
    stack = [exc]
    while stack:
        e = stack.pop()
        if e is None or id(e) in seen:
            continue
        seen.add(id(e))
        if isinstance(e, ServiceInfraError):
            return e
        stack.extend(getattr(e, "exceptions", None) or ())
    return None


def infra_error_payload(err: ServiceInfraError) -> dict:
    kind = getattr(err, "infra_kind", "") or "connection_error"
    message = _MESSAGES.get(kind, _MESSAGES["connection_error"])
    return {
        "error": message,
        "detail": message,
        "error_type": kind,
        "where": getattr(err, "where", "") or "",
        # The request may have written its record before the embedding or
        # model call failed (a memory save embeds after it stores).
        "may_have_partially_applied": True,
    }


class InfraErrorMiddleware:
    """Pure ASGI: ServiceInfraError out of the app -> 503 JSON."""

    def __init__(self, app: Any):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        started = False

        async def _send(message):
            nonlocal started
            if message.get("type") == "http.response.start":
                started = True
            await send(message)

        try:
            await self.app(scope, receive, _send)
        except BaseException as exc:  # noqa: BLE001 -- narrowed just below
            err = find_infra_error(exc)
            if err is None:
                raise
            path = scope.get("path", "?")
            logger.warning(
                f"Infrastructure outage in {scope.get('method', '?')} {path} "
                f"({err.infra_kind}, {err.where or 'unknown call'}): {str(err)[:300]}")
            if started:
                # Headers are out; end the body so the client is not left
                # waiting on a connection nothing will write to again.
                try:
                    await send({"type": "http.response.body", "body": b"",
                                "more_body": False})
                except Exception:  # noqa: BLE001 -- client already gone
                    pass
                return
            body = json.dumps(infra_error_payload(err)).encode("utf-8")
            await send({
                "type": "http.response.start", "status": 503,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(body)).encode("ascii")),
                    (b"retry-after", b"30"),
                ],
            })
            await send({"type": "http.response.body", "body": body,
                        "more_body": False})
