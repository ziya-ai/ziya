"""
Tests for app.config.write_policy — security-critical write gating.

Covers:
  - Default safe paths (.ziya/, /tmp/, /var/tmp/, /dev/null)
  - Path traversal rejection
  - allowed_write_patterns glob matching
  - Config cascade: global → project overrides
  - merge_env_overrides from shell subprocess
  - Edge cases: empty paths, quoted paths, relative vs absolute
"""

import json
import os
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from app.config.write_policy import WritePolicyManager, DEFAULT_WRITE_POLICY, get_write_policy_manager


@pytest.fixture
def pm(tmp_path):
    """Fresh WritePolicyManager with a temp project root."""
    mgr = WritePolicyManager()
    mgr._project_root = str(tmp_path)
    return mgr


@pytest.fixture
def project_root(tmp_path):
    return str(tmp_path)


# ── Default safe paths ─────────────────────────────────────────────

class TestDefaultSafePaths:

    def test_ziya_dir_allowed(self, pm, project_root):
        assert pm.is_write_allowed(".ziya/notes.md", project_root)

    def test_ziya_nested_allowed(self, pm, project_root):
        assert pm.is_write_allowed(".ziya/state/progress.json", project_root)

    def test_tmp_absolute_allowed(self, pm, project_root):
        assert pm.is_write_allowed("/tmp/scratch.txt", project_root)

    def test_var_tmp_absolute_allowed(self, pm, project_root):
        assert pm.is_write_allowed("/var/tmp/output.log", project_root)

    def test_dev_null_allowed(self, pm, project_root):
        assert pm.is_write_allowed("/dev/null", project_root)

    def test_project_source_blocked(self, pm, project_root):
        assert not pm.is_write_allowed("src/main.py", project_root)

    def test_project_root_file_blocked(self, pm, project_root):
        assert not pm.is_write_allowed("README.md", project_root)


# ── Path traversal ─────────────────────────────────────────────────

class TestPathTraversal:

    def test_dotdot_blocked(self, pm, project_root):
        assert not pm.is_write_allowed("../../../etc/passwd", project_root)

    def test_dotdot_in_middle_blocked(self, pm, project_root):
        assert not pm.is_write_allowed("src/../../../etc/shadow", project_root)

    def test_dotdot_to_home_blocked(self, pm, project_root):
        assert not pm.is_write_allowed("../../.ssh/authorized_keys", project_root)


# ── Quoted / whitespace paths ──────────────────────────────────────

class TestPathCleaning:

    def test_quoted_path_stripped(self, pm, project_root):
        assert pm.is_write_allowed("'.ziya/test.txt'", project_root)

    def test_double_quoted_path_stripped(self, pm, project_root):
        assert pm.is_write_allowed('".ziya/test.txt"', project_root)

    def test_whitespace_stripped(self, pm, project_root):
        assert pm.is_write_allowed("  .ziya/test.txt  ", project_root)

    def test_empty_path_blocked(self, pm, project_root):
        assert not pm.is_write_allowed("", project_root)

    def test_whitespace_only_blocked(self, pm, project_root):
        assert not pm.is_write_allowed("   ", project_root)


# ── allowed_write_patterns ─────────────────────────────────────────

class TestAllowedPatterns:

    def test_glob_star_md(self, pm, project_root):
        pm._policy["allowed_write_patterns"] = ["*.md"]
        assert pm.is_write_allowed("design/notes.md", project_root)

    def test_glob_no_match(self, pm, project_root):
        pm._policy["allowed_write_patterns"] = ["*.md"]
        assert not pm.is_write_allowed("src/main.py", project_root)

    def test_glob_nested_pattern(self, pm, project_root):
        pm._policy["allowed_write_patterns"] = ["design/*.md"]
        assert pm.is_write_allowed("design/architecture.md", project_root)

    def test_glob_basename_match(self, pm, project_root):
        """Patterns should match basename even for nested paths."""
        pm._policy["allowed_write_patterns"] = ["*.txt"]
        assert pm.is_write_allowed("deep/nested/file.txt", project_root)

    def test_comma_separated_patterns(self, pm, project_root):
        """Frontend stores comma-separated patterns as a single entry."""
        pm._policy["allowed_write_patterns"] = ["*.txt,*.md"]
        assert pm.is_write_allowed("notes.md", project_root)
        assert pm.is_write_allowed("data.txt", project_root)
        assert not pm.is_write_allowed("main.py", project_root)


# ── check_write returns reason ─────────────────────────────────────

class TestCheckWrite:

    def test_allowed_returns_empty_reason(self, pm, project_root):
        ok, reason = pm.check_write(".ziya/test.txt", project_root)
        assert ok
        assert reason == ""

    def test_blocked_returns_reason_with_paths(self, pm, project_root):
        ok, reason = pm.check_write("src/evil.py", project_root)
        assert not ok
        assert ".ziya/" in reason
        assert "/tmp/" in reason

    def test_blocked_reason_includes_patterns_if_set(self, pm, project_root):
        pm._policy["allowed_write_patterns"] = ["design/*.md"]
        ok, reason = pm.check_write("src/evil.py", project_root)
        assert not ok
        assert "design/*.md" in reason


# ── Config cascade: global + project overrides ─────────────────────

class TestConfigCascade:

    def test_load_global_overrides(self, pm, tmp_path):
        """Global override in ~/.ziya/write_policy.json adds patterns."""
        ziya_home = tmp_path / ".ziya_test_home"
        ziya_home.mkdir()
        policy_file = ziya_home / "write_policy.json"
        policy_file.write_text(json.dumps({
            "allowed_write_patterns": ["docs/*.rst"]
        }))

        with patch("pathlib.Path.home", return_value=tmp_path):
            # Rename so it matches the path the code looks for
            actual_ziya = tmp_path / ".ziya"
            actual_ziya.mkdir(exist_ok=True)
            (actual_ziya / "write_policy.json").write_text(json.dumps({
                "allowed_write_patterns": ["docs/*.rst"]
            }))
            pm.load_for_project("test-proj", str(tmp_path / "project"))

        assert "docs/*.rst" in pm._policy["allowed_write_patterns"]

    def test_project_overrides_extend(self, pm, tmp_path):
        """Per-project settings.writePolicy extends the defaults."""
        project_id = "proj-123"

        # Set up project config
        with patch("pathlib.Path.home", return_value=tmp_path):
            proj_dir = tmp_path / ".ziya" / "projects" / project_id
            proj_dir.mkdir(parents=True)
            (proj_dir / "project.json").write_text(json.dumps({
                "id": project_id,
                "path": "/some/project",
                "settings": {
                    "writePolicy": {
                        "allowed_write_patterns": ["generated/*.py"]
                    }
                }
            }))

            pm.load_for_project(project_id, "/some/project")

        assert "generated/*.py" in pm._policy["allowed_write_patterns"]
        # Defaults should still be present
        assert ".ziya/" in pm._policy["safe_write_paths"]


# ── merge_env_overrides ────────────────────────────────────────────

class TestEnvOverrides:

    def test_safe_write_paths_from_env(self, pm):
        pm.merge_env_overrides({
            "SAFE_WRITE_PATHS": "/custom/path/,/another/"
        })
        assert "/custom/path/" in pm._policy["safe_write_paths"]
        assert "/another/" in pm._policy["safe_write_paths"]

    def test_allowed_write_patterns_from_env(self, pm):
        pm.merge_env_overrides({
            "ALLOWED_WRITE_PATTERNS": "*.log,*.tmp"
        })
        assert "*.log" in pm._policy["allowed_write_patterns"]
        assert "*.tmp" in pm._policy["allowed_write_patterns"]

    def test_empty_env_no_change(self, pm):
        before = pm._policy.copy()
        pm.merge_env_overrides({})
        assert pm._policy["safe_write_paths"] == before["safe_write_paths"]

    def test_always_blocked_from_env(self, pm):
        pm.merge_env_overrides({
            "ALWAYS_BLOCKED_COMMANDS": "custom_danger"
        })
        assert "custom_danger" in pm._policy["always_blocked"]


# ── _merge deduplication ───────────────────────────────────────────

class TestMerge:

    def test_list_merge_deduplicates(self, pm):
        original_count = len(pm._policy["safe_write_paths"])
        pm._merge({"safe_write_paths": [".ziya/"]})  # Already present
        assert len(pm._policy["safe_write_paths"]) == original_count

    def test_dict_merge_updates(self, pm):
        pm._merge({"inplace_edit_flags": {"newprog": ["--inplace"]}})
        assert "newprog" in pm._policy["inplace_edit_flags"]
        # Original entries preserved
        assert "sed" in pm._policy["inplace_edit_flags"]


# ── Singleton ──────────────────────────────────────────────────────

class TestSingleton:

    def test_get_write_policy_manager_returns_same_instance(self):
        a = get_write_policy_manager()
        b = get_write_policy_manager()
        assert a is b

    def test_get_effective_policy_returns_copy(self, pm):
        effective = pm.get_effective_policy()
        effective["safe_write_paths"].append("/hacked/")
        assert "/hacked/" not in pm._policy["safe_write_paths"]
