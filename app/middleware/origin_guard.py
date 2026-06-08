"""Origin-validation middleware — CSRF / browser drive-by protection.

Ziya has no login layer; the local API trusts the host process's AWS
credentials. That makes a *browser drive-by* the primary residual
threat whenever the server is reachable from a browser: a malicious
page the developer visits can issue cross-origin POSTs to the local
API and drive the agent with the developer's credentials.

This middleware rejects state-changing requests (POST/PUT/PATCH/DELETE)
whose Origin (or Referer fallback) is not a loopback address. Safe
methods (GET/HEAD) and CORS preflight (OPTIONS) pass through untouched,
so SSE streaming and normal reads are unaffected.

Requests with neither Origin nor Referer are allowed by default:
non-browser clients (curl, local scripts, the Ziya CLI) don't send
those headers, and the default loopback bind already restricts who can
reach the socket. Set ZIYA_STRICT_ORIGIN=1 to also reject header-less
state-changing requests — recommended when binding to 0.0.0.0.
"""
import os
import re

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from app.utils.logging_utils import logger

# http(s)://localhost or 127.0.0.1, optional :port. Anchored both ends
# so "http://localhost.evil.com" cannot match.
_LOOPBACK_ORIGIN = re.compile(
    r"^https?://(localhost|127\.0\.0\.1)(:\d+)?$", re.IGNORECASE
)
_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


class OriginGuardMiddleware(BaseHTTPMiddleware):
    """Block cross-origin state-changing requests against the local API."""

    async def dispatch(self, request: Request, call_next):
        if request.method in _SAFE_METHODS:
            return await call_next(request)

        # Origin is the authoritative CSRF signal; Referer is the fallback
        # for clients that omit Origin on same-origin requests.
        candidate = request.headers.get("origin") or request.headers.get("referer")

        if candidate is None:
            strict = os.environ.get("ZIYA_STRICT_ORIGIN", "").lower() in (
                "1", "true", "yes",
            )
            if strict:
                logger.warning(
                    f"🔒 ORIGIN_GUARD: rejecting {request.method} "
                    f"{request.url.path} — no Origin/Referer and "
                    f"ZIYA_STRICT_ORIGIN is set"
                )
                return JSONResponse(
                    {"error": "Origin validation failed", "error_type": "csrf_block"},
                    status_code=403,
                )
            return await call_next(request)

        if _LOOPBACK_ORIGIN.match(candidate):
            return await call_next(request)

        logger.warning(
            f"🔒 ORIGIN_GUARD: blocked cross-origin {request.method} "
            f"{request.url.path} from origin={candidate!r}"
        )
        return JSONResponse(
            {"error": "Cross-origin request rejected", "error_type": "csrf_block"},
            status_code=403,
        )
