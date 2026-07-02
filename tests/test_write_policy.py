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


# ── Symlink resolution for safe paths (macOS /tmp → /private/tmp) ──

class TestSymlinkResolution:

    def test_resolved_tmp_path_allowed(self, pm, project_root):
        """On macOS, /tmp resolves to /private/tmp. Both must be allowed."""
        resolved = os.path.realpath("/tmp")
        assert pm.is_write_allowed(f"{resolved}/scratch.txt", project_root)

    def test_private_tmp_allowed_explicitly(self, pm, project_root):
        """Even if /private/tmp is passed directly, it should match /tmp/ safe path."""
        assert pm.is_write_allowed("/private/tmp/test.py", project_root)

    def test_resolved_var_tmp_allowed(self, pm, project_root):
        resolved = os.path.realpath("/var/tmp")
        assert pm.is_write_allowed(f"{resolved}/output.log", project_root)


# ── PenPal #55 (CWE-22): normalize before containment checks ───────
#
# _check_path previously compared raw, un-normalized strings against
# safe-path entries. A traversal sequence that starts with an allowed
# prefix (e.g. ".ziya/../../../etc/cron.d/x") string-prefix-matched
# ".ziya/" while resolving OUTSIDE project_root once a shell or open()
# call later collapsed the "..". These tests target the exact bypass
# shape from the report, not just outcomes that also happen to be
# blocked by other logic.

class TestNormalizedContainment:

    def test_safe_path_traversal_via_relative_prefix_blocked(self, pm, project_root):
        """'.ziya/../../../etc/cron.d/x' starts with the safe '.ziya/'
        prefix as a raw string, but normalizes to a path far outside
        project_root. Must be blocked."""
        assert not pm.is_write_allowed(".ziya/../../../etc/cron.d/x", project_root)

    def test_safe_path_traversal_single_level_blocked(self, pm, project_root):
        """A single '..' that still resolves outside .ziya/ must also
        be blocked (not just deep traversals)."""
        assert not pm.is_write_allowed(".ziya/../outside.txt", project_root)

    def test_safe_path_traversal_resolving_back_inside_allowed(self, pm, project_root):
        """A traversal that resolves back to a location still inside
        .ziya/ is legitimately safe and must remain allowed — the fix
        must not be overly strict."""
        assert pm.is_write_allowed(".ziya/sub/../notes.md", project_root)

    def test_absolute_safe_path_traversal_blocked(self, pm, project_root):
        """Same bypass shape against an absolute safe_write_paths entry
        (/tmp/) rather than a project-relative one."""
        assert not pm.is_write_allowed("/tmp/../etc/passwd", project_root)

    def test_sibling_directory_prefix_not_treated_as_safe(self, pm, project_root):
        """A path under a sibling directory whose name is a prefix of an
        allowed absolute path (e.g. '/tmp-evil/x' vs safe '/tmp/') must
        not be treated as inside the safe path."""
        assert not pm.is_write_allowed("/tmp-evil/x", project_root)

    def test_project_relative_sibling_prefix_not_matched(self, pm, project_root):
        """A project-relative safe entry (e.g. 'design/') must require an
        os.sep boundary — 'design-secrets/x' must not match 'design/'."""
        pm._policy["safe_write_paths"] = [".ziya/", "design/"]
        assert not pm.is_write_allowed("design-secrets/x", project_root)
        assert pm.is_write_allowed("design/notes.md", project_root)


class TestIsWithinProjectSiblingBypass:
    """_is_within_project backs direct_write_mode's project-containment
    check. A sibling directory whose name is a prefix of project_root
    (e.g. '/proj-backup' vs root '/proj') must not be treated as
    'within' the project — this is the CVE-class the fix closed."""

    def test_sibling_prefix_directory_not_within_project(self, pm, project_root):
        sibling = project_root + "-backup"
        assert not pm._is_within_project(f"{sibling}/secret.txt", project_root)

    def test_actual_subdirectory_is_within_project(self, pm, project_root):
        assert pm._is_within_project("src/main.py", project_root)

    def test_project_root_itself_is_within_project(self, pm, project_root):
        assert pm._is_within_project(".", project_root)

    def test_traversal_out_of_project_not_within(self, pm, project_root):
        assert not pm._is_within_project("../../etc/passwd", project_root)

    def test_direct_write_mode_all_files_respects_sibling_fix(self, pm, project_root):
        """End-to-end: with direct_write_mode='all_files', a sibling dir
        that string-prefix-matches project_root must still be rejected
        by is_direct_write_allowed."""
        pm._policy["direct_write_mode"] = "all_files"
        sibling = project_root + "-backup"
        allowed, _ = pm.is_direct_write_allowed(f"{sibling}/secret.txt", project_root)
        assert not allowed

    def test_direct_write_mode_all_files_allows_real_subpath(self, pm, project_root):
        pm._policy["direct_write_mode"] = "all_files"
        allowed, _ = pm.is_direct_write_allowed("src/new_file.py", project_root)
        assert allowed

