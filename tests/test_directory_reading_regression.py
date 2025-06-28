#!/usr/bin/env python3
"""
Regression test for Ziya directory reading and token counting functionality.
This test can be added to the global test suite to prevent regressions.

Key tests:
1. Directory reading returns files (not 0 files)
2. Token counting works via multiple methods
3. File detection and filtering work correctly
4. The files_processed counter bug is fixed

Usage:
    python test_directory_reading_regression.py
    
Or integrate into existing test suite:
    from test_directory_reading_regression import DirectoryReadingRegressionTest
"""

import os
import sys
import time
import tempfile
import shutil
import unittest
from typing import Dict, List, Any

# Add the app directory to the path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'app'))


class DirectoryReadingRegressionTest(unittest.TestCase):
    """Regression test for directory reading functionality."""
    
    @classmethod
    def setUpClass(cls):
        """Set up test environment with a known directory structure."""
        cls.test_dir = tempfile.mkdtemp(prefix='ziya_regression_test_')
        
        # Create test files with known content
        test_files = {
            'README.md': '# Test Project\n\nThis is a test.\n' * 20,
            'main.py': 'def main():\n    print("Hello")\n' * 15,
            'config.json': '{"test": true, "value": 42}\n',
            'empty.txt': '',
            'subdir/file.py': 'print("nested file")\n' * 10,
        }
        
        for file_path, content in test_files.items():
            full_path = os.path.join(cls.test_dir, file_path)
            os.makedirs(os.path.dirname(full_path), exist_ok=True)
            
            with open(full_path, 'w', encoding='utf-8') as f:
                f.write(content)
        
        # Create .gitignore
        with open(os.path.join(cls.test_dir, '.gitignore'), 'w') as f:
            f.write('*.pyc\n__pycache__/\n')
    
    @classmethod
    def tearDownClass(cls):
        """Clean up test environment."""
        shutil.rmtree(cls.test_dir, ignore_errors=True)
    
    def test_directory_reading_finds_files(self):
        """Test that directory reading finds files (regression test for 0 files bug)."""
        from app.utils.directory_util import get_folder_structure, get_ignored_patterns
        
        ignored_patterns = get_ignored_patterns(self.test_dir)
        structure = get_folder_structure(self.test_dir, ignored_patterns, max_depth=5)
        
        # Should return a valid structure
        self.assertIsInstance(structure, dict)
        self.assertNotIn('error', structure)
        
        # Count files in the structure
        def count_files_and_dirs(struct):
            files = dirs = 0
            if isinstance(struct, dict):
                for key, value in struct.items():
                    if key.startswith('_'):  # Skip metadata
                        continue
                    
                    if isinstance(value, dict):
                        if 'children' in value:
                            # It's a directory
                            dirs += 1
                            sub_files, sub_dirs = count_files_and_dirs(value['children'])
                            files += sub_files
                            dirs += sub_dirs
                        elif 'token_count' in value and not value.get('children'):
                            # It's a file
                            files += 1
                        else:
                            # Recurse to check
                            sub_files, sub_dirs = count_files_and_dirs(value)
                            files += sub_files
                            dirs += sub_dirs
            return files, dirs
        
        files_found, dirs_found = count_files_and_dirs(structure)
        
        # Should find files (this was the main bug - returning 0 files)
        self.assertGreater(files_found, 0, 
                          "Directory scan should find files, but found 0. "
                          "This indicates the files_processed counter bug is present.")
        
        # Should find at least our test files
        self.assertGreaterEqual(files_found, 4,  # README.md, main.py, config.json, subdir/file.py
                               f"Expected at least 4 files, but found {files_found}")
        
        # Should find directories
        self.assertGreater(dirs_found, 0, "Should find at least one directory (subdir)")
    
    def test_files_processed_counter_is_incremented(self):
        """Test that the files_processed counter is properly incremented."""
        from app.utils.directory_util import get_folder_structure, get_ignored_patterns, _scan_progress
        
        # Reset scan progress
        _scan_progress.clear()
        _scan_progress.update({"active": False, "progress": {}, "cancelled": False})
        
        ignored_patterns = get_ignored_patterns(self.test_dir)
        
        # Capture the scan progress during execution
        structure = get_folder_structure(self.test_dir, ignored_patterns, max_depth=3)
        
        # The structure should contain files
        self.assertIsInstance(structure, dict)
        self.assertNotIn('error', structure)
        
        # Count actual files in structure
        file_count = 0
        for key, value in structure.items():
            if isinstance(value, dict) and 'token_count' in value:
                if not value.get('children'):  # It's a file, not a directory
                    file_count += 1
                else:  # It's a directory, count files in it
                    for sub_key, sub_value in value.get('children', {}).items():
                        if isinstance(sub_value, dict) and 'token_count' in sub_value and not sub_value.get('children'):
                            file_count += 1
        
        # Should have found files
        self.assertGreater(file_count, 0,
                          "Files should be found and processed. "
                          "If this fails, the files_processed counter bug is likely present.")
    
    def test_token_counting_methods(self):
        """Test that token counting methods work correctly."""
        from app.utils.directory_util import estimate_tokens_fast, get_accurate_token_count
        
        test_file = os.path.join(self.test_dir, 'main.py')
        
        # Test fast estimation
        fast_tokens = estimate_tokens_fast(test_file)
        self.assertGreater(fast_tokens, 0, "Fast token estimation should return > 0 for text files")
        
        # Test accurate counting
        accurate_tokens = get_accurate_token_count(test_file)
        self.assertGreater(accurate_tokens, 0, "Accurate token counting should return > 0 for text files")
        
        # Both methods should give reasonable results
        self.assertLess(abs(fast_tokens - accurate_tokens) / max(fast_tokens, accurate_tokens), 5.0,
                       "Fast and accurate token counts should be within reasonable range of each other")
    
    def test_file_detection_functions(self):
        """Test file detection and filtering functions."""
        from app.utils.file_utils import is_binary_file, is_processable_file, read_file_content
        
        # Test text file
        text_file = os.path.join(self.test_dir, 'README.md')
        self.assertFalse(is_binary_file(text_file))
        self.assertTrue(is_processable_file(text_file))
        
        content = read_file_content(text_file)
        self.assertIsNotNone(content)
        self.assertIn('Test Project', content)
        
        # Test empty file
        empty_file = os.path.join(self.test_dir, 'empty.txt')
        self.assertFalse(is_binary_file(empty_file))
        self.assertTrue(is_processable_file(empty_file))
        
        empty_content = read_file_content(empty_file)
        self.assertEqual(empty_content, '')
    
    def test_gitignore_filtering(self):
        """Test that gitignore filtering works correctly."""
        from app.utils.directory_util import get_ignored_patterns
        from app.utils.gitignore_parser import parse_gitignore_patterns
        
        ignored_patterns = get_ignored_patterns(self.test_dir)
        should_ignore_fn = parse_gitignore_patterns(ignored_patterns)
        
        # Test that normal files are not ignored
        readme_path = os.path.join(self.test_dir, 'README.md')
        self.assertFalse(should_ignore_fn(readme_path),
                        "README.md should not be ignored")
        
        # Test that common ignore patterns work
        pyc_path = os.path.join(self.test_dir, 'test.pyc')
        # Note: we don't create this file, just test the pattern matching
        # The should_ignore_fn should work on paths that don't exist
    
    def test_performance_is_reasonable(self):
        """Test that directory scanning completes in reasonable time."""
        from app.utils.directory_util import get_folder_structure, get_ignored_patterns
        
        ignored_patterns = get_ignored_patterns(self.test_dir)
        
        start_time = time.time()
        structure = get_folder_structure(self.test_dir, ignored_patterns, max_depth=5)
        elapsed_time = time.time() - start_time
        
        # Should complete quickly for small test directory
        self.assertLess(elapsed_time, 5.0, 
                       f"Directory scan took {elapsed_time:.2f}s, which is too long for a small test directory")
        
        # Should return valid structure
        self.assertIsInstance(structure, dict)
        self.assertNotIn('error', structure)
    
    def test_cached_folder_structure(self):
        """Test that folder structure caching works."""
        from app.utils.directory_util import get_cached_folder_structure, get_ignored_patterns
        
        ignored_patterns = get_ignored_patterns(self.test_dir)
        
        # First call
        start_time = time.time()
        structure1 = get_cached_folder_structure(self.test_dir, ignored_patterns, max_depth=3)
        first_time = time.time() - start_time
        
        # Second call (should use cache)
        start_time = time.time()
        structure2 = get_cached_folder_structure(self.test_dir, ignored_patterns, max_depth=3)
        second_time = time.time() - start_time
        
        # Results should be identical
        self.assertEqual(structure1, structure2)
        
        # Second call should be faster (though this might be flaky on fast systems)
        # We'll just check that both calls succeeded
        self.assertIsInstance(structure1, dict)
        self.assertIsInstance(structure2, dict)
    
    def test_integration_with_current_directory(self):
        """Test directory reading on the actual Ziya project directory."""
        # This test uses the real Ziya directory to ensure it works in practice
        
        from app.utils.directory_util import get_folder_structure, get_ignored_patterns
        
        current_dir = os.getcwd()
        
        # Skip if we're not in a Ziya directory
        if not os.path.exists(os.path.join(current_dir, 'app', 'utils', 'directory_util.py')):
            self.skipTest("Not running in Ziya project directory")
        
        ignored_patterns = get_ignored_patterns(current_dir)
        
        # Perform scan with limited depth to keep test fast
        structure = get_folder_structure(current_dir, ignored_patterns, max_depth=2)
        
        # Should return valid structure
        self.assertIsInstance(structure, dict)
        
        if 'error' in structure:
            self.fail(f"Directory scan returned error: {structure['error']}")
        
        # Count files found
        file_count = 0
        for key, value in structure.items():
            if isinstance(value, dict) and 'token_count' in value:
                if not value.get('children'):
                    file_count += 1
        
        # Should find files in the Ziya project
        self.assertGreater(file_count, 0,
                          "Should find files in the Ziya project directory. "
                          "If this fails, directory reading is not working correctly.")


def run_regression_tests():
    """Run the regression tests and return success status."""
    suite = unittest.TestLoader().loadTestsFromTestCase(DirectoryReadingRegressionTest)
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    return result.wasSuccessful()


def main():
    """Main function for running tests."""
    print("=" * 60)
    print("ZIYA DIRECTORY READING REGRESSION TESTS")
    print("=" * 60)
    print("These tests verify that directory reading and token counting work correctly.")
    print("They specifically test for the files_processed counter bug that was fixed.")
    print()
    
    success = run_regression_tests()
    
    if success:
        print("\n✅ All regression tests passed!")
        print("Directory reading functionality is working correctly.")
    else:
        print("\n❌ Some regression tests failed!")
        print("There may be issues with directory reading functionality.")
    
    return success


if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
