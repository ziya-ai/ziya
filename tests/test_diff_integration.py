"""
Integration tests for the diff application system.

Moved from app/utils/diff_utils/tests/test_integration.py to be visible
to pytest (testpaths = tests).
"""

import os
import unittest
import tempfile
import shutil

from app.utils.diff_utils.application.patch_apply import apply_diff_with_difflib
from app.utils.diff_utils.core.exceptions import PatchApplicationError


class TestDiffApplicationIntegration(unittest.TestCase):
    """Integration tests for the diff application system."""
    
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        
        self.python_file = os.path.join(self.temp_dir, "test.py")
        self.js_file = os.path.join(self.temp_dir, "test.js")
        self.text_file = os.path.join(self.temp_dir, "test.txt")
        
        with open(self.python_file, "w") as f:
            f.write('''def hello():
    """Say hello."""
    print("Hello, world!")

class TestClass:
    def __init__(self):
        self.value = 42
    
    def get_value(self):
        return self.value
''')
        
        with open(self.js_file, "w") as f:
            f.write('''function hello() {
    console.log("Hello, world!");
}

class TestClass {
    constructor() {
        this.value = 42;
    }
    
    getValue() {
        return this.value;
    }
}
''')
        
        with open(self.text_file, "w") as f:
            f.write('''This is a test file.
It has multiple lines.
Some lines might be repeated.
''')
    
    def tearDown(self):
        shutil.rmtree(self.temp_dir)
    
    def test_apply_valid_python_diff(self):
        diff = '''diff --git a/test.py b/test.py
--- a/test.py
+++ b/test.py
@@ -1,5 +1,6 @@
 def hello():
     """Say hello."""
+    print("Starting...")
     print("Hello, world!")
 
 class TestClass:
'''
        result = apply_diff_with_difflib(self.python_file, diff)
        
        with open(self.python_file, "r") as f:
            content = f.read()
        self.assertIn('print("Starting...")', content)
        self.assertIn('print("Hello, world!")', content)
    
    def test_apply_invalid_python_diff(self):
        diff = '''diff --git a/test.py b/test.py
--- a/test.py
+++ b/test.py
@@ -1,5 +1,6 @@
 def hello():
     """Say hello."""
-    print("Hello, world!")
+    print("Hello, world!"
 
 class TestClass:
'''
        with self.assertRaises(PatchApplicationError):
            apply_diff_with_difflib(self.python_file, diff)
    
    def test_apply_duplicate_python_diff(self):
        diff = '''diff --git a/test.py b/test.py
--- a/test.py
+++ b/test.py
@@ -9,3 +9,7 @@ class TestClass:
     def get_value(self):
         return self.value
 
+def hello():
+    """Say hello again."""
+    print("Hello again!")
+
'''
        with self.assertRaises(PatchApplicationError):
            apply_diff_with_difflib(self.python_file, diff)
    
    def test_apply_js_diff(self):
        diff = '''diff --git a/test.js b/test.js
--- a/test.js
+++ b/test.js
@@ -1,5 +1,6 @@
 function hello() {
     console.log("Hello, world!");
+    console.log("Done!");
 }
 
 class TestClass {
'''
        result = apply_diff_with_difflib(self.js_file, diff)
        
        with open(self.js_file, "r") as f:
            content = f.read()
        self.assertIn('console.log("Done!");', content)
    
    def test_apply_text_diff(self):
        diff = '''diff --git a/test.txt b/test.txt
--- a/test.txt
+++ b/test.txt
@@ -1,3 +1,4 @@
 This is a test file.
 It has multiple lines.
 Some lines might be repeated.
+This is a new line.
'''
        result = apply_diff_with_difflib(self.text_file, diff)
        
        with open(self.text_file, "r") as f:
            content = f.read()
        self.assertIn("This is a new line.", content)


if __name__ == "__main__":
    unittest.main()
