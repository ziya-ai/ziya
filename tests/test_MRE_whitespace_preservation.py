import unittest
import os
import tempfile
import shutil
import sys

# Add the parent directory to the path so we can import the app
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from app.utils.code_util import use_git_to_apply_code_diff

class TestWhitespacePreservation(unittest.TestCase):
    """Test case for preserving whitespace when applying diffs."""
    
    def setUp(self):
        # Create a temporary directory
        self.temp_dir = tempfile.mkdtemp()
        # Set required environment variable
        os.environ['ZIYA_USER_CODEBASE_DIR'] = self.temp_dir
        
        # Load test case
        test_case_dir = os.path.join(os.path.dirname(__file__), 'test_cases', 'MRE_whitespace_preservation')
        
        # Read the original file
        with open(os.path.join(test_case_dir, 'original.py'), 'r') as f:
            self.original_content = f.read()
            
        # Read the diff
        with open(os.path.join(test_case_dir, 'changes.diff'), 'r') as f:
            self.diff_content = f.read()
            
        # Read the expected result
        with open(os.path.join(test_case_dir, 'expected.py'), 'r') as f:
            self.expected_content = f.read()
            
        # Create the target file
        target_file = os.path.join(self.temp_dir, 'src', 'whitespace_example.py')
        os.makedirs(os.path.dirname(target_file), exist_ok=True)
        with open(target_file, 'w') as f:
            f.write(self.original_content)
    
    def tearDown(self):
        # Remove the temporary directory
        shutil.rmtree(self.temp_dir)
    
    def test_whitespace_preservation(self):
        """Test that whitespace patterns are preserved when applying a diff."""
        # Apply the diff
        target_file = os.path.join(self.temp_dir, 'src', 'whitespace_example.py')
        use_git_to_apply_code_diff(self.diff_content, target_file)
        
        # Read the result
        result_file = os.path.join(self.temp_dir, 'src', 'whitespace_example.py')
        with open(result_file, 'r') as f:
            result_content = f.read()
        
        # Check that the result matches the expected content exactly
        self.assertEqual(result_content, self.expected_content, 
                        "Whitespace was not preserved correctly")
        
        # Verify that tab characters are present in the result
        self.assertIn('\t', result_content, 
                     "Tab characters were not preserved")
        
        # Count the number of tab characters
        tab_count = result_content.count('\t')
        expected_tab_count = self.expected_content.count('\t')
        self.assertEqual(tab_count, expected_tab_count,
                        f"Expected {expected_tab_count} tab characters, got {tab_count}")
        
        # Check that the specific line with tab indentation is preserved
        tab_indented_line = "\tresult = value * 2"
        self.assertIn(tab_indented_line, result_content,
                     "Tab-indented line was not preserved correctly")

if __name__ == '__main__':
    unittest.main()
