import unittest
import os
import json
import tempfile
import shutil
from app.utils.code_util import use_git_to_apply_code_diff

class DiffRegressionTest(unittest.TestCase):
    """Regression tests for diff application using real-world examples"""
    
    # Directory containing test cases
    TEST_CASES_DIR = os.path.join(os.path.dirname(__file__), 'diff_test_cases')
    
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        # Set required environment variable
        os.environ['ZIYA_USER_CODEBASE_DIR'] = self.temp_dir
        
    def tearDown(self):
        shutil.rmtree(self.temp_dir)
        
    def load_test_case(self, case_name):
        """Load a test case from the test cases directory"""
        case_dir = os.path.join(self.TEST_CASES_DIR, case_name)
        
        # Load metadata
        with open(os.path.join(case_dir, 'metadata.json')) as f:
            metadata = json.load(f)
            
        # Load original file
        with open(os.path.join(case_dir, 'original.py')) as f:
            original = f.read()
            
        # Load diff
        with open(os.path.join(case_dir, 'changes.diff')) as f:
            diff = f.read()
            
        # Load expected result
        with open(os.path.join(case_dir, 'expected.py')) as f:
            expected = f.read()
            
        return metadata, original, diff, expected
        
    def run_diff_test(self, case_name):
        """Run a single diff test case"""
        metadata, original, diff, expected = self.load_test_case(case_name)
        
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

    def test_all_cases(self):
        """Run all test cases found in the test cases directory"""
        for case_name in os.listdir(self.TEST_CASES_DIR):
            if os.path.isdir(os.path.join(self.TEST_CASES_DIR, case_name)):
                with self.subTest(case=case_name):
                    self.run_diff_test(case_name)

if __name__ == '__main__':
    unittest.main()
