import sys
import os
import unittest
import tempfile
import shutil
import logging

# Add project root to Python path
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, project_root)

from app.utils.code_util import use_git_to_apply_code_diff
from app.utils.diff_utils.parsing.diff_parser import split_combined_diff

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class TestNoDiffGitHeader(unittest.TestCase):
    """Test case for diffs without diff --git headers"""
    
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        # Set required environment variable
        os.environ['ZIYA_USER_CODEBASE_DIR'] = self.temp_dir
        
    def tearDown(self):
        shutil.rmtree(self.temp_dir)
        
    def test_split_combined_diff_no_diff_git_header(self):
        """Test that split_combined_diff correctly handles diffs without diff --git headers"""
        # Simple diff without diff --git header
        diff_content = """--- a/file.py
+++ b/file.py
@@ -1,3 +1,3 @@
 def hello():
-    print("Hello")
+    print("Hello, world!")
"""
        
        # Call the function
        result = split_combined_diff(diff_content)
        
        # Verify that the result is correct
        self.assertEqual(len(result), 1, "Should return a single diff")
        self.assertEqual(result[0], diff_content, "Should return the original diff unchanged")
        
        # Verify that no diff --git line was added
        self.assertFalse(result[0].startswith("diff --git"), 
                         "Should not add diff --git line to a diff that doesn't have one")
    
    def test_apply_diff_no_diff_git_header(self):
        """Test applying a diff without diff --git headers"""
        # Create a file to apply the diff to
        file_path = os.path.join(self.temp_dir, "test_file.py")
        with open(file_path, "w") as f:
            f.write("""def hello():
    print("Hello")
""")
        
        # Create a diff without diff --git header
        diff_content = """--- a/test_file.py
+++ b/test_file.py
@@ -1,2 +1,2 @@
 def hello():
-    print("Hello")
+    print("Hello, world!")
"""
        
        # Apply the diff
        result = use_git_to_apply_code_diff(diff_content, file_path)
        
        # Verify that the diff was applied successfully
        self.assertEqual(result["status"], "success", "Diff application should succeed")
        
        # Verify that the file was modified correctly
        with open(file_path, "r") as f:
            content = f.read()
        
        expected_content = """def hello():
    print("Hello, world!")
"""
        self.assertEqual(content, expected_content, "File content should be updated correctly")

if __name__ == "__main__":
    unittest.main()
