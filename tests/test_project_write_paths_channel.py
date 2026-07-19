"""
Tests for the ZIYA_PROJECT_WRITE_PATHS channel that unconflates file_write
and shell write policy.

Background: the shell subprocess cannot read the ALE-encrypted project.json
(no KEK), so it historically fell back to the floor (.ziya/, /tmp/, ...) and
refused shell writes (cp/sed/>) to project-policy paths that file_write —
running in the trusted parent, which CAN decrypt — already allowed. The fix
forwards the decrypted project writePolicy to the shell through a dedicated,
NON-gated env channel so both enforcers agree.

Three properties are covered:
  1. scope_canonical: the channel is non-gated (needs no root signature and
     survives strip_escalations) — otherwise the injected paths would be
     clamped back to the floor at subprocess boot.
  2. WritePolicyManager.merge_env_overrides: honors the channel, splitting
     directory entries into safe_write_paths and glob entries into
     allowed_write_patterns.
  3. MCPManager._apply_project_write_paths: forgery resistance (always
     set-or-delete, never trust inbound), shell-only, and multi-project
     isolation (uses a throwaway manager, never the shared singleton).
"""

import os
from unittest.mock import patch

import pytest

from app.config import scope_canonical as sc
from app.config.write_policy import WritePolicyManager, get_write_policy_manager
from app.mcp.manager import MCPManager

KEY = sc.PROJECT_WRITE_PATHS_ENV_KEY


# ── 1. Channel is non-gated in scope_canonical ─────────────────────

class TestChannelIsNonGated:

    def test_key_value(self):
        assert KEY == "ZIYA_PROJECT_WRITE_PATHS"

    def test_not_in_list_fields(self):
        # If it were gated, a value beyond the floor would need a signature.
        assert KEY not in sc._LIST_FIELDS

    def test_not_in_escalation_env_keys(self):
        assert KEY not in sc.ESCALATION_ENV_KEYS

    def test_survives_strip_escalations(self):
        # strip_escalations runs when signature verification fails; the channel
        # must pass through untouched so unsigned project paths still apply.
        env = {KEY: "tests/,Docs/,*.md"}
        assert sc.strip_escalations(env).get(KEY) == "tests/,Docs/,*.md"

    def test_channel_alone_is_authorized_without_signature(self):
        # Only the non-gated channel present -> no escalation delta -> True,
        # even with no ZIYA_SCOPE_SIG.
        env = {KEY: "tests/,Docs/,scripts/,*.md"}
        assert sc.is_env_scope_authorized(env) is True

    def test_channel_does_not_launder_gated_escalation(self):
        # The channel must not somehow authorize a genuine gated escalation
        # (e.g. an unsigned SAFE_WRITE_PATHS beyond the floor stays refused).
        env = {KEY: "tests/", "SAFE_WRITE_PATHS": ".ziya/,/etc/"}
        assert sc.is_env_scope_authorized(env) is False


# ── 2. merge_env_overrides honors the channel ──────────────────────

class TestMergeEnvProjectPaths:

    @pytest.fixture
    def pm(self, tmp_path):
        mgr = WritePolicyManager()
        mgr._project_root = str(tmp_path)
        return mgr

    def test_dir_entries_go_to_safe_write_paths(self, pm):
        pm.merge_env_overrides({KEY: "tests/,Docs/,scripts/"})
        for p in ("tests/", "Docs/", "scripts/"):
            assert p in pm._policy["safe_write_paths"]

    def test_glob_entries_go_to_patterns(self, pm):
        pm.merge_env_overrides({KEY: "*.md,*/__tests__/**,foo?.txt,a[bc].py"})
        for pat in ("*.md", "*/__tests__/**", "foo?.txt", "a[bc].py"):
            assert pat in pm._policy["allowed_write_patterns"]

    def test_mixed_split(self, pm):
        pm.merge_env_overrides({KEY: "tests/,*.md,Docs/,*/__tests__/**"})
        assert "tests/" in pm._policy["safe_write_paths"]
        assert "Docs/" in pm._policy["safe_write_paths"]
        assert "*.md" in pm._policy["allowed_write_patterns"]
        assert "*/__tests__/**" in pm._policy["allowed_write_patterns"]

    def test_end_to_end_shell_write_now_allowed(self, pm):
        # The behavioral payoff: after the channel merges, is_write_allowed
        # (the shell enforcer's check) permits project-policy paths.
        root = pm._project_root
        pm.merge_env_overrides({KEY: "tests/,scripts/,*.md,*/__tests__/**"})
        assert pm.is_write_allowed("tests/test_x.py", root)
        assert pm.is_write_allowed("scripts/run.sh", root)
        assert pm.is_write_allowed("notes.md", root)
        assert pm.is_write_allowed("app/__tests__/x.py", root)
        # Paths NOT in the project policy stay blocked.
        assert not pm.is_write_allowed("src/main.py", root)

    def test_floor_still_applies_alongside_channel(self, pm):
        root = pm._project_root
        pm.merge_env_overrides({KEY: "tests/"})
        assert pm.is_write_allowed(".ziya/state.json", root)  # floor intact
        assert pm.is_write_allowed("/tmp/scratch", root)

    def test_empty_channel_no_change(self, pm):
        before = list(pm._policy["safe_write_paths"])
        pm.merge_env_overrides({KEY: ""})
        assert pm._policy["safe_write_paths"] == before

    def test_absent_channel_no_change(self, pm):
        before = list(pm._policy["safe_write_paths"])
        pm.merge_env_overrides({})
        assert pm._policy["safe_write_paths"] == before

    def test_whitespace_and_blank_entries_ignored(self, pm):
        pm.merge_env_overrides({KEY: " tests/ , , Docs/ ,  "})
        assert "tests/" in pm._policy["safe_write_paths"]
        assert "Docs/" in pm._policy["safe_write_paths"]
        assert "" not in pm._policy["safe_write_paths"]


# ── 3. Manager injection: forgery resistance, shell-only, isolation ─

class _FakePM:
    """Stand-in for a throwaway WritePolicyManager used by the injector."""
    def __init__(self, policy):
        self._policy = policy
        self.loaded_root = None

    def _ensure_loaded_for_root(self, root):
        self.loaded_root = root

    def get_effective_policy(self):
        return self._policy


class TestManagerInjection:
    """_apply_project_write_paths references no self attributes, so we can
    invoke it unbound with any object as ``self``."""

    _PROJECT_POLICY = {
        "safe_write_paths": [".ziya/", "/tmp/", "/var/tmp/", "/dev/null",
                             "tests/", "Docs/", "scripts/"],
        "allowed_write_patterns": ["*.md", "*/__tests__/**"],
    }

    def _inject(self, env, server_name, policy=None):
        fake = _FakePM(policy if policy is not None else self._PROJECT_POLICY)
        with patch("app.config.write_policy.WritePolicyManager", return_value=fake):
            MCPManager._apply_project_write_paths(object(), env, server_name)
        return fake

    def test_shell_gets_project_paths(self):
        env = {"ZIYA_USER_CODEBASE_DIR": "/proj"}
        self._inject(env, "shell")
        vals = env[KEY].split(",")
        assert "tests/" in vals and "Docs/" in vals and "scripts/" in vals
        assert "*.md" in vals and "*/__tests__/**" in vals

    def test_non_shell_server_never_gets_key(self):
        env = {"ZIYA_USER_CODEBASE_DIR": "/proj"}
        self._inject(env, "time")
        assert KEY not in env

    def test_forged_inbound_value_is_cleared_for_non_shell(self):
        # A value hand-written into mcp_config.json for a non-shell server
        # must be removed, never forwarded.
        env = {"ZIYA_USER_CODEBASE_DIR": "/proj", KEY: "/etc/,/"}
        self._inject(env, "someserver")
        assert KEY not in env

    def test_forged_inbound_value_is_overwritten_for_shell(self):
        # For the shell, a forged inbound value must be replaced wholesale by
        # the parent-resolved policy — never merged/preserved.
        env = {"ZIYA_USER_CODEBASE_DIR": "/proj", KEY: "/etc/,/root/"}
        self._inject(env, "shell")
        assert "/etc/" not in env[KEY]
        assert "/root/" not in env[KEY]
        assert "tests/" in env[KEY]

    def test_no_project_root_clears_key(self):
        env = {KEY: "forged/"}
        # No ZIYA_USER_CODEBASE_DIR in env; also clear the real one.
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("ZIYA_USER_CODEBASE_DIR", None)
            self._inject(env, "shell")
        assert KEY not in env

    def test_empty_policy_leaves_no_key(self):
        env = {"ZIYA_USER_CODEBASE_DIR": "/proj"}
        self._inject(env, "shell", policy={"safe_write_paths": [],
                                           "allowed_write_patterns": []})
        assert KEY not in env

    def test_dedup_preserves_order(self):
        env = {"ZIYA_USER_CODEBASE_DIR": "/proj"}
        self._inject(env, "shell", policy={
            "safe_write_paths": ["tests/", "tests/", "Docs/"],
            "allowed_write_patterns": ["*.md", "*.md"],
        })
        assert env[KEY] == "tests/,Docs/,*.md"

    def test_uses_throwaway_not_shared_singleton(self):
        # The injector must resolve policy in a fresh WritePolicyManager()
        # instance — not by mutating the process-wide singleton other projects
        # rely on. We patch the class and assert it was instantiated (throwaway)
        # while the shared singleton's project root is left untouched.
        singleton = get_write_policy_manager()
        singleton._project_root = "/project-A"

        env = {"ZIYA_USER_CODEBASE_DIR": "/project-B"}
        fake = self._inject(env, "shell")

        # The throwaway was loaded for project B...
        assert fake.loaded_root == "/project-B"
        # ...and the shared singleton was NOT re-pointed to project B.
        assert singleton._project_root == "/project-A"

    def test_injection_never_raises_on_policy_error(self):
        # A decrypt/read failure must not block the spawn — the key is simply
        # left cleared.
        env = {"ZIYA_USER_CODEBASE_DIR": "/proj", KEY: "stale/"}

        class _Boom:
            def _ensure_loaded_for_root(self, r):
                raise RuntimeError("decrypt failed")
            def get_effective_policy(self):
                raise AssertionError("should not be reached")

        with patch("app.config.write_policy.WritePolicyManager", return_value=_Boom()):
            MCPManager._apply_project_write_paths(object(), env, "shell")
        assert KEY not in env
