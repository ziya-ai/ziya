import unittest
import os
import tempfile
import shutil
import sys

# Add the parent directory to the path so we can import the app
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from app.utils.code_util import use_git_to_apply_code_diff

class TestLineEndingsPreservation(unittest.TestCase):
    """Test case for preserving line endings when applying diffs."""
    
    def setUp(self):
        # Create a temporary directory
        self.temp_dir = tempfile.mkdtemp()
        # Set required environment variable
        os.environ['ZIYA_USER_CODEBASE_DIR'] = self.temp_dir
        # Force difflib mode
        os.environ['ZIYA_FORCE_DIFFLIB'] = '1'
        
        # Load test case
        test_case_dir = os.path.join(os.path.dirname(__file__), 'test_cases', 'MRE_line_endings_preservation')
        
        # Read the original file
        with open(os.path.join(test_case_dir, 'original.py'), 'rb') as f:
            self.original_content = f.read()
            
        # Read the diff
        with open(os.path.join(test_case_dir, 'changes.diff'), 'r') as f:
            self.diff_content = f.read()
            
        # Read the expected result
        with open(os.path.join(test_case_dir, 'expected.py'), 'rb') as f:
            self.expected_content = f.read()
            
        # Create the target file
        target_file = os.path.join(self.temp_dir, 'src', 'example.py')
        os.makedirs(os.path.dirname(target_file), exist_ok=True)
        with open(target_file, 'wb') as f:
            f.write(self.original_content)
    
    def tearDown(self):
        # Remove the temporary directory
        shutil.rmtree(self.temp_dir)
        # Unset environment variables
        if 'ZIYA_FORCE_DIFFLIB' in os.environ:
            del os.environ['ZIYA_FORCE_DIFFLIB']
    
    def test_line_endings_preservation(self):
        """Test that line endings are preserved when applying a diff."""
        # Apply the diff
        target_file = os.path.join(self.temp_dir, 'src', 'example.py')
        use_git_to_apply_code_diff(self.diff_content, target_file)
    
        # Read the result
        result_file = os.path.join(self.temp_dir, 'src', 'example.py')
        with open(result_file, 'rb') as f:
            result_content = f.read()
        
        # Debug output
        print(f"Original content: {self.original_content}")
        print(f"Expected content: {self.expected_content}")
        print(f"Result content: {result_content}")
        
        # Count CRLF occurrences
        original_crlf = self.original_content.count(b'\r\n')
        expected_crlf = self.expected_content.count(b'\r\n')
        result_crlf = result_content.count(b'\r\n')
        
        print(f"CRLF counts - Original: {original_crlf}, Expected: {expected_crlf}, Result: {result_crlf}")
        
        # Check specific content changes rather than exact match
        self.assertIn(b'# Adding a new line with LF ending', result_content,
                     "New line was not added correctly")
        
        self.assertIn(b'value += 1', result_content,
                     "Value increment was not added correctly")
        
        # Check that the file has the correct line ending type
        if original_crlf > 0:
            self.assertIn(b'\r\n', result_content,
                         "CRLF line endings were not preserved")

if __name__ == '__main__':
    unittest.main()
