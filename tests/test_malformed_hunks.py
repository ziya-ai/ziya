"""
Tests for malformed hunk detection and handling.
"""

import os
import tempfile
import pytest
from app.utils.diff_utils.core.exceptions import PatchApplicationError
from app.utils.diff_utils.application.patch_apply import apply_diff_with_difflib_hybrid_forced

def test_malformed_hunk_detection():
    """
    Test that malformed hunks are detected and rejected.
    """
    # Create a temporary directory for the test
    with tempfile.TemporaryDirectory() as temp_dir:
        # Create a test file
        test_file_path = os.path.join(temp_dir, "test_file.py")
        original_content = "def test_function():\n    return 'test'\n"
        original_lines = original_content.splitlines(True)
        
        # Create a malformed diff with an invalid hunk header
        malformed_diff = """--- a/test_file.py
+++ b/test_file.py
@@ -1,2 +1,3 @@
 def test_function():
+    print('test')
     return 'test'
@@ -1,2 +1,3 @@"""  # Malformed hunk header
        
        # Try to apply the diff
        with pytest.raises(PatchApplicationError) as excinfo:
            apply_diff_with_difflib_hybrid_forced(test_file_path, malformed_diff, original_lines)
        
        # Verify that the error message mentions malformed hunks
        assert "malformed" in str(excinfo.value).lower(), f"Expected error message to mention malformed hunks, got: {str(excinfo.value)}"

def test_missing_old_block():
    """
    Test that hunks with missing old_block are detected and rejected.
    """
    # Create a temporary directory for the test
    with tempfile.TemporaryDirectory() as temp_dir:
        # Create a test file
        test_file_path = os.path.join(temp_dir, "test_file.py")
        original_content = "def test_function():\n    return 'test'\n"
        original_lines = original_content.splitlines(True)
        
        # Create a malformed diff with missing old_block
        malformed_diff = """--- a/test_file.py
+++ b/test_file.py
@@ -1,0 +1,1 @@
+    print('test')
"""
        
        # Try to apply the diff
        with pytest.raises(PatchApplicationError) as excinfo:
            apply_diff_with_difflib_hybrid_forced(test_file_path, malformed_diff, original_lines)
        
        # Verify that the error message mentions malformed hunks
        assert "malformed" in str(excinfo.value).lower(), f"Expected error message to mention malformed hunks, got: {str(excinfo.value)}"

def test_missing_new_lines():
    """
    Test that hunks with missing new_lines are detected and rejected.
    """
    # Create a temporary directory for the test
    with tempfile.TemporaryDirectory() as temp_dir:
        # Create a test file
        test_file_path = os.path.join(temp_dir, "test_file.py")
        original_content = "def test_function():\n    return 'test'\n"
        original_lines = original_content.splitlines(True)
        
        # Create a malformed diff with missing new_lines
        malformed_diff = """--- a/test_file.py
+++ b/test_file.py
@@ -1,1 +0,0 @@
-def test_function():
"""
        
        # Try to apply the diff
        with pytest.raises(PatchApplicationError) as excinfo:
            apply_diff_with_difflib_hybrid_forced(test_file_path, malformed_diff, original_lines)
        
        # Verify that the error message mentions malformed hunks
        assert "malformed" in str(excinfo.value).lower(), f"Expected error message to mention malformed hunks, got: {str(excinfo.value)}"
