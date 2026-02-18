"""
Tests for app.mcp_servers.write_policy — shell command write enforcement.

Covers:
  - Always-blocked commands (sudo, vim, etc.)
  - Destructive commands gated by path (rm, mv, cp, etc.)
  - In-place edit flags (sed -i, awk -i, perl -i)
  - Interpreter heuristics (python3 -c write detection)
  - Redirection to project files blocked, to /tmp allowed
  - Pipe chains with mixed safe/unsafe segments
"""

import pytest

from app.config.write_policy import WritePolicyManager
from app.mcp_servers.write_policy import ShellWriteChecker


# ── Fixtures ───────────────────────────────────────────────────────

@pytest.fixture
def project_root(tmp_path):
    proj = tmp_path / "project"
    proj.mkdir()
    (proj / ".ziya").mkdir()
    return str(proj)


@pytest.fixture
def policy_manager(project_root):
    pm = WritePolicyManager()
    pm.load_for_project("test-project", project_root)
    return pm


@pytest.fixture
def checker(policy_manager):
    return ShellWriteChecker(policy_manager)


# ── Helpers ────────────────────────────────────────────────────────

def _simple_split(cmd):
    return [(None, cmd)]

def _pipe_split(cmd):
    return [(None, seg.strip()) for seg in cmd.split('|')]


# ── Always blocked commands ────────────────────────────────────────

class TestAlwaysBlocked:
    @pytest.mark.parametrize("cmd", [
        "sudo rm -rf /",
        "su -",
        "vim file.py",
        "nano config.yaml",
        "emacs main.py",
        "systemctl restart nginx",
    ])
    def test_blocked_commands(self, checker, cmd):
        ok, reason = checker.check(cmd, _simple_split)
        assert not ok
        assert "never allowed" in reason.lower()


# ── Destructive commands (rm, mv, cp, mkdir, chmod) ────────────────

class TestDestructiveCommands:

    def test_rm_project_file_blocked(self, checker):
        ok, reason = checker.check("rm src/main.py", _simple_split)
        assert not ok
        assert "blocked" in reason.lower()

    def test_rm_tmp_allowed(self, checker):
        ok, reason = checker.check("rm /tmp/temp.txt", _simple_split)
        assert ok

    def test_mv_project_file_blocked(self, checker):
        ok, reason = checker.check("mv old.py new.py", _simple_split)
        assert not ok

    def test_cp_to_tmp_allowed(self, checker):
        ok, reason = checker.check("cp src/file.py /tmp/backup.py", _simple_split)
        assert ok

    def test_mkdir_project_blocked(self, checker):
        ok, reason = checker.check("mkdir new_dir", _simple_split)
        assert not ok

    def test_mkdir_ziya_allowed(self, checker):
        ok, reason = checker.check("mkdir .ziya/state", _simple_split)
        assert ok

    def test_chmod_blocked(self, checker):
        ok, reason = checker.check("chmod +x script.sh", _simple_split)
        assert not ok


# ── In-place edit flags ────────────────────────────────────────────

class TestInplaceEdits:

    def test_sed_i_blocked(self, checker):
        ok, reason = checker.check("sed -i 's/foo/bar/' file.txt", _simple_split)
        assert not ok
        assert "in-place" in reason.lower()

    def test_sed_in_place_blocked(self, checker):
        ok, reason = checker.check("sed --in-place 's/x/y/' data.txt", _simple_split)
        assert not ok

    def test_sed_without_i_allowed(self, checker):
        ok, reason = checker.check("sed 's/foo/bar/' file.txt", _simple_split)
        assert ok

    def test_awk_i_blocked(self, checker):
        ok, reason = checker.check("awk -i inplace '{print}' file.txt", _simple_split)
        assert not ok

    def test_perl_i_blocked(self, checker):
        ok, reason = checker.check("perl -i -pe 's/old/new/' file.pl", _simple_split)
        assert not ok

    def test_perl_pi_blocked(self, checker):
        ok, reason = checker.check("perl -pi 's/x/y/' script.pl", _simple_split)
        assert not ok


# ── Redirection operators ──────────────────────────────────────────

class TestRedirection:

    def test_redirect_to_project_file_blocked(self, checker):
        ok, reason = checker.check("echo 'data' > src/config.json", _simple_split)
        assert not ok
        assert "blocked" in reason.lower()

    def test_append_to_project_file_blocked(self, checker):
        ok, reason = checker.check("echo 'log' >> output.log", _simple_split)
        assert not ok

    def test_redirect_to_tmp_allowed(self, checker):
        ok, reason = checker.check("echo 'test' > /tmp/output.txt", _simple_split)
        assert ok

    def test_redirect_to_ziya_allowed(self, checker):
        ok, reason = checker.check("echo 'state' > .ziya/progress.txt", _simple_split)
        assert ok

    def test_redirect_in_quotes_not_triggered(self, checker):
        ok, reason = checker.check("echo 'literal > symbol' | cat", _simple_split)
        assert ok

    def test_stderr_redirect_to_project_blocked(self, checker):
        ok, reason = checker.check("command 2> error.log", _simple_split)
        assert not ok


# ── Interpreter safe patterns and write detection ──────────────────

class TestInterpreters:

    def test_python3_c_safe(self, checker):
        ok, reason = checker.check("python3 -c 'print(2+2)'", _simple_split)
        assert ok

    def test_python3_m_pytest_safe(self, checker):
        ok, reason = checker.check("python3 -m pytest tests/", _simple_split)
        assert ok

    def test_python3_script_with_write_via_redirect(self, checker):
        ok, reason = checker.check(
            "python3 -c 'print(\"data\")' > result.txt",
            _simple_split
        )
        assert not ok
        assert "redirection" in reason.lower() or "blocked" in reason.lower()

    def test_python3_script_not_matching_safe_pattern(self, checker):
        """A python3 invocation that doesn't match any safe pattern
        should have write indicators checked."""
        ok, reason = checker.check(
            "python3 script_that_writes.py",
            _simple_split
        )
        # script_that_writes.py doesn't match any safe_pattern (-c, -m pytest, etc.)
        # and doesn't contain write indicators in the command string itself.
        # The check passes because the script name alone isn't flagged.
        assert ok

    def test_python3_inline_with_shutil_not_safe_pattern(self, checker):
        """python3 -c matches a safe pattern, so script_write_indicators
        are NOT checked. This is by design: -c is considered safe."""
        ok, _ = checker.check(
            "python3 -c 'import shutil; shutil.rmtree(\"src\")'",
            _simple_split
        )
        # -c matches interpreter_safe_patterns → skips write indicator check
        # Only redirection is checked, which passes here.
        assert ok

    def test_python3_with_os_remove_redirect_blocked(self, checker):
        """Even safe-pattern python3 is blocked if it redirects to project files."""
        ok, reason = checker.check(
            "python3 -c 'import os; os.remove(\"file.py\")' > result.txt",
            _simple_split
        )
        assert not ok


# ── NEW: Obvious write operations in -c scripts should be blocked ──

class TestObviousInlineWrites:
    """
    While safe_pattern matches prevent full write-indicator checks,
    we should still catch obvious file writes in -c commands.
    """

    def test_python3_c_with_obvious_open_write_blocked(self, checker):
        """Obvious file write via open(..., 'w') in -c command."""
        ok, reason = checker.check(
            'python3 -c \'open("hack.py", "w").write("bad")\'',
            _simple_split
        )
        assert not ok
        assert "write" in reason.lower() or "file" in reason.lower()

    def test_python3_c_with_pathlib_write_blocked(self, checker):
        """pathlib write operation in -c command."""
        ok, reason = checker.check(
            'python3 -c \'from pathlib import Path; Path("x").write_text("y")\'',
            _simple_split
        )
        assert not ok
        assert "write" in reason.lower() or "file" in reason.lower()

    def test_python3_c_with_shutil_copy_blocked(self, checker):
        """shutil.copy in -c command."""
        ok, reason = checker.check(
            'python3 -c \'import shutil; shutil.copy("a.py", "b.py")\'',
            _simple_split
        )
        assert not ok
        assert "write" in reason.lower() or "file" in reason.lower()

    def test_python3_c_with_os_rename_blocked(self, checker):
        """os.rename in -c command."""
        ok, reason = checker.check(
            'python3 -c \'import os; os.rename("old", "new")\'',
            _simple_split
        )
        assert not ok
        assert "write" in reason.lower() or "file" in reason.lower()

    def test_python3_c_with_subprocess_rm_blocked(self, checker):
        """subprocess call with destructive command."""
        ok, reason = checker.check(
            'python3 -c \'import subprocess; subprocess.run(["rm", "file.py"])\'',
            _simple_split
        )
        assert not ok

    def test_python3_c_with_open_read_allowed(self, checker):
        """Read-only open() is safe."""
        ok, reason = checker.check(
            'python3 -c \'print(open("file.py").read())\'',
            _simple_split
        )
        assert ok

    def test_python3_c_with_open_r_explicit_allowed(self, checker):
        """Explicit open(..., 'r') is safe."""
        ok, reason = checker.check(
            'python3 -c \'print(open("file.py", "r").read())\'',
            _simple_split
        )
        assert ok


# ── Read-only commands pass through ────────────────────────────────

class TestReadOnlyAllowed:

    @pytest.mark.parametrize("cmd", [
        "ls -la",
        "cat src/main.py",
        "grep -r 'TODO' .",
        "find . -name '*.py'",
        "wc -l src/*.py",
        "head -20 README.md",
        "tail -f /tmp/log.txt",
        "git status",
        "git log --oneline",
        "git diff HEAD~1",
        "uname -a",
        "whoami",
        "pwd",
    ])
    def test_read_only_commands_allowed(self, checker, cmd):
        ok, reason = checker.check(cmd, _simple_split)
        assert ok, f"Command should be allowed: {cmd} (reason: {reason})"


# ── Pipe chains ────────────────────────────────────────────────────

class TestPipeChains:

    def test_safe_pipe_chain(self, checker):
        ok, reason = checker.check("cat file.py | grep TODO | wc -l", _pipe_split)
        assert ok

    def test_pipe_to_destructive_blocked(self, checker):
        ok, reason = checker.check("echo data | sudo tee /etc/hosts", _pipe_split)
        assert not ok
