"""
Regression tests for ASR F-024: MCP server scripts must never be resolved
against the current working directory or the user's workspace.

A malicious repository could otherwise drop a file matching a configured
server-script name and have it auto-executed when Ziya starts in that
directory. _resolve_relative_script() searches trusted package/installation
roots only and returns None (clean failure) when the script is absent.
"""

import os
import tempfile
import unittest

from app.mcp.client import _resolve_relative_script


class TestResolveRelativeScript(unittest.TestCase):
    def test_found_in_trusted_root(self):
        with tempfile.TemporaryDirectory() as trusted:
            script_path = os.path.join(trusted, "server.py")
            with open(script_path, "w") as f:
                f.write("# server\n")
            resolved = _resolve_relative_script("server.py", [trusted])
            self.assertEqual(resolved, script_path)

    def test_first_trusted_root_wins(self):
        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
            for d in (a, b):
                with open(os.path.join(d, "server.py"), "w") as f:
                    f.write("# server\n")
            resolved = _resolve_relative_script("server.py", [a, b])
            self.assertEqual(resolved, os.path.join(a, "server.py"))

    def test_not_found_returns_none(self):
        with tempfile.TemporaryDirectory() as trusted:
            self.assertIsNone(_resolve_relative_script("server.py", [trusted]))

    def test_cwd_is_not_a_trusted_root(self):
        # The core F-024 guarantee: a script present ONLY in cwd must not
        # resolve. We simulate a malicious workspace by writing the script
        # into a temp dir, chdir-ing into it, and passing trusted roots that
        # do NOT include cwd.
        with tempfile.TemporaryDirectory() as malicious_cwd, \
             tempfile.TemporaryDirectory() as trusted_empty:
            with open(os.path.join(malicious_cwd, "shell_server.py"), "w") as f:
                f.write("# malicious\n")
            prev = os.getcwd()
            try:
                os.chdir(malicious_cwd)
                # trusted roots exclude cwd → must NOT find the malicious file
                resolved = _resolve_relative_script(
                    "shell_server.py", [trusted_empty]
                )
                self.assertIsNone(
                    resolved,
                    "F-024 REGRESSION: script resolved from cwd/workspace",
                )
            finally:
                os.chdir(prev)

    def test_empty_and_none_roots_skipped(self):
        with tempfile.TemporaryDirectory() as trusted:
            script_path = os.path.join(trusted, "server.py")
            with open(script_path, "w") as f:
                f.write("# server\n")
            # Empty-string roots must be skipped without raising.
            resolved = _resolve_relative_script("server.py", ["", trusted])
            self.assertEqual(resolved, script_path)

    def test_no_roots_returns_none(self):
        self.assertIsNone(_resolve_relative_script("server.py", []))


class TestCwdNotInPossibleRoots(unittest.TestCase):
    """Static guard: the source must not list os.getcwd() among the
    trusted MCP script-resolution roots."""

    def test_getcwd_not_in_possible_roots_block(self):
        import inspect
        from app.mcp import client
        src = inspect.getsource(client.MCPClient.connect)
        # Find the possible_roots assignment and assert os.getcwd() isn't in it.
        self.assertIn("possible_roots", src)
        # The resolution roots block must not reference os.getcwd().
        # (A workspace fallback would reintroduce F-024.)
        roots_section = src.split("possible_roots", 1)[1].split("]", 1)[0]
        self.assertNotIn(
            "os.getcwd()", roots_section,
            "F-024 REGRESSION: os.getcwd() is back in possible_roots",
        )


if __name__ == "__main__":
    unittest.main()
