"""Tests for shell_server.py secure execution (no shell=True).

Validates that the shell server executes commands via subprocess with
shell=False, using Python-side expansion and pipeline orchestration
instead of delegating to the system shell.
"""

import os
import subprocess
import sys
import unittest
from unittest.mock import patch, MagicMock

# Ensure the project root is importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.mcp_servers.shell_server import ShellServer


class TestNoShellTrueInSource(unittest.TestCase):
    """Static verification that shell=True is absent from the module."""

    def test_no_shell_true_in_source(self):
        """The shell_server module must not contain shell=True."""
        import inspect
        source = inspect.getsource(ShellServer)
        self.assertNotIn(
            "shell=True", source,
            "shell_server.py still contains shell=True — "
            "all subprocess calls must use shell=False"
        )


class TestExpandAndTokenize(unittest.TestCase):
    """Unit tests for _expand_and_tokenize."""

    def setUp(self):
        self.srv = ShellServer()

    def test_simple_command(self):
        result = self.srv._expand_and_tokenize("ls -la /tmp")
        self.assertEqual(result, ["ls", "-la", "/tmp"])

    def test_quoted_arguments(self):
        result = self.srv._expand_and_tokenize('echo "hello world"')
        self.assertEqual(result, ["echo", "hello world"])

    def test_single_quoted_arguments(self):
        result = self.srv._expand_and_tokenize("echo 'hello world'")
        self.assertEqual(result, ["echo", "hello world"])

    def test_tilde_expansion(self):
        result = self.srv._expand_and_tokenize("ls ~/Documents")
        expected_home = os.path.expanduser("~")
        self.assertEqual(result[0], "ls")
        self.assertTrue(
            result[1].startswith(expected_home),
            f"Expected tilde expansion, got {result[1]}"
        )

    def test_env_var_expansion(self):
        with patch.dict(os.environ, {"TEST_SHELL_VAR": "/test/path"}):
            result = self.srv._expand_and_tokenize("ls $TEST_SHELL_VAR")
            self.assertEqual(result, ["ls", "/test/path"])

    def test_glob_expansion_no_matches(self):
        """When glob has no matches, the literal pattern is preserved."""
        result = self.srv._expand_and_tokenize("ls /nonexistent_dir_xyz/*.abc")
        self.assertEqual(result, ["ls", "/nonexistent_dir_xyz/*.abc"])

    def test_glob_expansion_in_tmp(self):
        """Glob expansion works for existing files."""
        # Create a temp file so glob has something to match
        import tempfile
        with tempfile.NamedTemporaryFile(suffix=".shelltest", dir="/tmp", delete=False) as f:
            tmp_path = f.name
        try:
            result = self.srv._expand_and_tokenize("ls /tmp/*.shelltest")
            self.assertIn(tmp_path, result)
        finally:
            os.unlink(tmp_path)

    def test_empty_command(self):
        result = self.srv._expand_and_tokenize("")
        self.assertEqual(result, [])

    def test_malformed_quotes_fallback(self):
        """Malformed quotes should not crash — falls back to split()."""
        result = self.srv._expand_and_tokenize('echo "unclosed')
        self.assertIsInstance(result, list)
        self.assertTrue(len(result) > 0)


class TestResolveSubstitutions(unittest.TestCase):
    """Unit tests for _resolve_substitutions."""

    def setUp(self):
        self.srv = ShellServer()

    def test_dollar_paren_substitution(self):
        result = self.srv._resolve_substitutions("echo $(echo hello)", 10, "/tmp")
        self.assertEqual(result, "echo hello")

    def test_backtick_substitution(self):
        result = self.srv._resolve_substitutions("echo `echo world`", 10, "/tmp")
        self.assertEqual(result, "echo world")

    def test_no_substitutions(self):
        result = self.srv._resolve_substitutions("ls -la", 10, "/tmp")
        self.assertEqual(result, "ls -la")


class TestExecutePipeline(unittest.TestCase):
    """Integration tests for _execute_pipeline."""

    def setUp(self):
        self.srv = ShellServer()

    def test_simple_command(self):
        result = self.srv._execute_pipeline("echo hello", 10, "/tmp")
        self.assertEqual(result.returncode, 0)
        self.assertIn("hello", result.stdout)

    def test_pipe(self):
        result = self.srv._execute_pipeline(
            'printf "foo\\nbar\\nbaz" | grep bar', 10, "/tmp"
        )
        self.assertEqual(result.returncode, 0)
        self.assertIn("bar", result.stdout)
        self.assertNotIn("foo", result.stdout)

    def test_and_operator_success(self):
        result = self.srv._execute_pipeline("echo first && echo second", 10, "/tmp")
        self.assertEqual(result.returncode, 0)
        self.assertIn("first", result.stdout)
        self.assertIn("second", result.stdout)

    def test_and_operator_short_circuit(self):
        result = self.srv._execute_pipeline("false && echo should_not_appear", 10, "/tmp")
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("should_not_appear", result.stdout)

    def test_or_operator_fallback(self):
        result = self.srv._execute_pipeline("false || echo fallback", 10, "/tmp")
        self.assertEqual(result.returncode, 0)
        self.assertIn("fallback", result.stdout)

    def test_or_operator_skip_on_success(self):
        result = self.srv._execute_pipeline("echo ok || echo skip", 10, "/tmp")
        self.assertEqual(result.returncode, 0)
        self.assertIn("ok", result.stdout)
        self.assertNotIn("skip", result.stdout)

    def test_semicolon_sequential(self):
        result = self.srv._execute_pipeline("echo one ; echo two", 10, "/tmp")
        self.assertIn("one", result.stdout)
        self.assertIn("two", result.stdout)

    def test_cwd_is_respected(self):
        result = self.srv._execute_pipeline("pwd", 10, "/tmp")
        self.assertEqual(result.returncode, 0)
        # /tmp may resolve to /private/tmp on macOS
        self.assertTrue(
            result.stdout.strip().endswith("/tmp"),
            f"Expected cwd /tmp, got {result.stdout.strip()}"
        )

    def test_piped_commands_bc(self):
        """The canonical 'echo expr | bc' pattern must work."""
        result = self.srv._execute_pipeline('echo "2+3" | bc', 10, "/tmp")
        self.assertEqual(result.returncode, 0)
        self.assertIn("5", result.stdout.strip())

    def test_shell_false_used(self):
        """Verify subprocess.run is called with shell=False."""
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = subprocess.CompletedProcess(
                args=["echo", "test"], returncode=0, stdout="test\n", stderr=""
            )
            self.srv._execute_pipeline("echo test", 10, "/tmp")
            # Every call must have shell=False
            for call in mock_run.call_args_list:
                _, kwargs = call
                self.assertFalse(
                    kwargs.get("shell", False),
                    f"subprocess.run called with shell=True: {call}"
                )

    def test_timeout_propagated(self):
        """Timeout parameter is passed to subprocess.run."""
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = subprocess.CompletedProcess(
                args=["echo", "hi"], returncode=0, stdout="hi\n", stderr=""
            )
            self.srv._execute_pipeline("echo hi", 42, "/tmp")
            _, kwargs = mock_run.call_args
            self.assertEqual(kwargs["timeout"], 42)


class TestHandleRequestSecure(unittest.TestCase):
    """End-to-end test that handle_request uses secure execution."""

    def test_handle_request_does_not_use_shell_true(self):
        """Calling tools/call run_shell_command must use shell=False internally."""
        srv = ShellServer()

        import asyncio

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = subprocess.CompletedProcess(
                args=["echo", "hello"], returncode=0, stdout="hello\n", stderr=""
            )

            loop = asyncio.new_event_loop()
            try:
                response = loop.run_until_complete(srv.handle_request({
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "tools/call",
                    "params": {
                        "name": "run_shell_command",
                        "arguments": {"command": "echo hello"}
                    }
                }))
            finally:
                loop.close()

            # Should succeed
            self.assertIn("result", response, f"Expected success, got: {response}")

            # Verify shell=False on all calls
            for call in mock_run.call_args_list:
                _, kwargs = call
                self.assertFalse(
                    kwargs.get("shell", False),
                    f"handle_request used shell=True: {call}"
                )


if __name__ == "__main__":
    unittest.main()
