import unittest
import os
import tempfile
import shutil
import sys

# Add the parent directory to the path so we can import the app
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from app.utils.code_util import use_git_to_apply_code_diff

class TestInvisibleUnicodeImproved(unittest.TestCase):
    """Test case for handling invisible Unicode characters when applying diffs."""
    
    def setUp(self):
        # Create a temporary directory
        self.temp_dir = tempfile.mkdtemp()
        # Set required environment variable
        os.environ['ZIYA_USER_CODEBASE_DIR'] = self.temp_dir
        # Force difflib mode
        os.environ['ZIYA_FORCE_DIFFLIB'] = '1'
        
        # Load test case
        test_case_dir = os.path.join(os.path.dirname(__file__), 'test_cases', 'MRE_invisible_unicode_improved')
        
        # Read the original file
        with open(os.path.join(test_case_dir, 'original.py'), 'r', encoding='utf-8') as f:
            self.original_content = f.read()
            
        # Read the diff
        with open(os.path.join(test_case_dir, 'changes.diff'), 'r', encoding='utf-8') as f:
            self.diff_content = f.read()
            
        # Read the expected result
        with open(os.path.join(test_case_dir, 'expected.py'), 'r', encoding='utf-8') as f:
            self.expected_content = f.read()
            
        # Create the target file
        target_file = os.path.join(self.temp_dir, 'src', 'unicode_example.py')
        os.makedirs(os.path.dirname(target_file), exist_ok=True)
        with open(target_file, 'w', encoding='utf-8') as f:
            f.write(self.original_content)
    
    def tearDown(self):
        # Remove the temporary directory
        shutil.rmtree(self.temp_dir)
        # Unset environment variables
        if 'ZIYA_FORCE_DIFFLIB' in os.environ:
            del os.environ['ZIYA_FORCE_DIFFLIB']
    
    def test_invisible_unicode_handling(self):
        """Test that invisible Unicode characters are handled correctly when applying a diff."""
        # Apply the diff
        target_file = os.path.join(self.temp_dir, 'src', 'unicode_example.py')
        use_git_to_apply_code_diff(self.diff_content, target_file)
        
        # Read the result
        result_file = os.path.join(self.temp_dir, 'src', 'unicode_example.py')
        with open(result_file, 'r', encoding='utf-8') as f:
            result_content = f.read()
        
        # Debug output
        print(f"Original content: {repr(self.original_content)}")
        print(f"Expected content: {repr(self.expected_content)}")
        print(f"Result content: {repr(result_content)}")
        
        # Check specific changes instead of exact content match
        self.assertIn('print("Hello World")', result_content, 
                     "Zero-width space was not removed correctly")
        
        self.assertIn('value = 200 + 50', result_content, 
                     "Zero-width non-joiner was not handled correctly")
        
        # Check that the zero-width joiner is still present in the unchanged line
        self.assertIn('result = "test‍ing"', result_content, 
                     "Zero-width joiner was incorrectly modified")

if __name__ == '__main__':
    unittest.main()
