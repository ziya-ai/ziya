#!/usr/bin/env python3
"""
API endpoints integration test for Ziya.

Tests the integration between directory utilities and server API endpoints
to ensure they work correctly together.
"""

import os
import sys
import tempfile
import shutil
import unittest
from unittest.mock import patch, MagicMock
from typing import Dict, Any

# Add the app directory to the path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'app'))


class TestAPIEndpoints(unittest.TestCase):
    """Test API endpoint integration with directory utilities."""
    
    @classmethod
    def setUpClass(cls):
        """Set up test environment."""
        cls.test_dir = tempfile.mkdtemp(prefix='ziya_api_test_')
        
        # Create test files
        test_files = {
            'README.md': '# Test Project\n\nThis is a test project.\n',
            'main.py': 'def main():\n    print("Hello")\n',
            'config.json': '{"test": true}\n',
            'subdir/file.txt': 'Nested file content\n',
        }
        
        for file_path, content in test_files.items():
            full_path = os.path.join(cls.test_dir, file_path)
            os.makedirs(os.path.dirname(full_path), exist_ok=True)
            with open(full_path, 'w') as f:
                f.write(content)
    
    @classmethod
    def tearDownClass(cls):
        """Clean up test environment."""
        shutil.rmtree(cls.test_dir, ignore_errors=True)
    
    def test_folder_endpoint_integration(self):
        """Test integration with /folder endpoint logic."""
        from app.utils.directory_util import get_cached_folder_structure, get_ignored_patterns
        
        # Simulate the logic from the /folder endpoint
        ignored_patterns = get_ignored_patterns(self.test_dir)
        max_depth = 15  # Default from server
        
        result = get_cached_folder_structure(self.test_dir, ignored_patterns, max_depth)
        
        # Should return structure compatible with API response
        self.assertIsInstance(result, dict)
        
        if 'error' not in result:
            # Should be JSON serializable (important for API)
            import json
            try:
                json_str = json.dumps(result)
                self.assertIsInstance(json_str, str)
            except (TypeError, ValueError) as e:
                self.fail(f"Result should be JSON serializable: {e}")
            
            # Should contain expected files
            self.assertIn('README.md', result)
            self.assertIn('main.py', result)
            self.assertIn('config.json', result)
            
            # Files should have token counts
            for filename in ['README.md', 'main.py', 'config.json']:
                if filename in result:
                    file_entry = result[filename]
                    self.assertIsInstance(file_entry, dict)
                    self.assertIn('token_count', file_entry)
                    self.assertGreater(file_entry['token_count'], 0)
    
    def test_api_folders_endpoint_integration(self):
        """Test integration with /api/folders endpoint logic."""
        from app.utils.directory_util import get_cached_folder_structure, get_ignored_patterns
        
        # Mock environment variable for user codebase directory
        with patch.dict(os.environ, {'ZIYA_USER_CODEBASE_DIR': self.test_dir}):
            # Simulate the logic from /api/folders endpoint
            user_codebase_dir = os.environ.get("ZIYA_USER_CODEBASE_DIR", self.test_dir)
            
            ignored_patterns = get_ignored_patterns(user_codebase_dir)
            max_depth = int(os.environ.get("ZIYA_MAX_DEPTH", 15))
            
            result = get_cached_folder_structure(user_codebase_dir, ignored_patterns, max_depth)
            
            # Should return valid structure
            self.assertIsInstance(result, dict)
            
            if 'error' not in result:
                # Should find files
                file_count = 0
                for key, value in result.items():
                    if isinstance(value, dict) and 'token_count' in value:
                        if not value.get('children'):  # It's a file
                            file_count += 1
                
                self.assertGreater(file_count, 0, "Should find files in the test directory")
    
    def test_error_handling_in_api_context(self):
        """Test error handling when used in API context."""
        from app.utils.directory_util import get_cached_folder_structure, get_ignored_patterns
        
        # Test with non-existent directory
        fake_dir = '/path/that/does/not/exist'
        ignored_patterns = []
        
        result = get_cached_folder_structure(fake_dir, ignored_patterns, max_depth=5)
        
        # Should handle gracefully and return error structure
        self.assertIsInstance(result, dict)
        
        # Should be JSON serializable even with errors
        import json
        try:
            json.dumps(result)
        except (TypeError, ValueError) as e:
            self.fail(f"Error result should be JSON serializable: {e}")
    
    def test_environment_variable_integration(self):
        """Test integration with environment variables used by API."""
        from app.utils.directory_util import get_cached_folder_structure, get_ignored_patterns
        
        # Test with various environment variable configurations
        test_configs = [
            {'ZIYA_MAX_DEPTH': '5'},
            {'ZIYA_SCAN_TIMEOUT': '10'},
            {'ZIYA_ADDITIONAL_EXCLUDE_DIRS': '*.tmp,temp'},
        ]
        
        for config in test_configs:
            with patch.dict(os.environ, config):
                ignored_patterns = get_ignored_patterns(self.test_dir)
                max_depth = int(os.environ.get("ZIYA_MAX_DEPTH", 15))
                
                result = get_cached_folder_structure(self.test_dir, ignored_patterns, max_depth)
                
                # Should work with different configurations
                self.assertIsInstance(result, dict)
                
                if 'error' not in result:
                    # Should still find files
                    self.assertTrue(len(result) > 0, f"Should find files with config {config}")
    
    def test_progress_tracking_integration(self):
        """Test integration with progress tracking used by API."""
        from app.utils.directory_util import get_folder_structure, get_ignored_patterns, get_scan_progress
        
        ignored_patterns = get_ignored_patterns(self.test_dir)
        
        # Start a scan (this should update progress)
        structure = get_folder_structure(self.test_dir, ignored_patterns, max_depth=5)
        
        # Check that progress tracking worked
        progress = get_scan_progress()
        self.assertIsInstance(progress, dict)
        
        # Progress should be JSON serializable for API
        import json
        try:
            json.dumps(progress)
        except (TypeError, ValueError) as e:
            self.fail(f"Progress should be JSON serializable: {e}")
    
    def test_caching_behavior_in_api_context(self):
        """Test caching behavior as used by API endpoints."""
        from app.utils.directory_util import get_cached_folder_structure, get_ignored_patterns
        
        ignored_patterns = get_ignored_patterns(self.test_dir)
        
        # Multiple calls should use caching
        result1 = get_cached_folder_structure(self.test_dir, ignored_patterns, max_depth=5)
        result2 = get_cached_folder_structure(self.test_dir, ignored_patterns, max_depth=5)
        
        # Results should be identical (cached)
        self.assertEqual(result1, result2, "Cached results should be identical")
        
        # Both should be valid API responses
        for result in [result1, result2]:
            self.assertIsInstance(result, dict)
            
            # Should be JSON serializable
            import json
            try:
                json.dumps(result)
            except (TypeError, ValueError) as e:
                self.fail(f"Cached result should be JSON serializable: {e}")
    
    def test_response_format_compatibility(self):
        """Test that response format is compatible with frontend expectations."""
        from app.utils.directory_util import get_cached_folder_structure, get_ignored_patterns
        
        ignored_patterns = get_ignored_patterns(self.test_dir)
        result = get_cached_folder_structure(self.test_dir, ignored_patterns, max_depth=5)
        
        if 'error' not in result:
            # Check structure format expected by frontend
            for key, value in result.items():
                if isinstance(value, dict):
                    # Files should have token_count
                    if 'token_count' in value and not value.get('children'):
                        self.assertIsInstance(value['token_count'], int)
                        self.assertGreaterEqual(value['token_count'], 0)
                    
                    # Directories should have children and token_count
                    elif 'children' in value:
                        self.assertIsInstance(value['children'], dict)
                        self.assertIn('token_count', value)
                        self.assertIsInstance(value['token_count'], int)
                        self.assertGreaterEqual(value['token_count'], 0)
    
    def test_large_response_handling(self):
        """Test handling of large responses that might be returned by API."""
        from app.utils.directory_util import get_cached_folder_structure, get_ignored_patterns
        
        # Create a larger directory structure
        large_dir = os.path.join(self.test_dir, 'large_structure')
        os.makedirs(large_dir, exist_ok=True)
        
        # Create many files
        for i in range(50):
            file_path = os.path.join(large_dir, f'file_{i:02d}.txt')
            with open(file_path, 'w') as f:
                f.write(f'Content for file {i}\n' * 10)
        
        ignored_patterns = get_ignored_patterns(self.test_dir)
        result = get_cached_folder_structure(self.test_dir, ignored_patterns, max_depth=5)
        
        # Should handle large responses
        self.assertIsInstance(result, dict)
        
        # Should be JSON serializable even when large
        import json
        try:
            json_str = json.dumps(result)
            # Should produce a reasonable JSON string
            self.assertIsInstance(json_str, str)
            self.assertGreater(len(json_str), 100)  # Should have substantial content
        except (TypeError, ValueError) as e:
            self.fail(f"Large result should be JSON serializable: {e}")


if __name__ == "__main__":
    unittest.main()
