"""
Tests for TypeScript/JSX language validation false positive prevention.

Validates that:
1. When tsc reports only non-syntax errors (TS2xxx), we trust it and pass
2. The fallback heuristic path (no tsc) doesn't block valid TS/JSX patterns
3. Real structural errors are still caught
"""

import pytest
from unittest.mock import patch, MagicMock
from app.utils.diff_utils.language_handlers.typescript import TypeScriptHandler
from app.utils.diff_utils.language_handlers.javascript import JavaScriptHandler


class TestTscNonSyntaxDiagnostics:
    """When tsc reports only TS2xxx errors, syntax is valid — should pass."""

    @patch('subprocess.run')
    @patch('shutil.which', return_value='/usr/bin/tsc')
    def test_tsc_ts2xxx_only_passes(self, mock_which, mock_run):
        """tsc reporting only import resolution errors should not trigger fallback."""
        mock_run.return_value = MagicMock(
            returncode=1,
            stdout="test.tsx(5,10): error TS2307: Cannot find module 'react'.\n",
            stderr=""
        )
        original = "export {};\n"
        modified = "import React from 'react';\nexport const X = () => <div />;\n"

        is_valid, error = TypeScriptHandler.verify_changes(original, modified, "test.tsx")
        assert is_valid, f"tsc TS2xxx-only should pass, got: {error}"

    @patch('subprocess.run')
    @patch('shutil.which', return_value='/usr/bin/tsc')
    def test_tsc_ts1xxx_still_fails(self, mock_which, mock_run):
        """tsc reporting syntax errors (TS1xxx) should still fail."""
        mock_run.return_value = MagicMock(
            returncode=1,
            stdout="test.ts(3,1): error TS1005: ';' expected.\n",
            stderr=""
        )
        original = "const x = 1;\n"
        modified = "const x = \n"

        is_valid, error = TypeScriptHandler.verify_changes(original, modified, "test.ts")
        assert not is_valid, "tsc TS1xxx syntax error should fail"

    @patch('subprocess.run')
    @patch('shutil.which', return_value='/usr/bin/tsc')
    def test_tsc_mixed_errors_fails_on_syntax(self, mock_which, mock_run):
        """Mixed TS1xxx + TS2xxx should fail because of the syntax error."""
        mock_run.return_value = MagicMock(
            returncode=1,
            stdout=(
                "test.ts(2,1): error TS1005: ';' expected.\n"
                "test.ts(5,10): error TS2307: Cannot find module 'foo'.\n"
            ),
            stderr=""
        )
        original = "const x = 1;\n"
        modified = "const x = \nimport foo from 'foo';\n"

        is_valid, error = TypeScriptHandler.verify_changes(original, modified, "test.ts")
        assert not is_valid, "Mixed errors with TS1xxx should still fail"


class TestFallbackHeuristicAdvisoryOnly:
    """When tsc is not available, heuristic checks should be advisory, not blocking."""

    @patch('shutil.which', return_value=None)
    def test_any_type_does_not_block(self, mock_which):
        """Use of `any` type should not block diff application."""
        original = "const x = 1;\n"
        modified = "function process(data: any): void {\n  console.log(data);\n}\n"

        is_valid, error = TypeScriptHandler.verify_changes(original, modified, "test.ts")
        assert is_valid, f"'any' type should not block: {error}"

    @patch('shutil.which', return_value=None)
    def test_jsx_angle_brackets_do_not_block(self, mock_which):
        """JSX elements in object literals should not be misread as broken generics."""
        original = "const x = 1;\n"
        modified = (
            "const items = [\n"
            "  { key: 'a', icon: <FolderIcon />, onClick: () => {} },\n"
            "];\n"
        )

        is_valid, error = TypeScriptHandler.verify_changes(original, modified, "test.tsx")
        assert is_valid, f"JSX angle brackets should not block: {error}"

    @patch('shutil.which', return_value=None)
    def test_real_bracket_mismatch_still_caught(self, mock_which):
        """Mismatched brackets should still be caught even without tsc."""
        original = "const x = 1;\n"
        modified = "function broken() {\n  if (true) {\n    console.log('x');\n}\n"

        is_valid, error = TypeScriptHandler.verify_changes(original, modified, "test.ts")
        assert not is_valid, "Mismatched brackets should be caught"


class TestBasicJSValidationImprovements:
    """Tests for semicolon heuristic improvements in _basic_js_validation."""

    def test_trailing_comment_after_semicolon(self):
        """Lines like `foo: string;  // comment` should not flag missing semicolons."""
        content = "interface Foo {\n  name: string;  // the name\n  count: number; /* items */\n}\n"
        is_valid, error = JavaScriptHandler._basic_js_validation(content)
        assert is_valid, f"Trailing comments after semicolons should pass: {error}"

    def test_spread_syntax(self):
        content = "const C = ({ name, ...other }) => {\n  return null;\n};\n"
        is_valid, error = JavaScriptHandler._basic_js_validation(content)
        assert is_valid, f"Spread syntax should pass: {error}"

    def test_jsx_tags(self):
        content = "function App() {\n  return (\n    <div>\n      <span>Hi</span>\n    </div>\n  );\n}\n"
        is_valid, error = JavaScriptHandler._basic_js_validation(content)
        assert is_valid, f"JSX tags should pass: {error}"

    def test_decorators(self):
        content = "@Component({selector: 'root'})\nclass AppComponent {\n  title = 'app';\n}\n"
        is_valid, error = JavaScriptHandler._basic_js_validation(content)
        assert is_valid, f"Decorators should pass: {error}"

    def test_valid_tsx_component(self):
        content = (
            "import React from 'react';\n"
            "interface Props {\n  name: string;\n  count: number;  // item count\n}\n"
            "export const W: React.FC<Props> = ({ name, count }) => {\n"
            "  return <div>{name}: {count}</div>;\n"
            "};\n"
        )
        is_valid, error = JavaScriptHandler._basic_js_validation(content)
        assert is_valid, f"Valid TSX component should pass: {error}"


class TestKeywordFilterInFunctionBodies:
    """Tests for _extract_function_bodies not treating keywords as functions."""

    def test_if_not_extracted_as_function(self):
        """'if' blocks should not be extracted as function bodies."""
        content = (
            "if (data.active) {\n"
            "  console.log('yes');\n"
            "}\n"
            "function render() {\n"
            "  return null;\n"
            "}\n"
            "if (other.thing) {\n"
            "  console.log('no');\n"
            "}\n"
        )
        bodies = JavaScriptHandler._extract_function_bodies(content)
        assert 'if' not in bodies, f"'if' should not be extracted as function, got: {list(bodies.keys())}"
        assert 'render' in bodies, "Real functions should still be extracted"

    def test_while_not_extracted_as_function(self):
        """'while' blocks should not be extracted as function bodies."""
        content = (
            "while (running) {\n"
            "  process();\n"
            "}\n"
        )
        bodies = JavaScriptHandler._extract_function_bodies(content)
        assert 'while' not in bodies

    def test_for_not_extracted_as_function(self):
        """'for' blocks should not be extracted as function bodies."""
        content = (
            "for (let i = 0; i < 10; i++) {\n"
            "  console.log(i);\n"
            "}\n"
        )
        bodies = JavaScriptHandler._extract_function_bodies(content)
        assert 'for' not in bodies

    def test_similar_if_blocks_not_flagged_as_duplicates(self):
        """Multiple if blocks should not be flagged as similar functions."""
        content = (
            "if (a) {\n  console.log('a');\n}\n"
            "if (b) {\n  console.log('b');\n}\n"
            "function render() {\n  return null;\n}\n"
        )
        similar = JavaScriptHandler._detect_similar_functions(content)
        # Should not find 'if' in any similar pair
        for pair, _ in similar:
            assert 'if' not in pair[0] and 'if' not in pair[1], \
                f"'if' blocks should not be in similar functions: {pair}"


class TestDuplicateDetectionAdvisoryOnly:
    """Tests that duplicate detection doesn't block diff application."""

    def test_duplicate_detection_does_not_block_apply(self):
        """Even if duplicates are detected, apply_diff_atomically should succeed."""
        import tempfile
        import os
        from app.utils.diff_utils.application.git_diff import apply_diff_atomically

        # Create a temp file with content that has "duplicates"
        original = "function render() {\n  return 1;\n}\n"
        with tempfile.NamedTemporaryFile(mode='w', suffix='.js', delete=False) as f:
            f.write(original)
            temp_path = f.name

        try:
            # A simple diff that changes the return value
            diff = (
                f"--- a/{os.path.basename(temp_path)}\n"
                f"+++ b/{os.path.basename(temp_path)}\n"
                "@@ -1,3 +1,3 @@\n"
                " function render() {\n"
                "-  return 1;\n"
                "+  return 2;\n"
                " }\n"
            )
            result = apply_diff_atomically(temp_path, diff)
            assert result is not None
            # Should succeed even if duplicate detection fires
            assert result.get("status") != "error" or \
                result.get("details", {}).get("type") != "duplicate_code", \
                f"Duplicate detection should not block application: {result}"
        finally:
            os.unlink(temp_path)
