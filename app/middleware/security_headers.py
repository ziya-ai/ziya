"""Security headers middleware for Ziya."""
import os

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

# Specific jsdelivr script paths the bundled UI legitimately loads
# (marked, mermaid, vega-embed). Strict mode pins to these instead of
# allowlisting the whole cdn.jsdelivr.net origin.
_JSDELIVR_PINNED = (
    "https://cdn.jsdelivr.net/npm/marked/marked.min.js "
    "https://cdn.jsdelivr.net/npm/mermaid@10/dist/mermaid.min.js "
    "https://cdn.jsdelivr.net/npm/vega-embed@6"
)


def build_csp(mode: str = "relaxed") -> str:
    """Build the Content-Security-Policy header value.

    relaxed (default): allows 'unsafe-inline' + 'unsafe-eval' and the whole
        cdn.jsdelivr.net origin so Mermaid/Vega CDN diagrams render. This is
        the historical behaviour.
    strict: drops 'unsafe-eval' and pins jsdelivr to specific script paths.
        'unsafe-inline' is retained on script-src because the CRA build
        inlines its runtime chunk as an inline <script> (removing it requires
        a nonce-injecting build step, tracked separately). Vega diagrams that
        rely on the expression evaluator will not render in strict mode.
    """
    if mode == "strict":
        script_src = f"script-src 'self' 'unsafe-inline' {_JSDELIVR_PINNED}; "
    else:
        script_src = (
            "script-src 'self' 'unsafe-inline' 'unsafe-eval' "
            "https://cdn.jsdelivr.net; "
        )
    return (
        "default-src 'self'; "
        + script_src
        + "style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data: https:; "
        "font-src 'self' data:; "
        "connect-src 'self' http://localhost:* ws://localhost:* wss://localhost:*; "
        "object-src 'none'; "
        "base-uri 'self'; "
        "frame-ancestors 'none'; "
        "form-action 'self'"
    )


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Add security headers to all responses."""
    
    async def dispatch(self, request: Request, call_next) -> Response:
        response = await call_next(request)
        
        # Prevent clickjacking
        response.headers["X-Frame-Options"] = "DENY"
        
        # Prevent MIME type sniffing
        response.headers["X-Content-Type-Options"] = "nosniff"
        
        # Enable XSS protection
        response.headers["X-XSS-Protection"] = "1; mode=block"
        
        # Control referrer information
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        
        # Add CSP for non-streaming responses
        content_type = response.headers.get("content-type", "")
        if "text/event-stream" not in content_type:
            mode = os.environ.get("ZIYA_CSP_MODE", "relaxed").strip().lower()
            response.headers["Content-Security-Policy"] = build_csp(mode)
        
        return response
