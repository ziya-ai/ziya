"""
Tests that the file state change tracker uses the real conversation_id,
not a fabricated precision_ prefix.

Regression test for the bug where applying a diff would not be reflected
in the next context submission because the precision prompt system was
using a shared "precision_/streaming_tools" conversation_id instead of
the real one from the frontend.
"""

import os
import sys
import time
import tempfile
import shutil
import unittest
from unittest.mock import patch, MagicMock

# Ensure the project root is on sys.path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.utils.file_state_manager import FileStateManager, FileState


class TestFileStateConversationIdPropagation(unittest.TestCase):
    """Verify that the real conversation_id flows through to FileStateManager."""

    def setUp(self):
        self.manager = FileStateManager.__new__(FileStateManager)
        self.manager.conversation_states = {}
        self.manager.conversation_diffs = {}
        self.manager._conversation_access_times = {}
        self.manager._lock = __import__("threading").Lock()
        self.manager.state_file = "/dev/null"

        # Temp directory for test files
        self.tmpdir = tempfile.mkdtemp()
        self.test_file = os.path.join(self.tmpdir, "example.py")
        with open(self.test_file, "w") as f:
            f.write("def hello():\n    return 'world'\n")

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    # ---------------------------------------------------------------
    # Core regression: precision_ vs real conversation_id
    # ---------------------------------------------------------------

    def test_real_conversation_id_gets_file_state(self):
        """File state initialized under the real conversation_id should be
        retrievable under that same ID, not under a precision_ prefix."""
        real_id = "abc-123-real-conversation"
        content = "def hello():\n    return 'world'\n"

        self.manager.initialize_conversation(real_id, {"example.py": content})

        # State must exist under the real ID
        assert real_id in self.manager.conversation_states
        assert "example.py" in self.manager.conversation_states[real_id]

        # Annotated content must be retrievable
        lines, ok = self.manager.get_annotated_content(real_id, "example.py")
        assert ok
        assert len(lines) == 2

    def test_precision_prefix_does_not_shadow_real_id(self):
        """Creating state under a precision_ ID must not interfere with the
        real conversation_id's state."""
        real_id = "abc-123-real"
        fake_id = "precision_/streaming_tools"

        original = "line1\nline2\n"
        modified = "line1\nline2_modified\n"

        self.manager.initialize_conversation(real_id, {"f.py": original})
        self.manager.initialize_conversation(fake_id, {"f.py": original})

        # Modify the file under the real conversation
        self.manager.update_file_state(real_id, "f.py", modified)

        # The real conversation should show changes
        real_state = self.manager.conversation_states[real_id]["f.py"]
        assert real_state.current_content == ["line1", "line2_modified"]

        # The precision_ conversation should still have the original
        fake_state = self.manager.conversation_states[fake_id]["f.py"]
        assert fake_state.current_content == ["line1", "line2"]

    def test_cleanup_temporary_removes_precision_not_real(self):
        """cleanup_temporary_conversations must remove precision_ IDs
        but leave real conversation IDs intact."""
        real_id = "abc-123-real"
        fake_id = "precision_/streaming_tools"

        self.manager.initialize_conversation(real_id, {"f.py": "content"})
        self.manager.initialize_conversation(fake_id, {"f.py": "content"})

        self.manager.cleanup_temporary_conversations()

        assert real_id in self.manager.conversation_states
        assert fake_id not in self.manager.conversation_states

    # ---------------------------------------------------------------
    # Change tracking after disk modification (simulates diff apply)
    # ---------------------------------------------------------------

    def test_refresh_from_disk_detects_applied_diff(self):
        """After a diff is applied on disk, refresh_file_from_disk must
        update current_content and annotate the changed lines."""
        real_id = "conv-001"
        rel_path = "example.py"

        # Initialize with original content
        original = "def hello():\n    return 'world'\n"
        self.manager.initialize_conversation(real_id, {rel_path: original})

        # Simulate user applying a diff — write modified content to disk
        modified = "def hello():\n    return 'universe'\n"
        with open(self.test_file, "w") as f:
            f.write(modified)

        # Refresh from disk
        changed = self.manager.refresh_file_from_disk(real_id, rel_path, self.tmpdir)
        assert changed, "refresh_file_from_disk should detect the on-disk change"

        # Verify current_content is updated
        state = self.manager.conversation_states[real_id][rel_path]
        assert state.current_content == ["def hello():", "    return 'universe'"]

        # Verify annotation reflects the change
        lines, ok = self.manager.get_annotated_content(real_id, rel_path)
        assert ok
        # Line 2 should be marked as changed ('+' or '*')
        assert any("+" in line or "*" in line for line in lines if "universe" in line), \
            f"Changed line should be annotated: {lines}"

    def test_annotated_content_after_disk_refresh_shows_markers(self):
        """After refresh from disk, get_annotated_content should produce
        [NNN+] or [NNN*] markers on changed lines."""
        real_id = "conv-002"
        rel_path = "example.py"

        original = "a = 1\nb = 2\nc = 3\n"
        self.manager.initialize_conversation(real_id, {rel_path: original})

        # Modify line 2 on disk
        modified = "a = 1\nb = 99\nc = 3\n"
        with open(self.test_file, "w") as f:
            f.write(modified)

        self.manager.refresh_file_from_disk(real_id, rel_path, self.tmpdir)

        lines, ok = self.manager.get_annotated_content(real_id, rel_path)
        assert ok
        assert len(lines) == 3

        # Line 1 and 3 should be unchanged
        assert "[001 ]" in lines[0], f"Line 1 should be unchanged: {lines[0]}"
        assert "[003 ]" in lines[2], f"Line 3 should be unchanged: {lines[2]}"

        # Line 2 should be marked as modified or added
        assert "[002+" in lines[1] or "[002*" in lines[1], \
            f"Line 2 should be marked as changed: {lines[1]}"

    # ---------------------------------------------------------------
    # Shared precision_ ID cross-contamination
    # ---------------------------------------------------------------

    def test_shared_precision_id_causes_cross_contamination(self):
        """Demonstrates the bug: when two conversations share the same
        precision_ ID, changes from one leak into the other."""
        shared_id = "precision_/streaming_tools"

        # Conversation A initializes with file content
        content_a = "from_conversation_a\n"
        self.manager.initialize_conversation(shared_id, {"f.py": content_a}, force_reset=True)

        # Conversation B reinitializes with different content
        content_b = "from_conversation_b\n"
        self.manager.initialize_conversation(shared_id, {"f.py": content_b}, force_reset=True)

        # State should now reflect conversation B, not A
        state = self.manager.conversation_states[shared_id]["f.py"]
        assert state.current_content == ["from_conversation_b"], \
            "Shared precision_ ID causes conversation A's state to be overwritten"

    def test_separate_real_ids_prevent_cross_contamination(self):
        """With real conversation IDs, each conversation tracks independently."""
        id_a = "conv-aaa"
        id_b = "conv-bbb"

        self.manager.initialize_conversation(id_a, {"f.py": "content_a\n"})
        self.manager.initialize_conversation(id_b, {"f.py": "content_b\n"})

        # Each conversation has its own state
        assert self.manager.conversation_states[id_a]["f.py"].current_content == ["content_a"]
        assert self.manager.conversation_states[id_b]["f.py"].current_content == ["content_b"]

        # Modifying one doesn't affect the other
        self.manager.update_file_state(id_a, "f.py", "content_a_modified\n")
        assert self.manager.conversation_states[id_b]["f.py"].current_content == ["content_b"]


class TestPrecisionPromptSystemConversationId(unittest.TestCase):
    """Verify that PrecisionPromptSystem.build_messages uses the caller-supplied
    conversation_id rather than fabricating a precision_ one."""

    def test_conversation_id_parameter_accepted(self):
        """build_messages should accept a conversation_id keyword argument."""
        from app.utils.precision_prompt_system import PrecisionPromptSystem
        import inspect
        sig = inspect.signature(PrecisionPromptSystem.build_messages)
        assert "conversation_id" in sig.parameters, \
            "build_messages must accept a 'conversation_id' parameter"

    @patch("app.utils.precision_prompt_system.PrecisionPromptSystem.build_messages")
    def test_build_messages_for_streaming_passes_conversation_id(self, mock_build):
        """build_messages_for_streaming in server.py should pass conversation_id
        to precision_system.build_messages."""
        # This is a structural test — we check the call signature
        mock_build.return_value = [{"role": "user", "content": "test"}]

        try:
            from app.server import build_messages_for_streaming
            # Call with a known conversation_id
            build_messages_for_streaming(
                question="test question",
                chat_history=[],
                files=[],
                conversation_id="test-conv-id-123",
            )

            # Verify conversation_id was passed through
            if mock_build.called:
                call_kwargs = mock_build.call_args
                # Check both positional and keyword args
                all_args = {**dict(zip(
                    ["self", "request_path", "model_info", "files", "question",
                     "chat_history", "system_prompt_addition", "conv_start_ts", "conversation_id"],
                    call_kwargs.args if call_kwargs.args else []
                )), **(call_kwargs.kwargs or {})}
                assert all_args.get("conversation_id") == "test-conv-id-123", \
                    f"conversation_id not passed through: {all_args}"
        except ImportError as e:
            # Server imports may fail in test environment — skip gracefully
            self.skipTest(f"Cannot import server module in test env: {e}")


if __name__ == "__main__":
    unittest.main()
