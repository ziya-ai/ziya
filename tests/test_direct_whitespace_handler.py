"""
Tests for the direct whitespace handler.

This module tests the direct whitespace handler functionality to ensure that
it correctly identifies and applies whitespace-only changes in diffs.
"""

import unittest
import os
import tempfile
import shutil

from app.utils.diff_utils.application.direct_whitespace_handler import (
    is_whitespace_only_diff,
    apply_whitespace_only_diff
)

class TestDirectWhitespaceHandler(unittest.TestCase):
    """Test case for direct whitespace handler functionality."""
    
    def setUp(self):
        """Set up test environment."""
        # Create a temporary directory for test files
        self.test_dir = tempfile.mkdtemp()
        
        # Create a test file with specific content
        self.test_file = os.path.join(self.test_dir, "test.py")
        with open(self.test_file, "w") as f:
            f.write("def calculate_total(items):\n    \"\"\"\n    Calculate the total price of all items.\n    \n    Args:\n        items: List of items with 'price' attribute\n        \n    Returns:\n        Total price\n    \"\"\"\n    total = 0\n    for item in items:\n        total += item.price\n    \n    \n    return total\n\ndef apply_discount(total, discount_percent):\n    \"\"\"Apply percentage discount to total\"\"\"\n    if discount_percent < 0 or discount_percent > 100:\n        raise ValueError(\"Discount must be between 0 and 100\")\n    \n    discount = total * (discount_percent / 100)\n    return total - discount\n")
    
    def tearDown(self):
        """Clean up test environment."""
        # Remove the temporary directory and its contents
        shutil.rmtree(self.test_dir)
    
    def test_is_whitespace_only_diff(self):
        """Test detection of whitespace-only diffs."""
        # Whitespace-only diff
        whitespace_diff = """diff --git a/test.py b/test.py
index 1234567..abcdefg 100644
--- a/test.py
+++ b/test.py
@@ -10,8 +10,6 @@ def calculate_total(items):
     total = 0
     for item in items:
         total += item.price
-    
-    
     return total

@@ -19,5 +17,5 @@ def apply_discount(total, discount_percent):
     if discount_percent < 0 or discount_percent > 100:
         raise ValueError("Discount must be between 0 and 100")
     
-    discount = total * (discount_percent / 100)
+	discount = total * (discount_percent / 100)
     return total - discount"""
        
        self.assertTrue(is_whitespace_only_diff(whitespace_diff))
        
        # Non-whitespace diff
        non_whitespace_diff = """diff --git a/test.py b/test.py
index 1234567..abcdefg 100644
--- a/test.py
+++ b/test.py
@@ -10,8 +10,6 @@ def calculate_total(items):
     total = 0
     for item in items:
         total += item.price
-    
-    
     return total

@@ -19,5 +17,5 @@ def apply_discount(total, discount_percent):
     if discount_percent < 0 or discount_percent > 100:
         raise ValueError("Discount must be between 0 and 100")
     
-    discount = total * (discount_percent / 100)
+	discount = total * (discount_percent / 100) * 2  # Changed multiplier
     return total - discount"""
        
        self.assertFalse(is_whitespace_only_diff(non_whitespace_diff))
    
    def test_apply_whitespace_only_diff(self):
        """Test application of whitespace-only diffs."""
        # Whitespace-only diff
        whitespace_diff = """diff --git a/test.py b/test.py
index 1234567..abcdefg 100644
--- a/test.py
+++ b/test.py
@@ -10,8 +10,6 @@ def calculate_total(items):
     total = 0
     for item in items:
         total += item.price
-    
-    
     return total

@@ -19,5 +17,5 @@ def apply_discount(total, discount_percent):
     if discount_percent < 0 or discount_percent > 100:
         raise ValueError("Discount must be between 0 and 100")
     
-    discount = total * (discount_percent / 100)
+	discount = total * (discount_percent / 100)
     return total - discount"""
        
        # Apply the diff
        result = apply_whitespace_only_diff(self.test_file, whitespace_diff)
        self.assertTrue(result)
        
        # Read the modified file
        with open(self.test_file, "r") as f:
            modified_content = f.read()
        
        # Check that the changes were applied
        expected_content = "def calculate_total(items):\n    \"\"\"\n    Calculate the total price of all items.\n    \n    Args:\n        items: List of items with 'price' attribute\n        \n    Returns:\n        Total price\n    \"\"\"\n    total = 0\n    for item in items:\n        total += item.price\n    return total\n\ndef apply_discount(total, discount_percent):\n    \"\"\"Apply percentage discount to total\"\"\"\n    if discount_percent < 0 or discount_percent > 100:\n        raise ValueError(\"Discount must be between 0 and 100\")\n    \n\tdiscount = total * (discount_percent / 100)\n    return total - discount\n"
        self.maxDiff = None  # Show full diff
        self.assertEqual(modified_content, expected_content)
    
    def test_apply_non_whitespace_diff(self):
        """Test that non-whitespace diffs are not applied."""
        # Non-whitespace diff
        non_whitespace_diff = """diff --git a/test.py b/test.py
index 1234567..abcdefg 100644
--- a/test.py
+++ b/test.py
@@ -10,8 +10,6 @@ def calculate_total(items):
     total = 0
     for item in items:
         total += item.price
-    
-    
     return total

@@ -19,5 +17,5 @@ def apply_discount(total, discount_percent):
     if discount_percent < 0 or discount_percent > 100:
         raise ValueError("Discount must be between 0 and 100")
     
-    discount = total * (discount_percent / 100)
+	discount = total * (discount_percent / 100) * 2  # Changed multiplier
     return total - discount"""
        
        # Try to apply the diff
        result = apply_whitespace_only_diff(self.test_file, non_whitespace_diff)
        self.assertFalse(result)
        
        # Read the file to verify it wasn't modified
        with open(self.test_file, "r") as f:
            content = f.read()
        
        # Check that the file wasn't modified
        original_content = "def calculate_total(items):\n    \"\"\"\n    Calculate the total price of all items.\n    \n    Args:\n        items: List of items with 'price' attribute\n        \n    Returns:\n        Total price\n    \"\"\"\n    total = 0\n    for item in items:\n        total += item.price\n    \n    \n    return total\n\ndef apply_discount(total, discount_percent):\n    \"\"\"Apply percentage discount to total\"\"\"\n    if discount_percent < 0 or discount_percent > 100:\n        raise ValueError(\"Discount must be between 0 and 100\")\n    \n    discount = total * (discount_percent / 100)\n    return total - discount\n"
        self.assertEqual(content, original_content)

if __name__ == "__main__":
    unittest.main()
