"""
Regression tests for ASR F-003: MCP server subprocesses must not inherit
credential-bearing environment variables from the parent process.

MCP servers are installable from a third-party registry and therefore treated
as untrusted code. build_mcp_subprocess_env() strips AWS/Midway credentials,
tokens, secrets, and API keys from the inherited environment while preserving
benign vars and honouring explicit opt-ins.
"""

import unittest

from app.mcp.client import (
    build_mcp_subprocess_env,
    _is_sensitive_env_key,
)


class TestIsSensitiveEnvKey(unittest.TestCase):
    def test_aws_credentials_are_sensitive(self):
        for key in (
            "AWS_ACCESS_KEY_ID",
            "AWS_SECRET_ACCESS_KEY",
            "AWS_SESSION_TOKEN",
        ):
            self.assertTrue(_is_sensitive_env_key(key), key)

    def test_midway_and_token_vars_are_sensitive(self):
        for key in (
            "MIDWAY_TOKEN",
            "GITHUB_TOKEN",
            "MY_API_KEY",
            "DB_PASSWORD",
            "SOME_SECRET",
            "X_CREDENTIAL",
        ):
            self.assertTrue(_is_sensitive_env_key(key), key)

    def test_benign_vars_are_not_sensitive(self):
        for key in ("PATH", "HOME", "LANG", "TERM", "USER", "SHELL", "TMPDIR"):
            self.assertFalse(_is_sensitive_env_key(key), key)

    def test_aws_region_is_allowed(self):
        # Region is not a credential and AWS-backed MCP servers commonly need it.
        self.assertFalse(_is_sensitive_env_key("AWS_REGION"))
        self.assertFalse(_is_sensitive_env_key("AWS_DEFAULT_REGION"))


class TestBuildMcpSubprocessEnv(unittest.TestCase):
    def _base(self):
        return {
            "PATH": "/usr/bin",
            "HOME": "/home/dev",
            "LANG": "en_US.UTF-8",
            "AWS_ACCESS_KEY_ID": "AKIA_SECRET",
            "AWS_SECRET_ACCESS_KEY": "shhh",
            "AWS_SESSION_TOKEN": "tok",
            "AWS_REGION": "us-west-2",
            "MIDWAY_TOKEN": "midway",
            "GITHUB_TOKEN": "ghp_xxx",
        }

    def test_strips_credentials_keeps_benign(self):
        out = build_mcp_subprocess_env(base_env=self._base())
        self.assertIn("PATH", out)
        self.assertIn("HOME", out)
        self.assertIn("LANG", out)
        self.assertIn("AWS_REGION", out)  # non-secret AWS var preserved
        self.assertNotIn("AWS_ACCESS_KEY_ID", out)
        self.assertNotIn("AWS_SECRET_ACCESS_KEY", out)
        self.assertNotIn("AWS_SESSION_TOKEN", out)
        self.assertNotIn("MIDWAY_TOKEN", out)
        self.assertNotIn("GITHUB_TOKEN", out)

    def test_server_config_env_always_applied(self):
        # Operator-configured env wins even if it shares a sensitive name.
        out = build_mcp_subprocess_env(
            process_env={"AWS_ACCESS_KEY_ID": "scoped-key", "FOO": "bar"},
            base_env=self._base(),
        )
        self.assertEqual(out["AWS_ACCESS_KEY_ID"], "scoped-key")
        self.assertEqual(out["FOO"], "bar")

    def test_passthrough_allowlist(self):
        import os
        os.environ["ZIYA_MCP_ENV_PASSTHROUGH"] = "GITHUB_TOKEN"
        try:
            out = build_mcp_subprocess_env(base_env=self._base())
            self.assertIn("GITHUB_TOKEN", out)        # explicitly allowed
            self.assertNotIn("AWS_SECRET_ACCESS_KEY", out)  # still stripped
        finally:
            del os.environ["ZIYA_MCP_ENV_PASSTHROUGH"]

    def test_no_sensitive_value_leaks(self):
        out = build_mcp_subprocess_env(base_env=self._base())
        self.assertNotIn("shhh", out.values())
        self.assertNotIn("midway", out.values())


if __name__ == "__main__":
    unittest.main()
