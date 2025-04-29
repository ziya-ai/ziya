import unittest
import os
import json
import tempfile
import shutil
import difflib
from app.utils.code_util import use_git_to_apply_code_diff

class TestAllDiffCases(unittest.TestCase):
    """Test all diff test cases individually"""
    
    # Directory containing test cases
    TEST_CASES_DIR = os.path.join(os.path.dirname(__file__), 'diff_test_cases')
    
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        # Set required environment variable
        os.environ['ZIYA_USER_CODEBASE_DIR'] = self.temp_dir
        self.maxDiff = None  # Show full diffs
        
    def tearDown(self):
        shutil.rmtree(self.temp_dir)
    
    def run_diff_test(self, case_name):
        """Run a single diff test case"""
        case_dir = os.path.join(self.TEST_CASES_DIR, case_name)
        
        # Skip if not a directory or missing required files
        if not os.path.isdir(case_dir):
            self.skipTest(f"{case_name} is not a directory")
            
        metadata_path = os.path.join(case_dir, 'metadata.json')
        if not os.path.exists(metadata_path):
            self.skipTest(f"{case_name} is missing metadata.json")
            
        # Load metadata
        with open(metadata_path) as f:
            metadata = json.load(f)
            
        # Check for required files
        original_path = os.path.join(case_dir, 'original.py')
        diff_path = os.path.join(case_dir, 'changes.diff')
        expected_path = os.path.join(case_dir, 'expected.py')
        
        if not os.path.exists(original_path):
            self.skipTest(f"{case_name} is missing original.py")
        if not os.path.exists(diff_path):
            self.skipTest(f"{case_name} is missing changes.diff")
        if not os.path.exists(expected_path):
            self.skipTest(f"{case_name} is missing expected.py")
            
        # Load files
        with open(original_path) as f:
            original = f.read()
        with open(diff_path) as f:
            diff = f.read()
        with open(expected_path) as f:
            expected = f.read()
            
        # Set up the test file in the temp directory
        test_file_path = os.path.join(self.temp_dir, metadata['target_file'])
        os.makedirs(os.path.dirname(test_file_path), exist_ok=True)
        
        with open(test_file_path, 'w') as f:
            f.write(original)
            
        # Apply the diff
        use_git_to_apply_code_diff(diff, metadata['target_file'])
        
        # Read the result
        with open(test_file_path) as f:
            result = f.read()
        
        # Compare with expected
        self.assertEqual(result, expected, 
                        f"Diff application for {case_name} did not produce expected result")

    def test_two_functions(self):
        self.run_diff_test('two_functions')
        
    def test_misordered_hunks(self):
        self.run_diff_test('misordered_hunks')
        
    def test_already_applied_simple(self):
        self.run_diff_test('already_applied_simple')
        
    def test_indentation_change(self):
        self.run_diff_test('indentation_change')
        
    def test_already_applied_complex(self):
        self.run_diff_test('already_applied_complex')
        
    def test_indentation_changes(self):
        self.run_diff_test('indentation_changes')
        
    def test_import_line_order(self):
        self.run_diff_test('import_line_order')
        
    def test_single_line_replace(self):
        self.run_diff_test('single_line_replace')
        
    def test_function_collision(self):
        self.run_diff_test('function_collision')
        
    def test_model_defaults_config(self):
        self.run_diff_test('model_defaults_config')
        
    def test_MRE_whitespace_only_changes(self):
        # Force difflib mode for this test
        os.environ['ZIYA_FORCE_DIFFLIB'] = '1'
        try:
            self.run_diff_test('MRE_whitespace_only_changes')
        finally:
            # Clean up environment variable
            if 'ZIYA_FORCE_DIFFLIB' in os.environ:
                del os.environ['ZIYA_FORCE_DIFFLIB']

if __name__ == '__main__':
    unittest.main()
