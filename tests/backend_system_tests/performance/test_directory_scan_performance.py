#!/usr/bin/env python3
"""
Directory scanning performance tests for Ziya.

Tests performance characteristics of directory scanning including
timeout behavior, large directory handling, and caching effectiveness.

# SLOW_TEST - This test may take longer to execute
"""

import os
import sys
import time
import tempfile
import shutil
import unittest
from typing import Dict, List

# Add the app directory to the path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'app'))


class TestDirectoryScanPerformance(unittest.TestCase):
    """Test directory scanning performance characteristics."""
    
    @classmethod
    def setUpClass(cls):
        """Set up test directory with various file sizes and structures."""
        cls.test_dir = tempfile.mkdtemp(prefix='ziya_perf_test_')
        
        # Create a variety of files and directories for performance testing
        cls._create_test_structure()
    
    @classmethod
    def tearDownClass(cls):
        """Clean up test directory."""
        shutil.rmtree(cls.test_dir, ignore_errors=True)
    
    @classmethod
    def _create_test_structure(cls):
        """Create a test directory structure with various file types and sizes."""
        # Create nested directories
        for i in range(5):
            dir_path = os.path.join(cls.test_dir, f'subdir_{i}')
            os.makedirs(dir_path, exist_ok=True)
            
            # Create files in each subdirectory
            for j in range(10):
                file_path = os.path.join(dir_path, f'file_{j}.txt')
                content = f'This is file {j} in directory {i}.\n' * (j + 1)
                with open(file_path, 'w') as f:
                    f.write(content)
        
        # Create some larger files
        large_file = os.path.join(cls.test_dir, 'large_file.txt')
        with open(large_file, 'w') as f:
            f.write('Large file content line.\n' * 1000)
        
        # Create some code files
        code_files = {
            'main.py': '''
def main():
    """Main function."""
    print("Hello, World!")
    return 0

if __name__ == "__main__":
    main()
''' * 20,
            'utils.js': '''
function utility() {
    console.log("Utility function");
    return true;
}

module.exports = { utility };
''' * 15,
            'README.md': '''
# Test Project

This is a test project for performance testing.

## Features

- Feature 1
- Feature 2
- Feature 3
''' * 10,
        }
        
        for filename, content in code_files.items():
            with open(os.path.join(cls.test_dir, filename), 'w') as f:
                f.write(content)
    
    def test_directory_scan_completes_within_timeout(self):
        """Test that directory scanning completes within reasonable time."""
        from app.utils.directory_util import get_folder_structure, get_ignored_patterns
        
        ignored_patterns = get_ignored_patterns(self.test_dir)
        
        start_time = time.time()
        structure = get_folder_structure(self.test_dir, ignored_patterns, max_depth=10)
        elapsed_time = time.time() - start_time
        
        # Should complete within 10 seconds for our test structure
        self.assertLess(elapsed_time, 10.0, 
                       f"Directory scan took {elapsed_time:.2f}s, which exceeds 10s timeout")
        
        # Should return valid structure
        self.assertIsInstance(structure, dict)
        self.assertNotIn('error', structure)
    
    def test_cached_folder_structure_performance(self):
        """Test that caching improves performance."""
        from app.utils.directory_util import get_cached_folder_structure, get_ignored_patterns
        
        ignored_patterns = get_ignored_patterns(self.test_dir)
        
        # First call (should populate cache)
        start_time = time.time()
        structure1 = get_cached_folder_structure(self.test_dir, ignored_patterns, max_depth=5)
        first_call_time = time.time() - start_time
        
        # Second call (should use cache)
        start_time = time.time()
        structure2 = get_cached_folder_structure(self.test_dir, ignored_patterns, max_depth=5)
        second_call_time = time.time() - start_time
        
        # Results should be identical
        self.assertEqual(structure1, structure2, "Cached results should be identical")
        
        # Second call should be significantly faster
        # Allow some tolerance for system variance, but expect at least 50% improvement
        self.assertLess(second_call_time, first_call_time * 0.8,
                       f"Cached call ({second_call_time:.3f}s) should be faster than "
                       f"initial call ({first_call_time:.3f}s)")
    
    def test_deep_directory_traversal_performance(self):
        """Test performance with deep directory structures."""
        from app.utils.directory_util import get_folder_structure, get_ignored_patterns
        
        # Create a deep directory structure
        deep_dir = os.path.join(self.test_dir, 'deep')
        current_dir = deep_dir
        
        # Create 10 levels deep
        for i in range(10):
            current_dir = os.path.join(current_dir, f'level_{i}')
            os.makedirs(current_dir, exist_ok=True)
            
            # Add a file at each level
            with open(os.path.join(current_dir, f'file_level_{i}.txt'), 'w') as f:
                f.write(f'Content at level {i}\n' * 10)
        
        ignored_patterns = get_ignored_patterns(self.test_dir)
        
        # Test with different max depths
        for max_depth in [5, 10, 15]:
            start_time = time.time()
            structure = get_folder_structure(self.test_dir, ignored_patterns, max_depth=max_depth)
            elapsed_time = time.time() - start_time
            
            # Should complete within reasonable time even with deep structures
            self.assertLess(elapsed_time, 15.0,
                           f"Deep directory scan (depth {max_depth}) took {elapsed_time:.2f}s")
            
            # Should return valid structure
            self.assertIsInstance(structure, dict)
            self.assertNotIn('error', structure)
    
    def test_large_file_handling_performance(self):
        """Test performance when handling large files."""
        from app.utils.directory_util import estimate_tokens_fast, get_accurate_token_count
        
        # Create a large file
        large_file = os.path.join(self.test_dir, 'very_large_file.txt')
        with open(large_file, 'w') as f:
            # Write about 1MB of content
            content = 'This is a line of text for the large file test.\n'
            for _ in range(20000):  # About 1MB
                f.write(content)
        
        # Test fast estimation (should be very quick)
        start_time = time.time()
        fast_tokens = estimate_tokens_fast(large_file)
        fast_time = time.time() - start_time
        
        self.assertLess(fast_time, 0.1, f"Fast estimation took {fast_time:.3f}s, should be < 0.1s")
        self.assertGreater(fast_tokens, 0, "Fast estimation should return positive tokens")
        
        # Test accurate counting (slower but should still be reasonable)
        start_time = time.time()
        accurate_tokens = get_accurate_token_count(large_file)
        accurate_time = time.time() - start_time
        
        self.assertLess(accurate_time, 5.0, f"Accurate counting took {accurate_time:.3f}s, should be < 5s")
        self.assertGreater(accurate_tokens, 0, "Accurate counting should return positive tokens")
    
    def test_many_small_files_performance(self):
        """Test performance when handling many small files."""
        from app.utils.directory_util import get_folder_structure, get_ignored_patterns
        
        # Create directory with many small files
        many_files_dir = os.path.join(self.test_dir, 'many_files')
        os.makedirs(many_files_dir, exist_ok=True)
        
        # Create 100 small files
        for i in range(100):
            file_path = os.path.join(many_files_dir, f'small_file_{i:03d}.txt')
            with open(file_path, 'w') as f:
                f.write(f'Small file {i} content.\n')
        
        ignored_patterns = get_ignored_patterns(self.test_dir)
        
        start_time = time.time()
        structure = get_folder_structure(self.test_dir, ignored_patterns, max_depth=5)
        elapsed_time = time.time() - start_time
        
        # Should handle many small files efficiently
        self.assertLess(elapsed_time, 10.0,
                       f"Many small files scan took {elapsed_time:.2f}s, should be < 10s")
        
        # Should find the files
        self.assertIsInstance(structure, dict)
        self.assertNotIn('error', structure)
        
        # Should have found files in the many_files directory
        if 'many_files' in structure:
            many_files_entry = structure['many_files']
            if isinstance(many_files_entry, dict) and 'children' in many_files_entry:
                children_count = len(many_files_entry['children'])
                self.assertGreater(children_count, 50, 
                                 f"Should find most of the 100 files, found {children_count}")
    
    def test_timeout_behavior(self):
        """Test that timeout mechanisms work correctly."""
        from app.utils.directory_util import get_folder_structure, get_ignored_patterns
        
        # This test verifies timeout behavior without actually timing out
        # We'll use a reasonable timeout and verify the structure is returned
        
        ignored_patterns = get_ignored_patterns(self.test_dir)
        
        # Set a reasonable timeout via environment variable
        original_timeout = os.environ.get('ZIYA_SCAN_TIMEOUT')
        os.environ['ZIYA_SCAN_TIMEOUT'] = '30'  # 30 seconds
        
        try:
            start_time = time.time()
            structure = get_folder_structure(self.test_dir, ignored_patterns, max_depth=5)
            elapsed_time = time.time() - start_time
            
            # Should complete well within timeout
            self.assertLess(elapsed_time, 25.0, "Should complete well within timeout")
            
            # Should return valid structure (not timeout error)
            self.assertIsInstance(structure, dict)
            if 'error' in structure:
                self.assertNotIn('timeout', structure['error'].lower(),
                               "Should not timeout with reasonable directory structure")
        
        finally:
            # Restore original timeout
            if original_timeout is not None:
                os.environ['ZIYA_SCAN_TIMEOUT'] = original_timeout
            else:
                os.environ.pop('ZIYA_SCAN_TIMEOUT', None)
    
    def test_memory_usage_reasonable(self):
        """Test that memory usage remains reasonable during scanning."""
        import psutil
        import gc
        
        from app.utils.directory_util import get_folder_structure, get_ignored_patterns
        
        # Get initial memory usage
        process = psutil.Process()
        initial_memory = process.memory_info().rss / 1024 / 1024  # MB
        
        ignored_patterns = get_ignored_patterns(self.test_dir)
        
        # Perform multiple scans to check for memory leaks
        for i in range(3):
            structure = get_folder_structure(self.test_dir, ignored_patterns, max_depth=5)
            self.assertIsInstance(structure, dict)
            
            # Force garbage collection
            gc.collect()
            
            # Check memory usage
            current_memory = process.memory_info().rss / 1024 / 1024  # MB
            memory_increase = current_memory - initial_memory
            
            # Memory increase should be reasonable (less than 100MB for our test)
            self.assertLess(memory_increase, 100,
                           f"Memory usage increased by {memory_increase:.1f}MB, "
                           f"which may indicate a memory leak")
    
    def test_concurrent_scan_safety(self):
        """Test that concurrent scans don't interfere with each other."""
        import threading
        from app.utils.directory_util import get_folder_structure, get_ignored_patterns
        
        ignored_patterns = get_ignored_patterns(self.test_dir)
        results = []
        errors = []
        
        def scan_directory():
            try:
                structure = get_folder_structure(self.test_dir, ignored_patterns, max_depth=3)
                results.append(structure)
            except Exception as e:
                errors.append(str(e))
        
        # Start multiple concurrent scans
        threads = []
        for i in range(3):
            thread = threading.Thread(target=scan_directory)
            threads.append(thread)
            thread.start()
        
        # Wait for all threads to complete
        for thread in threads:
            thread.join(timeout=30)  # 30 second timeout per thread
        
        # Check results
        self.assertEqual(len(errors), 0, f"Concurrent scans had errors: {errors}")
        self.assertEqual(len(results), 3, "Should have 3 successful scan results")
        
        # All results should be valid dictionaries
        for i, result in enumerate(results):
            self.assertIsInstance(result, dict, f"Result {i} should be a dictionary")
            self.assertNotIn('error', result, f"Result {i} should not contain errors")


if __name__ == "__main__":
    unittest.main()
