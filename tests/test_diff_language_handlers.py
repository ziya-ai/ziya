"""
Tests for language handlers.

Moved from app/utils/diff_utils/tests/test_language_handlers.py to be visible
to pytest (testpaths = tests).  Relative imports converted to absolute.
"""

import os
import unittest
import tempfile
import shutil

from app.utils.diff_utils.language_handlers import (
    LanguageHandler,
    LanguageHandlerRegistry,
    GenericTextHandler
)
from app.utils.diff_utils.language_handlers.python import PythonHandler


class TestLanguageHandlers(unittest.TestCase):
    """Test cases for language handlers."""
    
    def setUp(self):
        """Set up test environment."""
        self.temp_dir = tempfile.mkdtemp()
        
        self.python_file = os.path.join(self.temp_dir, "test.py")
        self.js_file = os.path.join(self.temp_dir, "test.js")
        self.text_file = os.path.join(self.temp_dir, "test.txt")
        
        with open(self.python_file, "w") as f:
            f.write('''def test_function():
    """Test function."""
    return True

class TestClass:
    def method(self):
        return True
''')
        
        with open(self.js_file, "w") as f:
            f.write('''function testFunction() {
    return true;
}

class TestClass {
    method() {
        return true;
    }
}
''')
        
        with open(self.text_file, "w") as f:
            f.write('''This is a test file.
It has multiple lines.
Some lines might be repeated.
Some lines might be repeated.
''')
    
    def tearDown(self):
        shutil.rmtree(self.temp_dir)
    
    def test_handler_registry(self):
        """Test that the handler registry returns the correct handler."""
        handler = LanguageHandlerRegistry.get_handler(self.python_file)
        self.assertEqual(handler, PythonHandler)
        
        handler = LanguageHandlerRegistry.get_handler(self.text_file)
        self.assertEqual(handler, GenericTextHandler)
    
    def test_unterminated_triple_quote_attribution(self):
        """An unterminated triple-quoted string (e.g. a docstring bisected by a
        bad diff) must be attributed to the line that opens the dangling '\"\"\"',
        NOT to an innocent earlier line such as 'class C:' that the prefix-bisect
        heuristic would otherwise blame."""
        import ast
        broken = (
            "class C:\n"
            "    def process(self, response):\n"
            '        """\n'
            "        Process the response.\n"
            "        # Reset counters\n"
            "        self.applied = 0\n"
            "\n"
            "        # Extract all diffs\n"
            "        return response\n"
        )
        try:
            ast.parse(broken)
            self.fail("expected SyntaxError for bisected docstring")
        except SyntaxError as e:
            line, msg = PythonHandler._refine_syntax_error(broken, e)
            self.assertEqual(line, 3, "should point at the opening triple-quote line")
            self.assertNotIn("class C", msg, "must not blame the innocent class line")
            self.assertIn("triple-quote", msg.lower())

    def test_genuine_chase_forward_still_resolved(self):
        """A real unterminated string deep in an otherwise-valid file (parser
        detects at EOF) should still resolve to the actual opening delimiter."""
        import ast
        body = "x = 1\n" * 50 + 's = """unterminated here\n' + "more = 2\n" * 50
        try:
            ast.parse(body)
            self.fail("expected SyntaxError")
        except SyntaxError as e:
            line, _ = PythonHandler._refine_syntax_error(body, e)
            self.assertIn('"""', body.splitlines()[line - 1])

    def test_python_handler(self):
        """Test Python handler functionality."""
        self.assertTrue(PythonHandler.can_handle(self.python_file))
        self.assertFalse(PythonHandler.can_handle(self.text_file))
        
        with open(self.python_file, "r") as f:
            content = f.read()
        
        is_valid, error = PythonHandler.verify_changes(content, content, self.python_file)
        self.assertTrue(is_valid)
        self.assertIsNone(error)
        
        invalid_content = content + "\nthis is not valid python:"
        is_valid, error = PythonHandler.verify_changes(content, invalid_content, self.python_file)
        self.assertFalse(is_valid)
        self.assertIsNotNone(error)
    
    def test_duplicate_detection(self):
        """Test duplicate detection in Python code."""
        with open(self.python_file, "r") as f:
            original_content = f.read()
        
        duplicated_content = original_content + '''
def test_function():
    """Duplicate function."""
    return False
'''
        
        has_duplicates, duplicates = PythonHandler.detect_duplicates(original_content, duplicated_content)
        self.assertTrue(has_duplicates)
        self.assertTrue(any("test_function" in d for d in duplicates),
                        f"Expected 'test_function' in duplicates, got: {duplicates}")
        
        has_duplicates, duplicates = PythonHandler.detect_duplicates(original_content, original_content)
        self.assertFalse(has_duplicates)
        self.assertEqual(len(duplicates), 0)
    
    def test_generic_handler(self):
        """Test generic text handler functionality."""
        self.assertTrue(GenericTextHandler.can_handle(self.text_file))
        self.assertTrue(GenericTextHandler.can_handle(self.python_file))
        
        with open(self.text_file, "r") as f:
            content = f.read()
        
        is_valid, error = GenericTextHandler.verify_changes(content, content, self.text_file)
        self.assertTrue(is_valid)
        self.assertIsNone(error)
        
        with open(self.text_file, "r") as f:
            original_content = f.read()
        
        duplicated_content = original_content + "Some lines might be repeated.\n"
        
        has_duplicates, duplicates = GenericTextHandler.detect_duplicates(original_content, duplicated_content)
        # GenericTextHandler cannot reliably detect duplicates without
        # language structure — it intentionally returns (False, []).
        self.assertFalse(has_duplicates)
        self.assertEqual(len(duplicates), 0)


if __name__ == "__main__":
    unittest.main()
