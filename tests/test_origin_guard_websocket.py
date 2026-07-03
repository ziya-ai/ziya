"""
Tests for app.middleware.origin_guard.is_websocket_origin_allowed.

PenPal #157: OriginGuardMiddleware only runs against the ``http`` ASGI
scope. WebSocket upgrade requests use the ``websocket`` scope and bypass
it entirely regardless of middleware registration order, so a malicious
page open in any browser tab could open a cross-origin ws:// connection
to any of Ziya's WebSocket endpoints with no local code execution
required. This tests the shared helper that closes that gap; the actual
``@app.websocket`` handlers apply it as a one-line guard immediately
after the handler is entered (see app/server.py).
"""

import os
from unittest.mock import MagicMock, patch

from app.middleware.origin_guard import is_websocket_origin_allowed


def _make_ws(headers: dict):
    ws = MagicMock()
    ws.headers = headers
    return ws


class TestWebSocketOriginAllowed:
    def test_loopback_origin_allowed(self):
        ws = _make_ws({"origin": "http://localhost:6969"})
        assert is_websocket_origin_allowed(ws)

    def test_loopback_127_origin_allowed(self):
        ws = _make_ws({"origin": "http://127.0.0.1:6969"})
        assert is_websocket_origin_allowed(ws)

    def test_cross_origin_rejected(self):
        ws = _make_ws({"origin": "https://evil.example.com"})
        assert not is_websocket_origin_allowed(ws)

    def test_lookalike_origin_rejected(self):
        """'localhost.evil.com' must not match the anchored loopback regex."""
        ws = _make_ws({"origin": "http://localhost.evil.com"})
        assert not is_websocket_origin_allowed(ws)

    def test_referer_fallback_with_path_rejected(self):
        """A referer carrying a path suffix must not match — only a
        bare origin-shaped value does, matching OriginGuardMiddleware's
        HTTP-side behavior exactly."""
        ws = _make_ws({"referer": "http://localhost:6969/some/page"})
        assert not is_websocket_origin_allowed(ws)

    def test_no_headers_allowed_by_default(self):
        ws = _make_ws({})
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("ZIYA_STRICT_ORIGIN", None)
            assert is_websocket_origin_allowed(ws)

    def test_no_headers_rejected_under_strict_origin(self):
        ws = _make_ws({})
        with patch.dict(os.environ, {"ZIYA_STRICT_ORIGIN": "1"}):
            assert not is_websocket_origin_allowed(ws)
