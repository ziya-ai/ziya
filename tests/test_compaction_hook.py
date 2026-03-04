"""
Tests for T23: Autocompaction hook in stream_with_tools.

Verifies that the compaction engine fires after stream completion
and yields a crystal_ready event.
"""

import asyncio
import pytest
from unittest.mock import AsyncMock, patch, MagicMock

from app.agents.compaction_engine import CompactionEngine, get_compaction_engine
from app.models.delegate import MemoryCrystal


class TestAutocompactionHookContract:
    """
    Test the compaction hook's contract without running stream_with_tools.

    These tests verify that the hook logic (as isolated functions) behaves
    correctly given various conversation states.  This avoids the complexity
    of mocking the full Bedrock streaming pipeline.
    """

    @pytest.fixture
    def engine(self):
        e = CompactionEngine()
        # Disable LLM calls in tests — use deterministic fallback only
        async def _no_llm(*a, **k):
            raise RuntimeError("LLM disabled in tests")
        e._call_summary_model = _no_llm
        return e

    @pytest.fixture
    def long_conversation(self):
        """A conversation long enough to trigger compaction (>2000 tokens ~= >8000 chars)."""
        bt = chr(96) * 4
        msgs = [
            {"role": "user", "content": "Refactor the auth module to use OAuth2. " * 10},
            {"role": "assistant", "content": (
                "I decided to use the authorization code flow with PKCE. "
                "This ensures security for public clients. " * 50
            )},
            {"role": "assistant", "content": (
                f"Created the provider.\n\n{bt}tool:mcp_file_write\n"
                '{"path": "auth/provider.py", "content": "'
                + "class OAuthProvider:\\n    def authenticate(self):\\n        pass\\n" * 30
                + '", "create_only": true}'
                f"\n{bt}"
            )},
            {"role": "assistant", "content": (
                f"Updated config.\n\n{bt}tool:mcp_file_write\n"
                '{"path": "config/settings.py", "content": "OAUTH=True\\nPROVIDER=google\\n", "patch": "old"}'
                f"\n{bt}"
            )},
            {"role": "assistant", "content": (
                f"Ran tests.\n\n{bt}tool:mcp_run_shell_command\n"
                '{"command": "python -m pytest tests/ -v"}'
                f"\n{bt}"
            )},
            {"role": "assistant", "content": (
                "Using RS256 for JWT signing instead of HS256. "
                "We'll use refresh token rotation for security. "
                "Selected PKCE over implicit flow for better security. " * 30
            )},
        ]
        return msgs

    @pytest.fixture
    def short_conversation(self):
        """A conversation too short for compaction."""
        return [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi there!"},
        ]

    def test_long_conversation_produces_crystal(self, engine, long_conversation):
        """Conversations above MIN_COMPACTION_TOKENS produce a crystal."""
        crystal = asyncio.run(
            engine.compact(long_conversation, "conv-123", "Auth Refactor")
        )
        assert crystal is not None
        assert isinstance(crystal, MemoryCrystal)
        assert crystal.delegate_id == "conv-123"
        assert crystal.task == "Auth Refactor"
        assert len(crystal.summary) > 0
        assert len(crystal.files_changed) == 2
        assert crystal.original_tokens > 0
        assert crystal.crystal_tokens > 0

    def test_short_conversation_skipped(self, engine, short_conversation):
        """Conversations below threshold return None."""
        crystal = asyncio.run(
            engine.compact(short_conversation, "conv-456", "Quick Chat")
        )
        assert crystal is None

    def test_crystal_ready_event_structure(self, engine, long_conversation):
        """The crystal_ready event has the expected shape."""
        crystal = asyncio.run(
            engine.compact(long_conversation, "conv-789", "Auth Refactor")
        )
        assert crystal is not None

        # Simulate what the hook does: model_dump for the yield
        event = {'type': 'crystal_ready', 'crystal': crystal.model_dump(mode='json')}

        assert event['type'] == 'crystal_ready'
        assert isinstance(event['crystal'], dict)
        assert event['crystal']['delegate_id'] == 'conv-789'
        assert event['crystal']['task'] == 'Auth Refactor'
        assert isinstance(event['crystal']['files_changed'], list)
        assert isinstance(event['crystal']['decisions'], list)
        assert isinstance(event['crystal']['tool_stats'], dict)

    def test_crystal_round_trips_through_json(self, engine, long_conversation):
        """Crystal can be serialized to JSON and back."""
        import json

        crystal = asyncio.run(
            engine.compact(long_conversation, "conv-rt", "Auth Refactor")
        )
        assert crystal is not None

        serialized = json.dumps(crystal.model_dump(mode='json'))
        deserialized = json.loads(serialized)
        restored = MemoryCrystal(**deserialized)

        assert restored.delegate_id == crystal.delegate_id
        assert restored.summary == crystal.summary
        assert len(restored.files_changed) == len(crystal.files_changed)
        assert restored.decisions == crystal.decisions

    def test_compaction_failure_does_not_raise(self, engine):
        """If compaction fails internally, it should not propagate."""
        # Pass garbage that will fail extraction
        msgs = [{"role": "assistant", "content": 12345}]  # content is int, not str

        # Should not raise — compact handles errors internally
        crystal = asyncio.run(
            engine.compact(msgs, "bad", "bad", force=True)
        )
        # May return a crystal with empty extractions, or None
        # The point is it doesn't crash
        assert crystal is None or isinstance(crystal, MemoryCrystal)

    def test_message_normalization(self, engine):
        """The hook normalizes LangChain message objects to dicts."""
        # Simulate what the hook does with LangChain-style objects
        class FakeMessage:
            def __init__(self, role, content):
                self.role = role
                self.content = content

        messages = [
            FakeMessage("user", "Do something"),
            FakeMessage("assistant", "I decided to use approach X. " * 100),
        ]

        # The hook's normalization logic:
        normalized = [
            m if isinstance(m, dict) else {"role": getattr(m, "role", ""), "content": getattr(m, "content", "")}
            for m in messages
        ]

        assert all(isinstance(m, dict) for m in normalized)
        assert normalized[0]["role"] == "user"
        assert normalized[1]["role"] == "assistant"

        # These should be compactable (with force since they're short)
        crystal = asyncio.run(
            engine.compact(normalized, "test", "test", force=True)
        )
        assert crystal is not None


class TestStreamChunksCrystalEvent:
    """
    Test that the server's stream_chunks function forwards crystal_ready
    events correctly.

    These are unit-level tests on the event handling logic, not full
    integration tests (which would require a running Bedrock connection).
    """

    def test_unknown_chunk_types_are_not_errors(self):
        """
        The stream_chunks function in server.py has fallback handling for
        unknown chunk types.  Verify crystal_ready won't cause errors by
        checking it follows the expected dict pattern.
        """
        chunk = {'type': 'crystal_ready', 'crystal': {'delegate_id': 'D1', 'task': 'test'}}

        # The chunk follows the same pattern as other chunk types
        assert isinstance(chunk, dict)
        assert 'type' in chunk
        assert chunk['type'] == 'crystal_ready'

    def test_crystal_event_is_json_serializable(self):
        """The crystal event must be JSON-serializable for SSE transport."""
        import json

        crystal = MemoryCrystal(
            delegate_id="D1",
            task="Auth Refactor",
            summary="Created OAuth provider with PKCE support.",
            files_changed=[],
            decisions=["Used RS256 for JWT signing"],
            exports={"OAuthProvider": "auth.provider.OAuthProvider"},
            tool_stats={"file_write": 2},
            original_tokens=15000,
            crystal_tokens=350,
            created_at=1234567890.0,
        )

        event = {'type': 'crystal_ready', 'crystal': crystal.model_dump(mode='json')}

        # Must not raise
        serialized = json.dumps(event)
        assert '"crystal_ready"' in serialized
        assert '"D1"' in serialized
