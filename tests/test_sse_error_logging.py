"""
Tests for StreamingMiddleware._log_sse_error.

Verifies that every SSE error event sent to the client is:
  1. Returned as a correctly formatted SSE data line.
  2. Logged at ERROR level on the server with the full error payload.

Note: ModeAwareLogger disables propagation, so caplog cannot capture its
output. We patch logger.error directly instead.
"""
import json
from unittest.mock import patch

import pytest


# ---------------------------------------------------------------------------
# Fixture: a StreamingMiddleware instance without a real ASGI app
# ---------------------------------------------------------------------------

@pytest.fixture()
def middleware():
    """Create a StreamingMiddleware with a no-op ASGI app."""
    from starlette.applications import Starlette

    # Import after patching to avoid heavyweight model initialisation
    from app.middleware.streaming import StreamingMiddleware

    dummy_app = Starlette()
    # Bypass __init__ entirely — we only need the helper method
    instance = StreamingMiddleware.__new__(StreamingMiddleware)
    return instance


# ---------------------------------------------------------------------------
# Return value format
# ---------------------------------------------------------------------------

class TestLogSseErrorFormat:
    def test_returns_sse_data_line(self, middleware):
        error_data = {"error": "model_error", "detail": "something broke", "status_code": 500}
        result = middleware._log_sse_error(error_data)
        assert result.startswith("data: ")
        assert result.endswith("\n\n")

    def test_payload_is_valid_json(self, middleware):
        error_data = {"error": "context_size_error", "detail": "too big", "status_code": 413}
        result = middleware._log_sse_error(error_data)
        # Strip the "data: " prefix and trailing \n\n
        json_part = result[len("data: "):].strip()
        parsed = json.loads(json_part)
        assert parsed == error_data

    def test_all_fields_preserved(self, middleware):
        error_data = {
            "error": "chunk_processing_error",
            "detail": "unexpected token",
            "status_code": 500,
            "extra": "metadata",
        }
        result = middleware._log_sse_error(error_data)
        json_part = result[len("data: "):].strip()
        parsed = json.loads(json_part)
        assert parsed["extra"] == "metadata"

    def test_empty_dict_produces_valid_sse(self, middleware):
        result = middleware._log_sse_error({})
        assert result == "data: {}\n\n"


# ---------------------------------------------------------------------------
# Server-side logging
# ---------------------------------------------------------------------------

class TestLogSseErrorLogging:
    def test_logs_at_error_level(self, middleware):
        error_data = {"error": "model_error", "detail": "boom", "status_code": 500}
        with patch("app.middleware.streaming.logger.error") as mock_error:
            middleware._log_sse_error(error_data)
        mock_error.assert_called_once()
        assert "SSE error sent to client" in mock_error.call_args[0][0]

    def test_log_message_contains_full_payload(self, middleware):
        error_data = {"error": "context_size_error", "detail": "too large", "status_code": 413}
        with patch("app.middleware.streaming.logger.error") as mock_error:
            middleware._log_sse_error(error_data)
        log_message = mock_error.call_args[0][0]
        assert "context_size_error" in log_message
        assert "too large" in log_message

    def test_does_not_suppress_on_json_serialisation_error(self, middleware):
        # Non-serialisable objects should propagate (not be silently swallowed)
        non_serialisable = {"set_val": {1, 2, 3}}
        with pytest.raises(TypeError):
            middleware._log_sse_error(non_serialisable)  # type: ignore[arg-type]
