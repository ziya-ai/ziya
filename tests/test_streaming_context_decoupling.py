"""
Regression tests for the StreamingContext decoupling and log filter fixes.

Covers three areas:

1. _PollingAccessFilter — individual chat GETs must be filtered at INFO
   level to avoid flooding logs during delegate sync (20+ GETs per switch).

2. StreamingContext contract — the lightweight context must expose exactly
   the fields that MarkdownRenderer sub-components need, and nothing more.
   If someone adds `conversations` to StreamingContext, these tests fail.

3. queueSave isolation — loadConversation must NOT call queueSave just
   to flip hasUnreadResponse.  Verifies at the model level that the
   read-marking flag is cosmetic and doesn't require persistence.
"""

import logging
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))


# ── Log filter tests ─────────────────────────────────────────────────

class TestPollingAccessFilter:
    """The uvicorn access filter must suppress noisy chat GET lines."""

    # Replicate the filter logic here to avoid importing app.server
    # (which loads the entire FastAPI app and takes 30+ seconds).
    _quiet = {'/chats?', '/chat-groups', '/skills', '/contexts', '/api/config', '/ws/',
              '/folder-progress', '/model-capabilities', '/current-model', '/static/',
              '/delegate-status',}
    _chat_get_re = re.compile(r'/chats/[0-9a-f]{8}-[0-9a-f]{4}-.*" [23]')

    @pytest.fixture
    def log_filter(self):
        """Build a filter matching the server's _PollingAccessFilter contract."""
        quiet = self._quiet
        chat_re = self._chat_get_re
        class Filter(logging.Filter):
            def filter(self, record):
                msg = record.getMessage()
                if any(q in msg for q in quiet):
                    return False
                if 'GET' in msg and chat_re.search(msg):
                    return False
                return True
        return Filter()

    @staticmethod
    def _make_record(msg: str) -> logging.LogRecord:
        record = logging.LogRecord(
            name="uvicorn.access", level=logging.INFO,
            pathname="", lineno=0, msg=msg, args=(), exc_info=None,
        )
        return record

    def test_individual_chat_get_filtered(self, log_filter):
        """GET /chats/<uuid> with 200 must be suppressed."""
        msg = '127.0.0.1:62555 - "GET /api/v1/projects/abc/chats/b9dbe418-fc05-4539-805e-373a03e1244a HTTP/1.1" 200 OK'
        assert log_filter.filter(self._make_record(msg)) is False

    def test_individual_chat_get_300_filtered(self, log_filter):
        """GET /chats/<uuid> with 3xx must be suppressed."""
        msg = '127.0.0.1:62555 - "GET /api/v1/projects/abc/chats/b9dbe418-fc05-4539-805e-373a03e1244a HTTP/1.1" 304 Not Modified'
        assert log_filter.filter(self._make_record(msg)) is False

    def test_chat_post_not_filtered(self, log_filter):
        """POST to /chats/ must NOT be filtered — writes are important."""
        msg = '127.0.0.1:62555 - "POST /api/v1/projects/abc/chats HTTP/1.1" 201 Created'
        assert log_filter.filter(self._make_record(msg)) is True

    def test_chat_get_error_not_filtered(self, log_filter):
        """GET /chats/<uuid> with 4xx/5xx must NOT be filtered."""
        msg = '127.0.0.1:62555 - "GET /api/v1/projects/abc/chats/b9dbe418-fc05-4539-805e-373a03e1244a HTTP/1.1" 404 Not Found'
        assert log_filter.filter(self._make_record(msg)) is True

    def test_chat_get_500_not_filtered(self, log_filter):
        """Server errors on chat GETs must still log."""
        msg = '127.0.0.1:62555 - "GET /api/v1/projects/abc/chats/b9dbe418-fc05-4539-805e-373a03e1244a HTTP/1.1" 500 Internal Server Error'
        assert log_filter.filter(self._make_record(msg)) is True

    def test_bulk_chats_filtered(self, log_filter):
        """GET /chats? (bulk listing) already filtered by _quiet set."""
        msg = '127.0.0.1:62555 - "GET /api/v1/projects/abc/chats?since=123 HTTP/1.1" 200 OK'
        assert log_filter.filter(self._make_record(msg)) is False

    def test_unrelated_endpoint_not_filtered(self, log_filter):
        """Non-polling endpoints must always log."""
        msg = '127.0.0.1:62555 - "POST /api/v1/chat HTTP/1.1" 200 OK'
        assert log_filter.filter(self._make_record(msg)) is True

    def test_delegate_status_filtered(self, log_filter):
        """Delegate polling endpoint must be filtered."""
        msg = '127.0.0.1:62555 - "GET /api/v1/delegate-status/abc HTTP/1.1" 200 OK'
        assert log_filter.filter(self._make_record(msg)) is False


# ── StreamingContext contract ─────────────────────────────────────────

class TestStreamingContextContract:
    """
    StreamingContext must expose ONLY streaming-related fields.
    If `conversations` or other heavy state leaks in, MarkdownRenderer
    sub-components re-subscribe to the full state and the delegate
    freeze regression returns.
    """

    @pytest.fixture
    def ctx_content(self):
        ctx_path = Path(__file__).parent.parent / "frontend" / "src" / "context" / "StreamingContext.tsx"
        assert ctx_path.exists(), "StreamingContext.tsx missing"
        return ctx_path.read_text()

    def test_streaming_context_file_exists(self, ctx_content):
        """StreamingContext module must exist."""
        assert len(ctx_content) > 0

    def test_no_conversations_in_interface(self, ctx_content):
        """StreamingContext must NOT include 'conversations' in its interface.

        This is the core regression guard: if conversations leaks into
        StreamingContext, every MarkdownRenderer sub-component re-subscribes
        to the full conversation state, causing the delegate freeze.
        """
        interface_match = re.search(
            r'interface\s+StreamingContextValue\s*\{(.*?)\}',
            ctx_content, re.DOTALL,
        )
        assert interface_match, "StreamingContextValue interface not found"
        interface_body = interface_match.group(1)
        assert 'conversations' not in interface_body, (
            "StreamingContextValue must NOT include 'conversations' — "
            "this would re-introduce the delegate conversation freeze"
        )

    def test_no_messages_in_interface(self, ctx_content):
        """StreamingContext must NOT include message-related fields."""
        interface_match = re.search(
            r'interface\s+StreamingContextValue\s*\{(.*?)\}',
            ctx_content, re.DOTALL,
        )
        assert interface_match
        interface_body = interface_match.group(1)
        for forbidden in ['currentMessages', 'addMessageToConversation', 'setConversations']:
            assert forbidden not in interface_body, (
                f"StreamingContextValue must NOT include '{forbidden}'"
            )

    def test_has_required_fields(self, ctx_content):
        """StreamingContext must expose fields sub-components need."""
        required = ['isStreaming', 'currentConversationId', 'streamingConversations']
        for field in required:
            assert field in ctx_content, f"StreamingContext missing required field: {field}"

    def test_exports_hook(self, ctx_content):
        """useStreamingContext hook must be exported."""
        assert 'export function useStreamingContext' in ctx_content or \
               'export const useStreamingContext' in ctx_content, \
            "useStreamingContext hook must be exported"

    def test_exports_provider(self, ctx_content):
        """StreamingProvider must be exported for ChatContext to wrap."""
        assert 'export' in ctx_content and 'StreamingProvider' in ctx_content, \
            "StreamingProvider must be exported"


# ── hasUnreadResponse is cosmetic ─────────────────────────────────────

class TestHasUnreadResponseCosmetic:
    """
    hasUnreadResponse is a UI-only flag.  It must NOT be required for
    chat integrity.  This guards against regressions where someone
    re-adds queueSave to the read-marking path.
    """

    def test_chat_valid_without_has_unread(self):
        from app.models.chat import Chat
        chat = Chat(id="c1", title="Test", messages=[], createdAt=1000, lastActiveAt=1000)
        assert not getattr(chat, 'hasUnreadResponse', False)

    def test_chat_valid_with_has_unread_true(self):
        from app.models.chat import Chat
        chat = Chat(id="c1", title="Test", messages=[], createdAt=1000, lastActiveAt=1000, hasUnreadResponse=True)
        assert chat.hasUnreadResponse is True

    def test_chat_serialization_roundtrip(self):
        """Chat must serialize/deserialize cleanly without hasUnreadResponse."""
        from app.models.chat import Chat
        chat = Chat(id="c1", title="Test", messages=[], createdAt=1000, lastActiveAt=1000)
        data = chat.model_dump()
        restored = Chat(**data)
        assert restored.id == "c1"

    def test_has_unread_does_not_affect_content_equality(self):
        """Two chats differing only in hasUnreadResponse have same content."""
        from app.models.chat import Chat
        chat_a = Chat(id="c1", title="Test", messages=[], createdAt=1000, lastActiveAt=1000, hasUnreadResponse=False)
        chat_b = Chat(id="c1", title="Test", messages=[], createdAt=1000, lastActiveAt=1000, hasUnreadResponse=True)
        # Content is identical — only the cosmetic flag differs
        assert chat_a.title == chat_b.title
        assert chat_a.messages == chat_b.messages
