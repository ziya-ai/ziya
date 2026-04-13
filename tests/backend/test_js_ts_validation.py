"""
Tests for JavaScript/TypeScript validation fixes.

Guards against regressions of:
  1. False-positive "Possible missing semicolon" errors on TypeScript-specific
     syntax: bare identifiers in export blocks, type union lines, declare/
     interface/enum/type keywords, arrow function heads, multi-line expressions.

  2. tsc path resolution — should find node_modules/.bin/tsc before falling
     back to PATH, and raise FileNotFoundError cleanly when neither exists.

  3. pipeline_validator cwd fallback for when file is not found under
     ZIYA_USER_CODEBASE_DIR.
"""

import os
import sys
import unittest
import unittest.mock as mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from app.utils.diff_utils.language_handlers.javascript import JavaScriptHandler


class TestBasicJsValidationFalsePositives(unittest.TestCase):
    """
    _basic_js_validation must not flag TypeScript continuation lines,
    multi-line expressions, or keyword-started declarations as missing semicolons.
    """

    def _assert_valid(self, code: str, label: str = ''):
        ok, err = JavaScriptHandler._basic_js_validation(code)
        self.assertTrue(ok, f"Expected valid but got error for {label!r}: {err}")

    def _assert_invalid(self, code: str, label: str = ''):
        ok, err = JavaScriptHandler._basic_js_validation(code)
        self.assertFalse(ok, f"Expected invalid but passed for {label!r}")

    # -- Cases that were producing false positives before the fix --

    def test_bare_identifier_not_flagged(self):
        """A bare identifier on its own line is a continuation in an export block."""
        self._assert_valid('generateGraphvizWithClusters', 'bare identifier')

    def test_multiline_export_block(self):
        """Names inside a multi-line export { } block must not be flagged."""
        code = 'export {\n  generateGraphviz,\n  generateGraphvizWithClusters\n}'
        self._assert_valid(code, 'multi-line export block')

    def test_type_union_pipe_line(self):
        # Union/intersection operators appear at START of continuation lines in TS
        self._assert_valid('type Foo =\n  | string\n  | number;', 'type union multi-line')

    def test_type_intersection_amp_line(self):
        self._assert_valid('type Bar =\n  Foo\n  & Serializable;', 'type intersection multi-line')

    def test_declare_keyword(self):
        self._assert_valid('declare global {\n  const x: number;\n}', 'declare global')
        self._assert_valid('declare module "foo"', 'declare module')

    def test_interface_keyword(self):
        self._assert_valid('interface Foo {\n  name: string;\n}', 'interface')
        self._assert_valid('interface Foo extends Bar {\n  name: string;\n}', 'interface extends')

    def test_type_keyword(self):
        self._assert_valid('type Foo = string', 'type alias')
        self._assert_valid('type Foo = string | number', 'type union alias')

    def test_enum_keyword(self):
        self._assert_valid('enum Direction {\n  Up,\n  Down,\n}', 'enum')
        self._assert_valid('const enum Color {\n  Red,\n}', 'const enum')

    def test_abstract_keyword(self):
        self._assert_valid('abstract class Base {\n  abstract doThing(): void;\n}', 'abstract class')

    def test_async_keyword(self):
        self._assert_valid('async function doThing() {\n  return 42;\n}', 'async function')

    def test_arrow_function_head(self):
        # A line ending with => is a continuation — no semicolon expected
        self._assert_valid('const f = (x: string) =>\n  x.toUpperCase();', 'arrow head =>')
        # Full arrow expression with proper semicolons
        self._assert_valid('const doubled = [1, 2, 3].map((item) => item * 2);', 'arrow in chain')

    def test_import_statement(self):
        self._assert_valid("import { useState } from 'react'", 'import')
        self._assert_valid("import type { Foo } from './types'", 'import type')

    def test_export_statement(self):
        self._assert_valid('export { foo, bar }', 'export block')
        self._assert_valid('export type { Foo }', 'export type')
        self._assert_valid('export default function foo() {\n  return 1;\n}', 'export default')

    def test_open_paren_continuation(self):
        # A call split across lines — continuation line ends with ) which is excluded
        self._assert_valid('setConversations(prev => prev);\n', 'open paren')

    def test_open_bracket_continuation(self):
        self._assert_valid('const arr = [\n  1,\n  2,\n];', 'open bracket')

    def test_ternary_question_mark(self):
        self._assert_valid('  ? doThis()', 'ternary ?')

    def test_exact_drawioplugin_lines(self):
        """The exact lines from drawioPlugin.ts that triggered the original failure."""
        # Line 17 from the error report
        self._assert_valid('generateGraphvizWithClusters', 'drawioPlugin line 17')
        # Lines 62-63 from the error report
        self._assert_valid('  generateGraphviz,', 'drawioPlugin line 62')
        self._assert_valid('  generateGraphvizWithClusters', 'drawioPlugin line 63')

    def test_wellformed_ts_snippet_passes(self):
        code = (
            "import { useState } from 'react';\n"
            "\n"
            "interface Props {\n"
            "  name: string;\n"
            "  value: number;\n"
            "}\n"
            "\n"
            "export const MyComponent = ({ name, value }: Props) => {\n"
            "  const [count, setCount] = useState(0);\n"
            "  return count;\n"
            "};\n"
        )
        self._assert_valid(code, 'well-formed TypeScript snippet')

    # -- Cases that must still be caught --

    def test_missing_semicolon_is_advisory_only(self):
        """Semicolons are no longer hard failures — JS ASI and TS/JSX patterns
        make semicolon detection too noisy. Validation should still pass."""
        self._assert_valid('const x = 42', 'missing semicolon is advisory')

    def test_mismatched_bracket_detected(self):
        ok, err = JavaScriptHandler._basic_js_validation('function f() { return (1; }')
        self.assertFalse(ok)
        self.assertIn('Mismatched', err or '')

    def test_unclosed_bracket_detected(self):
        ok, err = JavaScriptHandler._basic_js_validation('function f() {')
        self.assertFalse(ok)
        self.assertIn('Unclosed', err or '')


class TestTscPathResolution(unittest.TestCase):
    """
    TypeScriptHandler should prefer node_modules/.bin/tsc over a global binary,
    and fall back gracefully to basic validation when neither exists.
    """

    def test_find_tsc_logic_present_in_source(self):
        """verify_changes must contain node_modules/.bin/tsc discovery logic."""
        import inspect
        from app.utils.diff_utils.language_handlers.typescript import TypeScriptHandler
        source = inspect.getsource(TypeScriptHandler.verify_changes)
        self.assertIn('node_modules', source)
        self.assertIn('.bin', source)
        self.assertIn('find_tsc', source)

    def test_skipLibCheck_flag_included(self):
        """The tsc invocation must include --skipLibCheck."""
        import inspect
        from app.utils.diff_utils.language_handlers.typescript import TypeScriptHandler
        source = inspect.getsource(TypeScriptHandler.verify_changes)
        self.assertIn('--skipLibCheck', source)

    def test_isolatedModules_and_noResolve_flags(self):
        """tsc invocation must include --isolatedModules and --noResolve for context-free validation."""
        import inspect
        from app.utils.diff_utils.language_handlers.typescript import TypeScriptHandler
        source = inspect.getsource(TypeScriptHandler.verify_changes)
        self.assertIn('--isolatedModules', source)
        self.assertIn('--noResolve', source)

    def test_tsx_uses_correct_suffix_and_jsx_flag(self):
        """For .tsx files, temp file must use .tsx suffix and --jsx flag must be passed."""
        import inspect
        from app.utils.diff_utils.language_handlers.typescript import TypeScriptHandler
        source = inspect.getsource(TypeScriptHandler.verify_changes)
        self.assertIn('.tsx', source, "Must handle .tsx suffix for temp files")
        self.assertIn('--jsx', source, "Must pass --jsx flag for .tsx files")

    def test_captures_stdout_for_diagnostics(self):
        """tsc writes diagnostics to stdout; error handling must check stdout."""
        import inspect
        from app.utils.diff_utils.language_handlers.typescript import TypeScriptHandler
        source = inspect.getsource(TypeScriptHandler.verify_changes)
        self.assertIn('result.stdout', source,
                      "Must read result.stdout since tsc writes diagnostics there")

    def test_fallback_when_tsc_missing(self):
        """When tsc is not findable, falls back to basic validation without raising."""
        from app.utils.diff_utils.language_handlers.typescript import TypeScriptHandler

        valid_ts = "const x: number = 42;\nexport default x;\n"

        with mock.patch('shutil.which', return_value=None), \
             mock.patch('os.path.isfile', return_value=False):
            try:
                result = TypeScriptHandler.verify_changes(
                    valid_ts, valid_ts, '/nonexistent/path/file.ts'
                )
                self.assertIsInstance(result, tuple)
                self.assertEqual(len(result), 2)
            except FileNotFoundError:
                self.fail(
                    "verify_changes must catch FileNotFoundError and fall back "
                    "to basic validation, not propagate the exception"
                )

    def test_no_duplicate_timeout_argument(self):
        """The subprocess.run call must have exactly one timeout argument."""
        import inspect
        from app.utils.diff_utils.language_handlers.typescript import TypeScriptHandler
        source = inspect.getsource(TypeScriptHandler.verify_changes)
        # Count occurrences of 'timeout=' inside the subprocess.run call
        run_start = source.find('subprocess.run(')
        self.assertGreater(run_start, -1)
        run_block = source[run_start:run_start + 400]
        timeout_count = run_block.count('timeout=')
        self.assertEqual(timeout_count, 1,
                         f"subprocess.run should have exactly one timeout= argument, found {timeout_count}")

    def test_nonsyntax_tsc_errors_fallback_to_basic(self):
        """When tsc fails with only import/type errors (TS2xxx), should fall back to basic validation."""
        from app.utils.diff_utils.language_handlers.typescript import TypeScriptHandler

        # Valid TS code that would fail tsc in isolation due to unresolved import
        code_with_import = "import { useState } from 'react';\nconst x: number = 42;\n"

        # Mock tsc as found but returning TS2307 (module not found) — NOT a syntax error
        mock_result = mock.Mock()
        mock_result.returncode = 1
        mock_result.stdout = "file.ts(1,30): error TS2307: Cannot find module 'react'.\n"
        mock_result.stderr = ""

        with mock.patch('shutil.which', return_value='/usr/bin/tsc'), \
             mock.patch('os.path.isfile', return_value=False), \
             mock.patch('subprocess.run', return_value=mock_result):
            is_valid, error = TypeScriptHandler.verify_changes(
                code_with_import, code_with_import, '/project/src/file.ts'
            )
            # Should fall back to basic validation and pass (code is structurally valid)
            self.assertTrue(is_valid, f"Non-syntax tsc errors should not reject valid code, got: {error}")

    def test_syntax_tsc_errors_reject(self):
        """When tsc fails with a syntax error (TS1xxx), should reject the diff."""
        from app.utils.diff_utils.language_handlers.typescript import TypeScriptHandler

        broken_code = "const x: number = ;\n"  # actual syntax error

        # Mock tsc returning TS1109 (expression expected) — a real syntax error
        mock_result = mock.Mock()
        mock_result.returncode = 1
        mock_result.stdout = "file.ts(1,19): error TS1109: Expression expected.\n"
        mock_result.stderr = ""

        with mock.patch('shutil.which', return_value='/usr/bin/tsc'), \
             mock.patch('os.path.isfile', return_value=False), \
             mock.patch('subprocess.run', return_value=mock_result):
            is_valid, error = TypeScriptHandler.verify_changes(
                broken_code, broken_code, '/project/src/file.ts'
            )
            self.assertFalse(is_valid, "Syntax errors (TS1xxx) should reject the diff")
            self.assertIn('TS1109', error or '')


class TestPipelineValidatorPathResolution(unittest.TestCase):
    """
    pipeline_validator.py should fall back to os.getcwd() when the file is
    not found under ZIYA_USER_CODEBASE_DIR.
    """

    def _get_validator_source(self) -> str:
        validator_path = os.path.normpath(os.path.join(
            os.path.dirname(__file__),
            '..', '..',
            'app', 'utils', 'diff_utils', 'validation', 'pipeline_validator.py'
        ))
        with open(validator_path) as f:
            return f.read()

    def test_cwd_fallback_present(self):
        source = self._get_validator_source()
        self.assertIn('os.getcwd()', source,
                      "pipeline_validator should fall back to os.getcwd() "
                      "when file not found at primary codebase_dir")

    def test_cwd_fallback_only_used_when_primary_path_missing(self):
        """The cwd fallback must be guarded by an os.path.exists check."""
        source = self._get_validator_source()
        # The pattern should be: if not os.path.exists(full_path): ... os.getcwd()
        cwd_idx = source.find('os.getcwd()')
        self.assertGreater(cwd_idx, -1)
        # There must be an 'os.path.exists' check before the cwd fallback
        pre_cwd = source[:cwd_idx]
        self.assertIn('os.path.exists', pre_cwd,
                      "cwd fallback should only activate when primary path doesn't exist")


if __name__ == '__main__':
    unittest.main()
