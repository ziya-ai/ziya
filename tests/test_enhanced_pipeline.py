"""
Tests for the enhanced pipeline functionality.

This module tests the enhanced pipeline functionality to ensure that
it correctly applies diffs with improved error tracking and fuzzy matching.
"""

import unittest
import os
import tempfile
import shutil
from unittest.mock import patch, MagicMock

from app.utils.diff_utils.pipeline.enhanced_pipeline_manager import apply_diff_pipeline_with_enhancements

class TestEnhancedPipeline(unittest.TestCase):
    """Test case for enhanced pipeline functionality."""
    
    def setUp(self):
        """Set up test environment."""
        # Create a temporary directory for test files
        self.test_dir = tempfile.mkdtemp()
        
        # Create a test file
        self.test_file = os.path.join(self.test_dir, "test.py")
        with open(self.test_file, "w") as f:
            f.write("def main():\n    x = 1\n    return x\n")
        
        # Save the original environment variable
        self.original_env = os.environ.get('ZIYA_USE_ENHANCED_MATCHING')
    
    def tearDown(self):
        """Clean up test environment."""
        # Remove the temporary directory and its contents
        shutil.rmtree(self.test_dir)
        
        # Restore the original environment variable
        if self.original_env is not None:
            os.environ['ZIYA_USE_ENHANCED_MATCHING'] = self.original_env
        else:
            if 'ZIYA_USE_ENHANCED_MATCHING' in os.environ:
                del os.environ['ZIYA_USE_ENHANCED_MATCHING']
    
    def test_standard_pipeline_fallback(self):
        """Test fallback to standard pipeline when enhanced matching is disabled."""
        # Ensure enhanced matching is disabled
        if 'ZIYA_USE_ENHANCED_MATCHING' in os.environ:
            del os.environ['ZIYA_USE_ENHANCED_MATCHING']
        
        # Create a simple diff
        diff = """diff --git a/test.py b/test.py
index 1234567..abcdef0 100644
--- a/test.py
+++ b/test.py
@@ -1,3 +1,3 @@
 def main():
     x = 1
-    return x
+    return x + 1
"""
        
        # Mock the standard pipeline
        with patch('app.utils.diff_utils.pipeline.enhanced_pipeline_manager.apply_diff_pipeline') as mock_pipeline:
            # Set up the mock to return a success result
            mock_pipeline.return_value = {
                "status": "success",
                "message": "Changes applied successfully.",
                "succeeded": [1],
                "failed": [],
                "already_applied": [],
                "changes_written": True
            }
            
            # Call the enhanced pipeline
            result = apply_diff_pipeline_with_enhancements(diff, self.test_file)
            
            # Check that the standard pipeline was called
            mock_pipeline.assert_called_once()
            
            # Check that the result includes enhanced error information
            self.assertIn("enhanced_errors", result)
            self.assertEqual(result["status"], "success")
    
    def test_enhanced_matching_enabled(self):
        """Test using enhanced matching when enabled."""
        # Enable enhanced matching
        os.environ['ZIYA_USE_ENHANCED_MATCHING'] = '1'
        
        # Create a simple diff
        diff = """diff --git a/test.py b/test.py
index 1234567..abcdef0 100644
--- a/test.py
+++ b/test.py
@@ -1,3 +1,3 @@
 def main():
     x = 1
-    return x
+    return x + 1
"""
        
        # Mock the enhanced matching function
        with patch('app.utils.diff_utils.pipeline.enhanced_pipeline_manager.apply_diff_with_enhanced_matching_wrapper') as mock_enhanced:
            # Set up the mock to return a modified content
            mock_enhanced.return_value = "def main():\n    x = 1\n    return x + 1\n"
            
            # Call the enhanced pipeline
            result = apply_diff_pipeline_with_enhancements(diff, self.test_file)
            
            # Check that the enhanced matching function was called
            mock_enhanced.assert_called_once()
            
            # Check that the result indicates success
            self.assertEqual(result["status"], "success")
            self.assertIn("enhanced_errors", result)
    
    def test_enhanced_matching_fallback(self):
        """Test fallback to standard pipeline when enhanced matching fails."""
        # Enable enhanced matching
        os.environ['ZIYA_USE_ENHANCED_MATCHING'] = '1'
        
        # Create a simple diff
        diff = """diff --git a/test.py b/test.py
index 1234567..abcdef0 100644
--- a/test.py
+++ b/test.py
@@ -1,3 +1,3 @@
 def main():
     x = 1
-    return x
+    return x + 1
"""
        
        # Mock the enhanced matching function to raise an exception
        with patch('app.utils.diff_utils.pipeline.enhanced_pipeline_manager.apply_diff_with_enhanced_matching_wrapper') as mock_enhanced:
            mock_enhanced.side_effect = Exception("Enhanced matching failed")
            
            # Mock the standard pipeline
            with patch('app.utils.diff_utils.pipeline.enhanced_pipeline_manager.apply_diff_pipeline') as mock_pipeline:
                # Set up the mock to return a success result
                mock_pipeline.return_value = {
                    "status": "success",
                    "message": "Changes applied successfully.",
                    "succeeded": [1],
                    "failed": [],
                    "already_applied": [],
                    "changes_written": True
                }
                
                # Call the enhanced pipeline
                result = apply_diff_pipeline_with_enhancements(diff, self.test_file)
                
                # Check that both functions were called
                mock_enhanced.assert_called_once()
                mock_pipeline.assert_called_once()
                
                # Check that the result indicates success
                self.assertEqual(result["status"], "success")
                self.assertIn("enhanced_errors", result)

if __name__ == "__main__":
    unittest.main()
