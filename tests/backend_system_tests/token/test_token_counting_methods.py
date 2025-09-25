#!/usr/bin/env python3
"""
Token counting methods regression test for Ziya.

Tests the various token counting methods to ensure they work correctly
and consistently across different file types and content.
"""

import os
import sys
import tempfile
import shutil
import unittest
from typing import Dict, List

# Add the app directory to the path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'app'))


class TestTokenCountingMethods(unittest.TestCase):
    """Test token counting methods and consistency."""
    
    @classmethod
    def setUpClass(cls):
        """Set up test files with known content."""
        cls.test_dir = tempfile.mkdtemp(prefix='ziya_token_test_')
        
        # Create test files with different characteristics
        cls.test_files = {
            'simple.txt': 'Hello world! This is a simple test file with basic content.',
            'code.py': '''
def hello_world():
    """A simple hello world function."""
    print("Hello, World!")
    return "success"

if __name__ == "__main__":
    result = hello_world()
    print(f"Result: {result}")
''',
            'markdown.md': '''
# Test Document

This is a **test document** with various formatting:

- Item 1
- Item 2  
- Item 3

## Code Example

```python
print("Hello from markdown!")
```

The end.
''',
            'json.json': '''
{
    "name": "test",
    "version": "1.0.0",
    "description": "A test JSON file",
    "keywords": ["test", "json", "example"],
    "nested": {
        "value": 42,
        "array": [1, 2, 3, 4, 5]
    }
}
''',
            'empty.txt': '',
            'large.txt': 'This is a line of text that will be repeated many times.\n' * 500,
        }
        
        for filename, content in cls.test_files.items():
            with open(os.path.join(cls.test_dir, filename), 'w') as f:
                f.write(content)
    
    @classmethod
    def tearDownClass(cls):
        """Clean up test files."""
        shutil.rmtree(cls.test_dir, ignore_errors=True)
    
    def test_fast_token_estimation(self):
        """Test fast token estimation method."""
        from app.utils.directory_util import estimate_tokens_fast
        
        for filename in self.test_files.keys():
            filepath = os.path.join(self.test_dir, filename)
            
            tokens = estimate_tokens_fast(filepath)
            
            if filename == 'empty.txt':
                self.assertEqual(tokens, 0, "Empty file should have 0 tokens")
            else:
                self.assertGreater(tokens, 0, f"Non-empty file {filename} should have > 0 tokens")
    
    def test_accurate_token_counting(self):
        """Test accurate token counting with tiktoken."""
        from app.utils.directory_util import get_accurate_token_count
        
        for filename in self.test_files.keys():
            filepath = os.path.join(self.test_dir, filename)
            
            tokens = get_accurate_token_count(filepath)
            
            if filename == 'empty.txt':
                self.assertEqual(tokens, 0, "Empty file should have 0 tokens")
            else:
                self.assertGreater(tokens, 0, f"Non-empty file {filename} should have > 0 tokens")
    
    def test_token_counting_consistency(self):
        """Test that fast and accurate methods are reasonably consistent."""
        from app.utils.directory_util import estimate_tokens_fast, get_accurate_token_count
        
        for filename in self.test_files.keys():
            if filename == 'empty.txt':
                continue  # Skip empty file
                
            filepath = os.path.join(self.test_dir, filename)
            
            fast_count = estimate_tokens_fast(filepath)
            accurate_count = get_accurate_token_count(filepath)
            
            # Both should be positive
            self.assertGreater(fast_count, 0, f"Fast count should be > 0 for {filename}")
            self.assertGreater(accurate_count, 0, f"Accurate count should be > 0 for {filename}")
            
            # They should be within reasonable range of each other
            ratio = fast_count / accurate_count if accurate_count > 0 else 0
            self.assertGreater(ratio, 0.1, f"Fast/accurate ratio too low for {filename}: {ratio}")
            self.assertLess(ratio, 10.0, f"Fast/accurate ratio too high for {filename}: {ratio}")
    
    def test_file_type_multipliers(self):
        """Test file type multipliers for different extensions."""
        from app.utils.directory_util import get_file_type_multiplier
        
        test_cases = [
            ('test.py', 1.8),      # Python file
            ('test.js', 1.8),      # JavaScript file
            ('test.md', 1.3),      # Markdown file
            ('test.json', 1.3),    # JSON file
            ('test.txt', 1.2),     # Text file
            ('test.unknown', 1.5), # Unknown extension
            ('test', 1.5),         # No extension
        ]
        
        for filename, expected_multiplier in test_cases:
            multiplier = get_file_type_multiplier(filename)
            self.assertEqual(multiplier, expected_multiplier,
                           f"File type multiplier for {filename} should be {expected_multiplier}")
    
    def test_token_estimation_with_multipliers(self):
        """Test that file type multipliers are applied correctly."""
        from app.utils.directory_util import estimate_tokens_fast, get_file_type_multiplier
        
        # Test with Python file (should have 1.8x multiplier)
        py_file = os.path.join(self.test_dir, 'code.py')
        py_tokens = estimate_tokens_fast(py_file)
        py_multiplier = get_file_type_multiplier(py_file)
        
        # Test with text file (should have 1.2x multiplier)  
        txt_file = os.path.join(self.test_dir, 'simple.txt')
        txt_tokens = estimate_tokens_fast(txt_file)
        txt_multiplier = get_file_type_multiplier(txt_file)
        
        # Python file should have higher multiplier
        self.assertGreater(py_multiplier, txt_multiplier,
                          "Python files should have higher multiplier than text files")
        
        # Both should have positive token counts
        self.assertGreater(py_tokens, 0, "Python file should have positive token count")
        self.assertGreater(txt_tokens, 0, "Text file should have positive token count")
    
    def test_large_file_handling(self):
        """Test token counting with large files."""
        from app.utils.directory_util import estimate_tokens_fast, get_accurate_token_count
        
        large_file = os.path.join(self.test_dir, 'large.txt')
        
        # Fast estimation should work quickly
        fast_tokens = estimate_tokens_fast(large_file)
        self.assertGreater(fast_tokens, 0, "Large file should have positive token count")
        
        # Accurate counting should also work (though slower)
        accurate_tokens = get_accurate_token_count(large_file)
        self.assertGreater(accurate_tokens, 0, "Large file should have positive accurate token count")
        
        # Should be reasonably consistent
        ratio = fast_tokens / accurate_tokens if accurate_tokens > 0 else 0
        self.assertGreater(ratio, 0.1, "Fast/accurate ratio should be reasonable for large file")
        self.assertLess(ratio, 10.0, "Fast/accurate ratio should be reasonable for large file")
    
    def test_binary_file_handling(self):
        """Test that binary files return 0 tokens."""
        from app.utils.directory_util import estimate_tokens_fast, get_accurate_token_count
        
        # Create a binary file
        binary_file = os.path.join(self.test_dir, 'binary.pyc')
        with open(binary_file, 'wb') as f:
            f.write(b'\x00\x01\x02\x03\x04\x05\x06\x07')
        
        # Both methods should return 0 for binary files
        fast_tokens = estimate_tokens_fast(binary_file)
        accurate_tokens = get_accurate_token_count(binary_file)
        
        self.assertEqual(fast_tokens, 0, "Binary file should have 0 tokens (fast)")
        self.assertEqual(accurate_tokens, 0, "Binary file should have 0 tokens (accurate)")
    
    def test_nonexistent_file_handling(self):
        """Test handling of non-existent files."""
        from app.utils.directory_util import estimate_tokens_fast, get_accurate_token_count
        
        nonexistent_file = os.path.join(self.test_dir, 'does_not_exist.txt')
        
        # Should handle gracefully without crashing
        fast_tokens = estimate_tokens_fast(nonexistent_file)
        accurate_tokens = get_accurate_token_count(nonexistent_file)
        
        # Should return 0 for non-existent files
        self.assertEqual(fast_tokens, 0, "Non-existent file should have 0 tokens (fast)")
        self.assertEqual(accurate_tokens, 0, "Non-existent file should have 0 tokens (accurate)")
    
    def test_tiktoken_integration(self):
        """Test tiktoken integration for accurate counting."""
        try:
            import tiktoken
            from app.utils.directory_util import get_accurate_token_count
            
            # Test that tiktoken is working
            encoding = tiktoken.get_encoding("cl100k_base")
            test_text = "Hello, world! This is a test."
            tokens = encoding.encode(test_text)
            self.assertGreater(len(tokens), 0, "tiktoken should encode text to tokens")
            
            # Test with our function
            test_file = os.path.join(self.test_dir, 'simple.txt')
            token_count = get_accurate_token_count(test_file)
            self.assertGreater(token_count, 0, "Accurate token count should be positive")
            
        except ImportError:
            self.skipTest("tiktoken not available")


if __name__ == "__main__":
    unittest.main()
