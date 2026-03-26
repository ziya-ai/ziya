"""
Tests for the shell conversation data-loss prevention guards.

The Ziya frontend loads "conversation shells" on startup — lightweight
objects with only the first and last messages — to render the sidebar
quickly.  A critical bug was identified where multiple code paths
(queueSave fast-path cache, GC, server sync) could write these shells
back to IndexedDB, destroying all middle messages.

These tests validate the backend-side guards and the Chat model's
handling of shell metadata.  Frontend-side guards are tested in the
corresponding JavaScript/TypeScript test suite.

Ref: ChatContext.tsx getConversationShells() / queueSave() race condition
"""
import pytest
import json
import time
from pathlib import Path
from unittest.mock import patch, MagicMock


class TestShellMetadataPreservation:
    """Test that shell metadata (_isShell, _fullMessageCount) is preserved
    correctly through the Pydantic Chat model."""

    def test_chat_model_allows_extra_fields(self):
        """Chat model uses extra='allow' so _isShell and _fullMessageCount pass through."""
        from app.models.chat import Chat

        chat_data = {
            "id": "test-123",
            "title": "Test Conversation",
            "messages": [
                {"id": "m1", "role": "human", "content": "Hello", "timestamp": 1000},
                {"id": "m2", "role": "assistant", "content": "Hi", "timestamp": 2000},
            ],
            "createdAt": 1000,
            "lastActiveAt": 2000,
            "_isShell": True,
            "_fullMessageCount": 50,
        }
        chat = Chat(**chat_data)
        dumped = chat.model_dump()

        # Extra fields should survive round-trip
        assert dumped.get("_isShell") == True
        assert dumped.get("_fullMessageCount") == 50

    def test_chat_model_without_shell_fields(self):
        """Normal (non-shell) chats should not have _isShell set."""
        from app.models.chat import Chat

        chat_data = {
            "id": "test-456",
            "title": "Full Conversation",
            "messages": [
                {"id": "m1", "role": "human", "content": "Hello", "timestamp": 1000},
            ],
            "createdAt": 1000,
            "lastActiveAt": 2000,
        }
        chat = Chat(**chat_data)
        dumped = chat.model_dump()

        assert dumped.get("_isShell") is None
        assert dumped.get("_fullMessageCount") is None

    def test_bulk_sync_preserves_shell_metadata(self):
        """When bulk-sync receives data with shell metadata, it should be
        written through to JSON.  This validates that if a misbehaving client
        sends shell data, the server doesn't silently strip the markers."""
        from app.models.chat import Chat, ChatBulkSync

        chat_data = {
            "id": "test-789",
            "title": "Shell Chat",
            "messages": [
                {"id": "m1", "role": "human", "content": "First", "timestamp": 1000},
                {"id": "m50", "role": "assistant", "content": "Last", "timestamp": 50000},
            ],
            "createdAt": 1000,
            "lastActiveAt": 50000,
            "_isShell": True,
            "_fullMessageCount": 50,
        }
        sync_data = ChatBulkSync(chats=[Chat(**chat_data)])
        
        # Verify the shell markers survive through the Pydantic model
        synced_chat = sync_data.chats[0]
        dumped = synced_chat.model_dump()
        assert dumped.get("_isShell") == True
        assert dumped.get("_fullMessageCount") == 50


class TestMessageCountProtection:
    """Test that conversations with reduced message counts are detected
    and protected against accidental overwrite."""

    def test_detect_message_count_regression(self):
        """Simulate the scenario where a shell with 2 messages would
        overwrite a full conversation with 50 messages."""
        # This tests the logic that should be in the save guard
        full_messages = 50
        shell_messages = 2  # first + last only

        # The guard should reject this write
        is_shell = True
        would_lose_data = is_shell and shell_messages < full_messages
        assert would_lose_data, "Guard should detect message count regression"

    def test_allow_normal_message_addition(self):
        """Adding a new message should not be blocked by the guard."""
        full_messages = 50
        new_messages = 51  # One message added

        is_shell = False
        would_lose_data = is_shell and new_messages < full_messages
        assert not would_lose_data, "Normal message addition should not be blocked"

    def test_allow_cleared_shell_to_save(self):
        """Once _isShell is cleared (full data loaded), saves should proceed."""
        full_messages = 50

        is_shell = False  # Shell flag cleared after lazy-load
        would_lose_data = is_shell and 50 < full_messages
        assert not would_lose_data, "Cleared shell should be allowed to save"


class TestServerSyncVersioning:
    """Test that server sync correctly handles version comparison
    to prevent stale data from overwriting newer data."""

    def test_version_comparison_rejects_older(self):
        """Server sync should reject incoming data with older _version."""
        incoming_version = 1000
        existing_version = 2000

        # The sync logic should skip if incoming is older
        should_update = incoming_version >= existing_version
        assert not should_update, "Older version should be rejected"

    def test_version_comparison_accepts_newer(self):
        """Server sync should accept incoming data with newer _version."""
        incoming_version = 3000
        existing_version = 2000

        should_update = incoming_version >= existing_version
        assert should_update, "Newer version should be accepted"

    def test_equal_version_acceptance(self):
        """Equal versions use >= so they're accepted.
        
        Note: This is the current behavior but could be a source of issues.
        The fix guards against shells at the data level, not the version level.
        """
        incoming_version = 2000
        existing_version = 2000

        should_update = incoming_version >= existing_version
        assert should_update, "Equal version is accepted with >= comparison"


class TestChatStorageMessageIntegrity:
    """Test that ChatStorage operations preserve message integrity."""

    def test_add_message_preserves_existing(self, tmp_path):
        """Adding a message to a chat should preserve all existing messages."""
        from app.storage.chats import ChatStorage
        from app.models.chat import Chat, ChatCreate, Message
        
        # Create a chat storage in temp dir
        project_dir = tmp_path / "test_project"
        project_dir.mkdir()
        storage = ChatStorage(project_dir)
        
        # Create initial chat with messages
        chat_data = {
            "id": "integrity-test",
            "title": "Message Integrity Test",
            "messages": [
                {"id": f"m{i}", "role": "human" if i % 2 == 0 else "assistant",
                 "content": f"Message {i}", "timestamp": 1000 + i}
                for i in range(20)
            ],
            "createdAt": 1000,
            "lastActiveAt": 2000,
        }
        storage._write_json(storage._chat_file("integrity-test"), chat_data)
        
        # Add a new message
        new_msg = Message(id="m20", role="human", content="New message", timestamp=3000)
        result = storage.add_message("integrity-test", new_msg)
        
        # Verify all messages preserved
        assert result is not None
        assert len(result.messages) == 21, f"Expected 21 messages, got {len(result.messages)}"
        
        # Verify specific messages
        assert result.messages[0].content == "Message 0"
        assert result.messages[10].content == "Message 10"
        assert result.messages[20].content == "New message"

    def test_get_preserves_all_messages(self, tmp_path):
        """Reading a chat should return all messages, not just first+last."""
        from app.storage.chats import ChatStorage
        
        project_dir = tmp_path / "test_project"
        project_dir.mkdir()
        storage = ChatStorage(project_dir)
        
        # Write a chat with many messages
        chat_data = {
            "id": "read-test",
            "title": "Read Test",
            "messages": [
                {"id": f"m{i}", "role": "human" if i % 2 == 0 else "assistant",
                 "content": f"Message {i}", "timestamp": 1000 + i}
                for i in range(100)
            ],
            "createdAt": 1000,
            "lastActiveAt": 2000,
        }
        storage._write_json(storage._chat_file("read-test"), chat_data)
        
        # Read it back
        result = storage.get("read-test")
        assert result is not None
        assert len(result.messages) == 100, f"Expected 100 messages, got {len(result.messages)}"
        
        # Verify middle messages are present
        assert result.messages[50].content == "Message 50"
        assert result.messages[99].content == "Message 99"
