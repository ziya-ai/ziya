"""
ASR Part 0 — the shell server reads privilege state only via ``_scope_env``.

``ShellServer.__init__`` states the invariant explicitly: after the
escalation-integrity gate runs, every later read of a privilege-bearing value
uses ``self._scope_env``, never ``os.environ``. ``GIT_OPERATIONS_ENABLED`` was
reading ``os.environ`` directly.

That was not exploitable, and the reason is worth pinning rather than
re-deriving: the var is deliberately outside ``ESCALATION_ENV_KEYS``, so
``strip_escalations`` never rewrites it -- both dicts hold identical bytes on
every path -- and it cannot carry an escalation in either direction, because
the default is ``True``, so setting it can only no-op or restrict. The
escalation-bearing half of git is ``SAFE_GIT_OPERATIONS``, which *is* gated.

So this file pins two things: the invariant is now literally true, and the
reasoning that makes the exclusion safe still holds.
"""

import ast
from pathlib import Path

import pytest

from app.config.scope_canonical import ESCALATION_ENV_KEYS, strip_escalations
from app.config.shell_config import DEFAULT_SHELL_CONFIG

SHELL_SERVER = (
    Path(__file__).resolve().parents[1] / "app" / "mcp_servers" / "shell_server.py"
)

# Values that decide what the shell server will run or write. Everything else
# (timeouts, output caps, view prefs) is not privilege-bearing and may be read
# from os.environ freely.
PRIVILEGE_KEYS = {
    "ALLOW_COMMANDS",
    "SAFE_WRITE_PATHS",
    "ALLOWED_WRITE_PATTERNS",
    "ALLOWED_INTERPRETERS",
    "SAFE_GIT_OPERATIONS",
    "YOLO_MODE",
    "GIT_OPERATIONS_ENABLED",
}


def _env_reads(tree, receiver: str):
    """Env keys read via ``<receiver>.get("KEY")`` in *tree*.

    ``receiver`` is matched on the source text of the call's object so both
    ``os.environ`` and ``self._scope_env`` can be located the same way.
    """
    found = set()
    for node in ast.walk(tree):
        if not (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "get"
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, str)
        ):
            continue
        if ast.unparse(node.func.value) == receiver:
            found.add(node.args[0].value)
    return found


@pytest.fixture(scope="module")
def tree():
    return ast.parse(SHELL_SERVER.read_text())


class TestNoPrivilegeReadsFromOsEnviron:
    def test_no_privilege_key_read_from_os_environ(self, tree):
        leaked = _env_reads(tree, "os.environ") & PRIVILEGE_KEYS
        assert not leaked, (
            "shell_server reads privilege-bearing env directly from "
            "os.environ, bypassing the escalation-integrity gate's clamped "
            "view (ASR Part 0): " + ", ".join(sorted(leaked))
        )

    def test_git_operations_enabled_is_read_from_scope_env(self, tree):
        """Positive control: the value is still read, from the right place.

        Deleting the read entirely would satisfy the assertion above.
        """
        assert "GIT_OPERATIONS_ENABLED" in _env_reads(tree, "self._scope_env")

    def test_the_gated_git_field_is_also_scope_env(self, tree):
        """SAFE_GIT_OPERATIONS is the escalation-bearing half of git config and
        must come from the clamped view."""
        assert "SAFE_GIT_OPERATIONS" in _env_reads(tree, "self._scope_env")

    def test_non_privilege_env_reads_are_not_restricted(self, tree):
        """The invariant is about privilege state, not about banning os.environ.
        If this ever comes back empty the scanner has stopped working rather
        than the code having become perfect.
        """
        assert _env_reads(tree, "os.environ") - PRIVILEGE_KEYS


class TestExclusionFromTheGateIsSafe:
    """Why ``GIT_OPERATIONS_ENABLED`` needs no signature.

    Same category as ``ALWAYS_BLOCKED_COMMANDS``: additions only restrict. If
    any of these properties changes, the exclusion stops being safe and this
    file is where that surfaces.
    """

    def test_not_in_the_gated_key_set(self):
        assert "GIT_OPERATIONS_ENABLED" not in ESCALATION_ENV_KEYS

    def test_strip_escalations_leaves_it_untouched(self):
        """Because it is ungated, the clamped and raw views hold identical
        bytes -- which is what made the original inconsistency harmless."""
        for value in ("true", "false", "1", "garbage"):
            env = {"GIT_OPERATIONS_ENABLED": value, "ALLOW_COMMANDS": "rm,curl"}
            assert strip_escalations(env)["GIT_OPERATIONS_ENABLED"] == value

    def test_strip_escalations_does_clamp_the_gated_neighbour(self):
        """Paired control: the function under test really is the clamping one,
        so the pass-through above is a deliberate exclusion and not a no-op."""
        env = {"SAFE_GIT_OPERATIONS": "status,push,reset"}
        kept = strip_escalations(env)["SAFE_GIT_OPERATIONS"].split(",")
        assert "status" in kept
        assert "push" not in kept

    def test_default_is_enabled_so_setting_it_cannot_escalate(self):
        """The load-bearing fact: with the default already True, a supplied
        value can only be a no-op or a restriction."""
        assert DEFAULT_SHELL_CONFIG["gitOperationsEnabled"] is True
