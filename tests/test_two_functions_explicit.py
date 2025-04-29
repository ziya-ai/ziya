import unittest
import os
import tempfile
import shutil
import sys
import logging

# Add the parent directory to the path so we can import the app
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from app.utils.diff_utils.pipeline.pipeline_manager import apply_diff_pipeline

# Configure logging for the test
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger("TestTwoFunctions")

class TestTwoFunctions(unittest.TestCase):
    """Test case for the 'two_functions' scenario."""

    def setUp(self):
        # Create a temporary directory
        self.temp_dir = tempfile.mkdtemp()
        # Set required environment variable
        os.environ['ZIYA_USER_CODEBASE_DIR'] = self.temp_dir
        # Force difflib mode
        os.environ['ZIYA_FORCE_DIFFLIB'] = '1'

        # Load test case
        test_case_dir = os.path.join(os.path.dirname(__file__), 'diff_test_cases', 'two_functions')        # Read the original file
        with open(os.path.join(test_case_dir, 'original.py'), 'r', encoding='utf-8') as f:
            self.original_content = f.read()

        # Read the diff
        with open(os.path.join(test_case_dir, 'changes.diff'), 'r', encoding='utf-8') as f:
            self.diff_content = f.read()

        # Read the expected result
        with open(os.path.join(test_case_dir, 'expected.py'), 'r', encoding='utf-8') as f:
            self.expected_content = f.read()

        # Create the target file
        self.target_file = os.path.join(self.temp_dir, 'test.py')
        os.makedirs(os.path.dirname(self.target_file), exist_ok=True)
        with open(self.target_file, 'w', encoding='utf-8') as f:
            f.write(self.original_content)

    def tearDown(self):
        # Remove the temporary directory
        shutil.rmtree(self.temp_dir)
        # Unset environment variables
        if 'ZIYA_FORCE_DIFFLIB' in os.environ:
            del os.environ['ZIYA_FORCE_DIFFLIB']

    def test_two_functions_diff_apply(self):
        """Test applying the diff for the 'two_functions' case."""
        logger.info("Starting test_two_functions_diff_apply")
        try:
            # Apply the diff using the pipeline
            result_dict = apply_diff_pipeline(self.diff_content, self.target_file)
            logger.info(f"Pipeline result: {result_dict}")

            # Read the resulting file content
            with open(self.target_file, 'r', encoding='utf-8') as f:
                result_content = f.read()            # Compare the result with the expected content
            self.assertEqual(result_content.strip(), self.expected_content.strip(),
                             "The applied diff did not produce the expected file content.")

        except Exception as e:
            logger.exception("Exception occurred during test execution")
            self.fail(f"Test failed with exception: {e}")

if __name__ == '__main__':
    # You can run this specific test file directly if needed
    # Example: python tests/test_two_functions_explicit.py
    suite = unittest.TestSuite()
    suite.addTest(unittest.makeSuite(TestTwoFunctions))
    runner = unittest.TextTestRunner(verbosity=2)
    runner.run(suite)
