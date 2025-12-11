"""
Regression tests for file state tracking system.

These tests verify that:
1. File changes are tracked correctly between conversation turns
2. The model receives accurate file content from disk, not stale cached data
3. Declined diffs don't affect subsequent context generation
4. Change markers ([NNN+], [NNN*], [NNN ]) are generated correctly
5. File authority messages are included when appropriate
"""

import os
import tempfile
import shutil
import pytest
from typing import Dict, List
from unittest.mock import patch, MagicMock

from app.utils.file_state_manager import FileStateManager, FileState


class TestFileStateManager:
    """Tests for the FileStateManager class."""
    
    @pytest.fixture
    def temp_dir(self):
        """Create a temporary directory for test files."""
        temp_dir = tempfile.mkdtemp()
        yield temp_dir
        shutil.rmtree(temp_dir)
    
    @pytest.fixture
    def file_state_manager(self, temp_dir):
        """Create a fresh FileStateManager for each test."""
        manager = FileStateManager()
        manager.state_file = os.path.join(temp_dir, "test_file_states.json")
        manager.conversation_states = {}
        return manager
    
    @pytest.fixture
    def sample_files(self, temp_dir):
        """Create sample files for testing."""
        files = {}
        
        file1_path = os.path.join(temp_dir, "test_file1.py")
        file1_content = """def hello():
    return "Hello, World!"

def goodbye():
    return "Goodbye!"
"""
        with open(file1_path, 'w') as f:
            f.write(file1_content)
        files["test_file1.py"] = file1_content
        
        file2_path = os.path.join(temp_dir, "test_file2.py")
        file2_content = """class MyClass:
    def __init__(self):
        self.value = 42
    
    def get_value(self):
        return self.value
"""
        with open(file2_path, 'w') as f:
            f.write(file2_content)
        files["test_file2.py"] = file2_content
        
        return files
    
    def test_initialize_conversation_new(self, file_state_manager, sample_files):
        """Test initializing a new conversation."""
        conv_id = "test_conv_1"
        
        file_state_manager.initialize_conversation(conv_id, sample_files, force_reset=True)
        
        assert conv_id in file_state_manager.conversation_states
        assert len(file_state_manager.conversation_states[conv_id]) == 2
        
        for file_path in sample_files:
            state = file_state_manager.conversation_states[conv_id][file_path]
            assert state.path == file_path
            assert state.original_content == sample_files[file_path].splitlines()
            assert state.current_content == sample_files[file_path].splitlines()
            assert state.line_states == {}
    
    def test_initialize_conversation_preserves_existing_state(self, file_state_manager, sample_files):
        """Test that initializing an existing conversation preserves state by default."""
        conv_id = "test_conv_preserve"
        
        file_state_manager.initialize_conversation(conv_id, sample_files, force_reset=True)
        
        state = file_state_manager.conversation_states[conv_id]["test_file1.py"]
        state.line_states[1] = '*'
        original_line_states = state.line_states.copy()
        
        file_state_manager.initialize_conversation(conv_id, sample_files, force_reset=False)
        
        state_after = file_state_manager.conversation_states[conv_id]["test_file1.py"]
        assert state_after.line_states == original_line_states
    
    def test_initialize_conversation_force_reset(self, file_state_manager, sample_files):
        """Test that force_reset=True clears existing state."""
        conv_id = "test_conv_reset"
        
        file_state_manager.initialize_conversation(conv_id, sample_files, force_reset=True)
        state = file_state_manager.conversation_states[conv_id]["test_file1.py"]
        state.line_states[1] = '*'
        
        file_state_manager.initialize_conversation(conv_id, sample_files, force_reset=True)
        
        state_after = file_state_manager.conversation_states[conv_id]["test_file1.py"]
        assert state_after.line_states == {}
    
    def test_refresh_file_from_disk_detects_changes(self, file_state_manager, sample_files, temp_dir):
        """Test that refresh_file_from_disk detects when a file has changed on disk."""
        conv_id = "test_conv_refresh"
        
        file_state_manager.initialize_conversation(conv_id, sample_files, force_reset=True)
        
        file_path = os.path.join(temp_dir, "test_file1.py")
        new_content = """def hello():
    return "Hello, Modified World!"

def goodbye():
    return "Goodbye!"

def new_function():
    return "I'm new!"
"""
        with open(file_path, 'w') as f:
            f.write(new_content)
        
        changed = file_state_manager.refresh_file_from_disk(conv_id, "test_file1.py", temp_dir)
        
        assert changed is True
        
        state = file_state_manager.conversation_states[conv_id]["test_file1.py"]
        assert state.current_content == new_content.splitlines()
        assert len(state.line_states) > 0
    
    def test_refresh_file_from_disk_no_changes(self, file_state_manager, sample_files, temp_dir):
        """Test that refresh_file_from_disk returns False when file hasn't changed."""
        conv_id = "test_conv_no_change"
        
        file_state_manager.initialize_conversation(conv_id, sample_files, force_reset=True)
        
        changed = file_state_manager.refresh_file_from_disk(conv_id, "test_file1.py", temp_dir)
        
        assert changed is False
    
    def test_get_annotated_content_unchanged(self, file_state_manager, sample_files):
        """Test that annotated content shows unchanged markers for unmodified files."""
        conv_id = "test_conv_annotated"
        
        file_state_manager.initialize_conversation(conv_id, sample_files, force_reset=True)
        
        annotated_lines, success = file_state_manager.get_annotated_content(conv_id, "test_file1.py")
        
        assert success is True
        assert len(annotated_lines) > 0
        
        for line in annotated_lines:
            assert line[4] == ' ', f"Expected unchanged marker, got: {line}"
    
    def test_get_annotated_content_with_changes(self, file_state_manager, sample_files):
        """Test that annotated content shows correct markers for modified files."""
        conv_id = "test_conv_annotated_changes"
        
        file_state_manager.initialize_conversation(conv_id, sample_files, force_reset=True)
        
        state = file_state_manager.conversation_states[conv_id]["test_file1.py"]
        state.line_states[1] = '+'
        state.line_states[2] = '*'
        
        annotated_lines, success = file_state_manager.get_annotated_content(conv_id, "test_file1.py")
        
        assert success is True
        assert annotated_lines[0][4] == '+'
        assert annotated_lines[1][4] == '*'
        assert annotated_lines[2][4] == ' '
    
    def test_format_file_authority_message(self, file_state_manager, sample_files):
        """Test that file authority message is generated when there are tracked changes."""
        conv_id = "test_conv_authority"
        
        file_state_manager.initialize_conversation(conv_id, sample_files, force_reset=True)
        
        msg = file_state_manager.format_file_authority_message(conv_id)
        assert msg == ""
        
        state = file_state_manager.conversation_states[conv_id]["test_file1.py"]
        state.line_states[1] = '*'
        
        msg = file_state_manager.format_file_authority_message(conv_id)
        assert "CRITICAL: FILE CONTENT AUTHORITY" in msg
        assert "test_file1.py" in msg
    
    def test_mark_context_submission_updates_baseline(self, file_state_manager, sample_files):
        """Test that mark_context_submission updates the baseline for change detection."""
        conv_id = "test_conv_submission"
        
        file_state_manager.initialize_conversation(conv_id, sample_files, force_reset=True)
        
        state = file_state_manager.conversation_states[conv_id]["test_file1.py"]
        new_lines = state.current_content.copy()
        new_lines.append("# New line")
        state.current_content = new_lines
        
        assert file_state_manager.has_changes_since_last_context_submission(conv_id, "test_file1.py")
        
        file_state_manager.mark_context_submission(conv_id)
        
        assert not file_state_manager.has_changes_since_last_context_submission(conv_id, "test_file1.py")
    
    def test_declined_diff_scenario(self, file_state_manager, sample_files, temp_dir):
        """
        Test the scenario where:
        1. User asks for diff A
        2. Model suggests changes
        3. User declines to apply
        4. User asks for diff B
        5. Context should show ACTUAL file content, not the suggested changes from A
        
        This is the core regression test for the bug described in the Slack conversation.
        """
        conv_id = "test_declined_diff"
        
        file_state_manager.initialize_conversation(conv_id, sample_files, force_reset=True)
        file_state_manager.mark_context_submission(conv_id)
        
        original_content = sample_files["test_file1.py"]
        
        # User asks for another change - refresh from disk
        changed = file_state_manager.refresh_file_from_disk(conv_id, "test_file1.py", temp_dir)
        
        assert changed is False
        
        state = file_state_manager.conversation_states[conv_id]["test_file1.py"]
        assert state.current_content == original_content.splitlines()
        
        annotated_lines, success = file_state_manager.get_annotated_content(conv_id, "test_file1.py")
        assert success
        
        for i, line in enumerate(annotated_lines):
            content_part = line[6:]
            expected_line = original_content.splitlines()[i] if i < len(original_content.splitlines()) else ""
            assert content_part == expected_line
    
    def test_applied_diff_scenario(self, file_state_manager, sample_files, temp_dir):
        """
        Test the scenario where user APPLIES the diff.
        """
        conv_id = "test_applied_diff"
        
        file_state_manager.initialize_conversation(conv_id, sample_files, force_reset=True)
        file_state_manager.mark_context_submission(conv_id)
        
        file_path = os.path.join(temp_dir, "test_file1.py")
        modified_content = """def hello():
    return "Hello, Modified World!"

def goodbye():
    return "Goodbye!"
"""
        with open(file_path, 'w') as f:
            f.write(modified_content)
        
        changed = file_state_manager.refresh_file_from_disk(conv_id, "test_file1.py", temp_dir)
        
        assert changed is True
        
        state = file_state_manager.conversation_states[conv_id]["test_file1.py"]
        assert state.current_content == modified_content.splitlines()
        assert len(state.line_states) > 0
    
    def test_external_edit_detection(self, file_state_manager, sample_files, temp_dir):
        """Test that external edits are detected when conversation resumes."""
        conv_id = "test_external_edit"
        
        file_state_manager.initialize_conversation(conv_id, sample_files, force_reset=True)
        file_state_manager.mark_context_submission(conv_id)
        
        file_path = os.path.join(temp_dir, "test_file1.py")
        externally_edited_content = """def hello():
    return "Hello, World!"

def goodbye():
    return "Goodbye!"

# User added this comment while working in their IDE
def user_added_function():
    pass
"""
        with open(file_path, 'w') as f:
            f.write(externally_edited_content)
        
        results = file_state_manager.refresh_all_files_from_disk(conv_id, temp_dir)
        
        assert results["test_file1.py"] is True
        
        annotated_lines, success = file_state_manager.get_annotated_content(conv_id, "test_file1.py")
        assert success
        
        full_content = "\n".join(line[6:] for line in annotated_lines)
        assert "user_added_function" in full_content


class TestConversationIdHandling:
    """Tests for proper conversation ID handling."""
    
    @pytest.fixture
    def file_state_manager(self):
        manager = FileStateManager()
        manager.conversation_states = {}
        return manager
    
    def test_conversation_id_consistency(self, file_state_manager):
        """Test that the same conversation_id consistently accesses the same state."""
        conv_id = "consistent_conv_123"
        files = {"file1.py": "content1"}
        
        file_state_manager.initialize_conversation(conv_id, files, force_reset=True)
        file_state_manager.conversation_states[conv_id]["file1.py"].line_states[1] = '*'
        
        file_state_manager.initialize_conversation(conv_id, files, force_reset=False)
        
        assert file_state_manager.conversation_states[conv_id]["file1.py"].line_states[1] == '*'
    
    def test_different_conversations_isolated(self, file_state_manager):
        """Test that different conversation IDs have isolated state."""
        files = {"file1.py": "content1"}
        
        file_state_manager.initialize_conversation("conv_a", files, force_reset=True)
        file_state_manager.initialize_conversation("conv_b", files, force_reset=True)
        
        file_state_manager.conversation_states["conv_a"]["file1.py"].line_states[1] = '*'
        
        assert file_state_manager.conversation_states["conv_b"]["file1.py"].line_states == {}


class TestChangeMarkerGeneration:
    """Tests for change marker generation."""
    
    @pytest.fixture
    def file_state_manager(self):
        manager = FileStateManager()
        manager.conversation_states = {}
        return manager
    
    def test_marker_format(self, file_state_manager):
        """Test that markers follow the expected [NNN?] format."""
        conv_id = "marker_format_test"
        files = {"test.py": "line1\nline2\nline3"}
        
        file_state_manager.initialize_conversation(conv_id, files, force_reset=True)
        
        state = file_state_manager.conversation_states[conv_id]["test.py"]
        state.line_states[1] = '+'
        state.line_states[2] = '*'
        
        annotated_lines, _ = file_state_manager.get_annotated_content(conv_id, "test.py")
        
        assert annotated_lines[0].startswith("[001+]")
        assert annotated_lines[1].startswith("[002*]")
        assert annotated_lines[2].startswith("[003 ]")
    
    def test_markers_survive_refresh(self, file_state_manager, tmp_path):
        """Test that change markers are preserved through file refresh when content unchanged."""
        conv_id = "markers_survive_test"
        
        test_file = tmp_path / "test.py"
        test_file.write_text("line1\nline2\nline3")
        
        files = {"test.py": "line1\nline2\nline3"}
        file_state_manager.initialize_conversation(conv_id, files, force_reset=True)
        
        state = file_state_manager.conversation_states[conv_id]["test.py"]
        state.line_states[1] = '*'
        
        file_state_manager.refresh_file_from_disk(conv_id, "test.py", str(tmp_path))
        
        assert file_state_manager.conversation_states[conv_id]["test.py"].line_states[1] == '*'


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
