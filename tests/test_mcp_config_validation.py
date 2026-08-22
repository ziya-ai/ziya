"""
Tests for per-server mcp_config.json validation.

Whole-file JSON syntax errors were already reported. A file that parsed but
contained an unusable entry — a typo'd key, a non-string env value — produced a
server that was silently absent from the GUI, with the reason discarded into a
logger.info. These tests pin the findings contract the GUI panel renders.

Validation is advisory: it must never reject a server the loader would accept,
and must never raise, since it runs during startup.
"""

import json

import pytest

from app.mcp.config_validation import validate_config, validate_server_entry


def _codes(findings):
    return {f["code"] for f in findings}


def _by_code(findings, code):
    for f in findings:
        if f["code"] == code:
            return f
    return None


class TestUnknownKeys:
    """The silent-typo case: the user's intent was never honoured and nothing
    told them why."""

    def test_typo_suggests_correction(self):
        findings = validate_server_entry("fs", {"commands": "npx"})
        f = _by_code(findings, "unknown_key_typo")
        assert f is not None
        assert f["suggestion"] == "command"
        assert "commands" in f["summary"]
        assert f["severity"] == "error"

    def test_case_only_mismatch_is_caught(self):
        # difflib scores "COMMAND" as unrelated to "command"; an explicit
        # lowercase check is what catches it.
        findings = validate_server_entry("fs", {"COMMAND": "npx"})
        f = _by_code(findings, "unknown_key_typo")
        assert f is not None
        assert f["suggestion"] == "command"

    @pytest.mark.parametrize("alias,expected", [
        ("cmd", "command"),
        ("executable", "command"),
        ("arguments", "args"),
        ("argv", "args"),
        ("environment", "env"),
        ("endpoint", "url"),
    ])
    def test_known_aliases_resolve(self, alias, expected):
        findings = validate_server_entry("s", {alias: "x", "command": "echo"})
        f = _by_code(findings, "unknown_key_typo")
        assert f is not None, f"{alias} produced no typo finding"
        assert f["suggestion"] == expected

    def test_genuinely_unknown_key_is_warning_without_guess(self):
        """A confidently wrong suggestion is worse than none."""
        findings = validate_server_entry(
            "s", {"command": "echo", "totally_unrelated_xyz": 1}
        )
        f = _by_code(findings, "unknown_key")
        assert f is not None
        assert f["severity"] == "warning"
        assert f.get("suggestion") is None

    def test_all_known_keys_produce_no_findings(self):
        entry = {
            "command": "npx", "args": ["-y", "pkg"], "env": {"A": "b"},
            "enabled": True, "builtin": False, "trusted": False,
            "timeout": 60, "max_retries": 3, "description": "d",
            "external_server": True, "enable_response_cleaning": True,
            "workspace_scoped": False, "name": "s",
        }
        assert validate_server_entry("s", entry) == []


class TestLaunchKey:

    def test_missing_command_is_error(self):
        findings = validate_server_entry("s", {"args": ["x"]})
        assert "missing_launch_key" in _codes(findings)

    def test_url_alone_is_sufficient(self):
        findings = validate_server_entry("s", {"url": "https://x.invalid/mcp"})
        assert "missing_launch_key" not in _codes(findings)

    def test_installation_path_alone_is_sufficient(self):
        findings = validate_server_entry("s", {"installation_path": "/opt/s"})
        assert "missing_launch_key" not in _codes(findings)

    def test_typo_is_named_as_probable_cause(self):
        """A typo'd 'command' must not surface only as 'no command' — the user
        needs to know the key they wrote is the reason."""
        findings = validate_server_entry("s", {"commands": "npx"})
        assert "unknown_key_typo" in _codes(findings)
        launch = _by_code(findings, "missing_launch_key")
        assert launch is not None
        assert "commands" in launch["detail"]


class TestValueTypes:

    def test_env_value_null_is_error(self):
        findings = validate_server_entry(
            "gh", {"command": "npx", "env": {"GITHUB_TOKEN": None}}
        )
        f = _by_code(findings, "env_value_not_string")
        assert f is not None
        assert "GITHUB_TOKEN" in f["summary"]

    def test_env_value_number_is_error(self):
        findings = validate_server_entry(
            "s", {"command": "npx", "env": {"PORT": 8080}}
        )
        assert "env_value_not_string" in _codes(findings)

    def test_env_string_values_are_clean(self):
        findings = validate_server_entry(
            "s", {"command": "npx", "env": {"PORT": "8080"}}
        )
        assert findings == []

    def test_command_as_array_is_warning_not_error(self):
        """The loader normalizes this, so it works — flagging it as fatal would
        be wrong."""
        findings = validate_server_entry("s", {"command": ["npx", "-y", "p"]})
        f = _by_code(findings, "command_as_array")
        assert f is not None
        assert f["severity"] == "warning"

    def test_empty_command_array_is_error(self):
        findings = validate_server_entry("s", {"command": []})
        assert "empty_command_array" in _codes(findings)

    def test_command_wrong_type_is_error(self):
        findings = validate_server_entry("s", {"command": 42})
        assert "command_wrong_type" in _codes(findings)

    def test_args_wrong_type_is_warning(self):
        findings = validate_server_entry("s", {"command": "x", "args": "notalist"})
        f = _by_code(findings, "args_wrong_type")
        assert f is not None
        assert f["severity"] == "warning"

    def test_entry_not_object(self):
        findings = validate_server_entry("s", "just-a-string")
        assert "entry_not_object" in _codes(findings)


class TestPathAndAmbiguity:

    def test_relative_script_path_is_flagged(self):
        """F-024 resolves relative scripts against trusted roots only, so a
        relative path in user config almost never resolves."""
        findings = validate_server_entry(
            "s", {"command": "python", "args": ["tools/srv.py"]}
        )
        f = _by_code(findings, "relative_script_path")
        assert f is not None
        assert f["severity"] == "warning"

    def test_absolute_script_path_is_clean(self):
        findings = validate_server_entry(
            "s", {"command": "python", "args": ["/opt/srv.py"]}
        )
        assert "relative_script_path" not in _codes(findings)

    def test_dot_relative_path_is_not_flagged_as_bare_relative(self):
        findings = validate_server_entry(
            "s", {"command": "python", "args": ["./srv.py"]}
        )
        assert "relative_script_path" not in _codes(findings)

    def test_enabled_and_disabled_together_is_ambiguous(self):
        findings = validate_server_entry(
            "s", {"command": "x", "enabled": True, "disabled": True}
        )
        f = _by_code(findings, "enabled_and_disabled")
        assert f is not None
        assert f["severity"] == "warning"


class TestLineNumbers:
    """Line numbers let the GUI point at the offending line. They are
    presentational: a missing number degrades the message but must never
    change validation behaviour."""

    RAW = '''{
  "mcpServers": {
    "filesystem": {
      "commands": "npx"
    },
    "github": {
      "command": "npx",
      "env": {
        "GITHUB_TOKEN": null
      }
    }
  }
}'''

    def test_typo_line_is_reported(self):
        data = json.loads(self.RAW)
        findings = validate_config(data, self.RAW)
        f = _by_code(findings, "unknown_key_typo")
        assert f["line"] == 4

    def test_env_value_line_is_reported(self):
        data = json.loads(self.RAW)
        findings = validate_config(data, self.RAW)
        f = _by_code(findings, "env_value_not_string")
        assert f["line"] == 9

    def test_missing_raw_text_still_validates(self):
        """Validation must work without raw text; only line numbers are lost."""
        data = json.loads(self.RAW)
        findings = validate_config(data)
        assert "unknown_key_typo" in _codes(findings)
        assert _by_code(findings, "unknown_key_typo")["line"] is None

    def test_same_key_in_two_servers_resolves_per_server(self):
        raw = '''{
  "mcpServers": {
    "a": {
      "command": "echo"
    },
    "b": {
      "command": "echo",
      "cmd": "dupe"
    }
  }
}'''
        findings = validate_config(json.loads(raw), raw)
        f = _by_code(findings, "unknown_key_typo")
        assert f["server"] == "b"
        assert f["line"] == 8, (
            "line scan must stay within the server's own block"
        )


class TestConfigLevel:

    def test_valid_config_is_clean(self):
        data = {"mcpServers": {"s": {"command": "npx", "args": ["-y", "p"]}}}
        assert validate_config(data) == []

    def test_empty_config_is_clean(self):
        assert validate_config({}) == []
        assert validate_config({"mcpServers": {}}) == []

    def test_missing_mcpservers_wrapper_is_reported(self):
        """Servers written at the root are never loaded; say so rather than
        reporting an empty config."""
        data = {"filesystem": {"command": "npx"}}
        findings = validate_config(data)
        assert "missing_mcpservers_key" in _codes(findings)

    def test_mcpservers_wrong_type(self):
        findings = validate_config({"mcpServers": []})
        assert "mcpservers_not_object" in _codes(findings)

    def test_root_not_object(self):
        findings = validate_config(["not", "an", "object"])
        assert "config_not_object" in _codes(findings)

    def test_multiple_servers_each_reported(self):
        data = {"mcpServers": {
            "a": {"commands": "npx"},
            "b": {"command": "npx", "env": {"X": None}},
            "c": {"command": "npx"},
        }}
        findings = validate_config(data)
        servers = {f["server"] for f in findings}
        assert servers == {"a", "b"}, "clean server 'c' must not be reported"

    @pytest.mark.parametrize("junk", [
        None, 42, "string", [], {"mcpServers": None},
        {"mcpServers": {"s": None}}, {"mcpServers": {"s": []}},
    ])
    def test_never_raises_on_junk(self, junk):
        """Validation runs during startup; an exception here would break MCP
        initialization entirely."""
        result = validate_config(junk)
        assert isinstance(result, list)
