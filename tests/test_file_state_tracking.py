"""
Regression tests for file state tracking system.
Tests the specific bug: declined diffs affecting subsequent context.
"""

import os
import tempfile
import shutil
import pytest
from typing import Dict

from app.utils.file_state_manager import FileStateManager, FileState


class TestFileStateTracking:
    """Core regression tests for the declined diff bug."""
    
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
    
    def test_initialize_conversation_preserves_existing_state(self, file_state_manager):
        """Test that initialize_conversation doesn't reset existing state by default."""
        conv_id = "test_conv_001"
        files = {"test.py": "line1\nline2\nline3"}
        
        # First initialization
        file_state_manager.initialize_conversation(conv_id, files, force_reset=True)
        
        # Modify state
        state = file_state_manager.conversation_states[conv_id]["test.py"]
        state.line_states[1] = '*'
        original_line_states = state.line_states.copy()
        
        # Second initialization without force_reset should preserve state
        file_state_manager.initialize_conversation(conv_id, files, force_reset=False)
        
        state_after = file_state_manager.conversation_states[conv_id]["test.py"]
        assert state_after.line_states == original_line_states, \
            "Line states should be preserved when force_reset=False"
    
    def test_declined_diff_scenario(self, file_state_manager, temp_dir):
        """
        THE CORE REGRESSION TEST for TK Dang's bug report.
        
        Scenario:
        1. User asks for diff A
        2. Model suggests changes to line 1
        3. User DECLINES to apply diff A (file unchanged on disk)
        4. User asks for diff B
        5. Model should see ACTUAL file content, not the declined suggestion from A
        """
        conv_id = "test_declined_diff"
        
        # Create test file
        test_file = os.path.join(temp_dir, "test.py")
        original_content = "def hello():\n    return 'World'\n"
        with open(test_file, 'w') as f:
            f.write(original_content)
        
        # Step 1: First request - initialize conversation
        files = {"test.py": original_content}
        file_state_manager.initialize_conversation(conv_id, files, force_reset=True)
        file_state_manager.mark_context_submission(conv_id)
        
        # Step 2: User asks for another change, file unchanged on disk
        # This simulates the user declining diff A
        
        # Step 3: Second request - should NOT reset state
        # Simulate extract_codebase being called again
        file_state_manager.initialize_conversation(conv_id, files, force_reset=False)
        
        # Step 4: Refresh from disk to verify file unchanged
        changed = file_state_manager.refresh_file_from_disk(conv_id, "test.py", temp_dir)
        assert changed is False, "File should be unchanged (user declined diff A)"
        
        # Step 5: Verify annotated content shows ACTUAL file state
        annotated, success = file_state_manager.get_annotated_content(conv_id, "test.py")
        assert success
        
        # Verify content matches actual file
        actual_lines = original_content.splitlines()
        for i, line in enumerate(annotated):
            content_part = line[7:]  # Skip [NNN?] (6 chars: [ + 3 digits + state + ]) and space
            assert content_part == actual_lines[i], \
                f"Line {i+1} should match actual file, not declined suggestion"
    
    def test_applied_diff_scenario(self, file_state_manager, temp_dir):
        """
        Test the scenario where user APPLIES the diff.
        This should be detected via disk refresh.
        """
        conv_id = "test_applied_diff"
        
        # Create test file
        test_file = os.path.join(temp_dir, "test.py")
        original_content = "def hello():\n    return 'World'\n"
        with open(test_file, 'w') as f:
            f.write(original_content)
        
        # Initialize conversation
        files = {"test.py": original_content}
        file_state_manager.initialize_conversation(conv_id, files, force_reset=True)
        file_state_manager.mark_context_submission(conv_id)
        
        # User applies diff - modify file on disk
        modified_content = "def hello():\n    return 'Universe'\n"
        with open(test_file, 'w') as f:
            f.write(modified_content)
        
        # Refresh should detect changes
        changed = file_state_manager.refresh_file_from_disk(conv_id, "test.py", temp_dir)
        assert changed is True, "File changes should be detected"
        
        # Verify state reflects actual file
        state = file_state_manager.conversation_states[conv_id]["test.py"]
        assert state.current_content == modified_content.splitlines()
        assert len(state.line_states) > 0, "Change markers should be set"
    
    def test_authority_message_generated_when_changes_exist(self, file_state_manager):
        """Test that authority message is generated when there are tracked changes."""
        conv_id = "test_authority"
        files = {"test.py": "line1\nline2"}
        
        file_state_manager.initialize_conversation(conv_id, files, force_reset=True)
        
        # No changes initially
        msg = file_state_manager.format_file_authority_message(conv_id)
        assert msg == "", "No authority message when no changes"
        
        # Add a change marker
        state = file_state_manager.conversation_states[conv_id]["test.py"]
        state.line_states[1] = '*'
        
        # Should now generate authority message
        msg = file_state_manager.format_file_authority_message(conv_id)
        assert "CRITICAL: FILE CONTENT AUTHORITY" in msg
        assert "test.py" in msg
    
    def test_refresh_all_files_from_disk(self, file_state_manager, temp_dir):
        """Test refreshing all files in a conversation."""
        conv_id = "test_refresh_all"
        
        # Create two test files
        file1 = os.path.join(temp_dir, "file1.py")
        file2 = os.path.join(temp_dir, "file2.py")
        
        with open(file1, 'w') as f:
            f.write("content1")
        with open(file2, 'w') as f:
            f.write("content2")
        
        files = {
            "file1.py": "content1",
            "file2.py": "content2"
        }
        
        file_state_manager.initialize_conversation(conv_id, files, force_reset=True)
        
        # Modify file1 on disk
        with open(file1, 'w') as f:
            f.write("modified1")
        
        # Refresh all
        results = file_state_manager.refresh_all_files_from_disk(conv_id, temp_dir)
        
        assert results["file1.py"] is True, "file1 should be detected as changed"
        assert results["file2.py"] is False, "file2 should be unchanged"
    
    def test_mark_context_submission_updates_baseline(self, file_state_manager):
        """Test that mark_context_submission updates the baseline for change detection."""
        conv_id = "test_baseline"
        files = {"test.py": "line1\nline2"}
        
        file_state_manager.initialize_conversation(conv_id, files, force_reset=True)
        
        # Modify current content
        state = file_state_manager.conversation_states[conv_id]["test.py"]
        state.current_content = ["line1", "line2", "line3"]
        
        # Should detect changes before marking
        assert file_state_manager.has_changes_since_last_context_submission(conv_id, "test.py")
        
        # Mark context submission
        file_state_manager.mark_context_submission(conv_id)
        
        # Should no longer detect changes
        assert not file_state_manager.has_changes_since_last_context_submission(conv_id, "test.py")
    
    def test_change_markers_format(self, file_state_manager):
        """Test that change markers follow the expected [NNN?] format."""
        conv_id = "test_markers"
        files = {"test.py": "line1\nline2\nline3"}
        
        file_state_manager.initialize_conversation(conv_id, files, force_reset=True)
        
        # Set different change markers
        state = file_state_manager.conversation_states[conv_id]["test.py"]
        state.line_states[1] = '+'  # New
        state.line_states[2] = '*'  # Modified
        # line 3 unchanged
        
        annotated, _ = file_state_manager.get_annotated_content(conv_id, "test.py")
        
        # Check the marker character at position 4 (0-indexed: [001+])
        #                                              01234
        assert annotated[0][4] == '+', f"Line 1 should be marked as new, got: {annotated[0][:6]}"
        assert annotated[1][4] == '*', f"Line 2 should be marked as modified, got: {annotated[1][:6]}"
        assert annotated[2][4] == ' ', f"Line 3 should be marked as unchanged, got: {annotated[2][:6]}"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
