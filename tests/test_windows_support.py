"""Windows support: file locks, line endings, process launching, the shell tool.

Most of these run on every platform.  Behavior that exists only on Windows
is skipped elsewhere.
"""
import io
import os
import shutil
import subprocess
import sys
import time
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from app.mcp.enhanced_tools import execute_context_request
from app.mcp.registry.installation_helper import InstallationHelper
from app.mcp_servers import shell_server
from app.services import folder_service
from app.utils.diff_utils.file_ops.file_handlers import (
    preserved_line_endings,
    write_preserving_line_endings,
)
from app.utils.diff_utils.pipeline.pipeline_manager import apply_diff_pipeline
from app.utils.diff_utils.pipeline.reverse_pipeline import apply_reverse_diff_pipeline
from app.utils.file_locking import lock_exclusive, unlock
from app.utils.process_utils import configure_stdio, resolve_command, resolve_executable

windows_only = pytest.mark.skipif(sys.platform != "win32", reason="Windows-only behavior")
needs_git = pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")


# -- file locking -------------------------------------------------------------

def test_lock_is_exclusive_and_leaves_contents_readable(tmp_path):
    path = tmp_path / "state.json"
    path.write_text('{"a": 1}')
    with open(path, "r+") as first, open(path, "r+") as second:
        first.seek(3)
        assert lock_exclusive(first)
        assert first.tell() == 3
        assert not lock_exclusive(second, blocking=False)
        # Windows byte-range locks are mandatory; the lock byte lies past EOF
        assert path.read_text() == '{"a": 1}'
        unlock(first)
        assert lock_exclusive(second, blocking=False)
        unlock(second)


# -- process launching --------------------------------------------------------

def test_configure_stdio_prevents_emoji_crash_on_legacy_codepage(monkeypatch):
    raw = io.BytesIO()
    stdout = io.TextIOWrapper(raw, encoding="cp1252", errors="strict")
    monkeypatch.setattr(sys, "stdout", stdout)

    configure_stdio()
    print("\u2705 Folder scan completed")
    stdout.flush()

    assert raw.getvalue().startswith(rb"\u2705 Folder scan completed")


@pytest.fixture
def windows_pathext(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setenv("PATHEXT", ".EXE;.CMD")


def test_resolve_command_finds_cmd_shims_on_windows(tmp_path, windows_pathext):
    bin_dir = tmp_path / "nodejs"
    bin_dir.mkdir()
    (bin_dir / "npx.CMD").write_text("")
    npx = str(bin_dir / "npx.CMD")
    assert resolve_command(["npx", "-y", "server"], path=str(bin_dir)) == [npx, "-y", "server"]
    assert resolve_executable("npx.CMD", path=str(bin_dir)) == npx


def test_resolve_executable_ignores_the_current_directory(tmp_path, monkeypatch, windows_pathext):
    """shutil.which would run a repository's own npx.cmd from the project root."""
    project = tmp_path / "project"
    project.mkdir()
    (project / "npx.CMD").write_text("")
    monkeypatch.chdir(project)
    assert resolve_executable("npx", path=os.pathsep.join([".", str(tmp_path / "empty")])) == "npx"


def test_resolve_executable_is_a_no_op_off_windows(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    (tmp_path / "npx").write_text("")
    assert resolve_executable("npx", path=str(tmp_path)) == "npx"


def test_resolve_executable_keeps_explicit_paths(windows_pathext):
    explicit = os.path.join("tools", "server.exe")
    assert resolve_executable(explicit) == explicit


def test_mcp_npm_install_uses_resolved_npm(tmp_path):
    npm = r"C:\Program Files\nodejs\npm.CMD"
    completed = SimpleNamespace(returncode=0, stdout="", stderr="")
    with (
        patch("app.mcp.registry.installation_helper.resolve_executable", return_value=npm),
        patch("app.mcp.registry.installation_helper.subprocess.run", return_value=completed) as run,
    ):
        InstallationHelper.install_npm_package("example-mcp", tmp_path)
    assert run.call_args.args[0] == [npm, "install", "example-mcp"]


def test_frontend_build_uses_resolved_npm():
    from app import ziya_exec

    npm = r"C:\Program Files\nodejs\npm.CMD"
    with (
        patch("app.utils.process_utils.resolve_executable", return_value=npm),
        patch("app.ziya_exec.subprocess.run") as run,
        patch.object(sys, "argv", ["fbuild"]),
    ):
        ziya_exec.frontend_build()
    run.assert_called_once_with([npm, "run", "build"], cwd="frontend", env=None)


def test_cli_builds_prompt_session_only_when_used():
    """One-shot commands must not need a console (Windows prompt_toolkit output does)."""
    from app.cli import CLI

    with patch.object(CLI, "_setup_prompt_session") as setup:
        cli = CLI(files=[])
        setup.assert_not_called()
        cli.session
        setup.assert_called_once()


# -- diff apply / undo --------------------------------------------------------

@pytest.mark.parametrize("newline", ["\n", "\r\n"])
def test_write_preserves_existing_line_endings(tmp_path, newline):
    target = tmp_path / "example.py"
    target.write_bytes(f"a = 1{newline}b = 2{newline}".encode())

    write_preserving_line_endings(str(target), "a = 1\nb = 3\n")

    assert target.read_bytes() == f"a = 1{newline}b = 3{newline}".encode()


@pytest.mark.parametrize("newline", ["\n", "\r\n"])
def test_preserved_line_endings_repairs_external_writes(tmp_path, newline):
    target = tmp_path / "example.py"
    target.write_bytes(f"a = 1{newline}b = 2{newline}".encode())
    with preserved_line_endings(str(target)):
        # What git apply does to an LF file under core.autocrlf=true
        target.write_bytes(f"a = 1{newline}b = 2{newline}c = 3\r\n".encode())
    assert target.read_bytes() == f"a = 1{newline}b = 2{newline}c = 3{newline}".encode()


@pytest.fixture
def without_patch_binary():
    """Hide GNU patch, as on a stock Windows install."""
    real_which = shutil.which
    with patch("shutil.which",
               side_effect=lambda cmd, *a, **kw: None if cmd == "patch" else real_which(cmd, *a, **kw)):
        yield


DIFF = (
    "--- a/example.py\n"
    "+++ b/example.py\n"
    "@@ -1,2 +1,3 @@\n"
    " def f():\n"
    "-    return 1\n"
    "+    # caf\u00e9\n"
    "+    return 2\n"
)


@needs_git
@pytest.mark.parametrize("newline", ["\n", "\r\n"])
def test_apply_and_undo_without_patch_binary(tmp_path, monkeypatch, newline, without_patch_binary):
    project = tmp_path / "project"
    project.mkdir()
    target = project / "example.py"
    original = f"def f():{newline}    return 1{newline}".encode()
    target.write_bytes(original)
    monkeypatch.setenv("ZIYA_USER_CODEBASE_DIR", str(project))

    result = apply_diff_pipeline(DIFF, str(target))

    assert result["status"] == "success"
    assert result["hunk_statuses"]["1"]["stage"] == "git_apply"
    expected = f"def f():{newline}    # caf\u00e9{newline}    return 2{newline}"
    assert target.read_bytes() == expected.encode()
    assert os.listdir(project) == ["example.py"]  # no .backup / .rej left

    undo = apply_reverse_diff_pipeline(DIFF, str(target), original.decode().replace("\r\n", "\n"))

    assert undo["status"] == "success"
    assert target.read_bytes() == original


# -- context_request path guard -----------------------------------------------

@pytest.mark.parametrize("path", [
    "/etc/passwd",
    "../outside.txt",
    r"\Windows\win.ini",
    r"\\server\share\secret.txt",
    pytest.param(r"C:\Windows\win.ini", marks=windows_only),
    pytest.param(r"C:Windows\win.ini", marks=windows_only),
])
async def test_context_request_rejects_paths_outside_project(path):
    result = await execute_context_request(path, "conversation")
    assert "Security Error" in result


# -- folder cache / watcher paths ---------------------------------------------

def test_folder_cache_keys_and_broadcasts_use_forward_slashes(tmp_path, monkeypatch):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.py").write_text("x = 1\n")
    root = os.path.abspath(str(tmp_path))
    monkeypatch.setitem(folder_service._folder_cache, root, {"data": {}, "timestamp": 0})
    sent = []
    monkeypatch.setattr(folder_service, "_schedule_broadcast", lambda *args: sent.append(args))

    assert folder_service.add_file_to_folder_cache(os.path.join("src", "a.py"), base_dir=root)

    assert "a.py" in folder_service._folder_cache[root]["data"]["src"]["children"]
    assert sent[0][:2] == ("file_added", "src/a.py")


# -- shell tool ---------------------------------------------------------------

def test_decode_output_matches_text_mode_newlines_and_survives_bad_bytes():
    assert shell_server._decode_output(b"a\r\nb\rc\xff") == "a\nb\nc\ufffd"
    assert shell_server._decode_output(None) is None


def test_pipe_data_reaches_the_child_byte_for_byte():
    """Text-mode stdin would turn each \\n into \\r\\n on Windows."""
    script = "import sys; sys.stdout.write(repr(sys.stdin.buffer.read()))"
    result = shell_server._popen_group([sys.executable, "-c", script], 30, None, input_data="a\nb\n")
    assert result.stdout == repr(b"a\nb\n")


def test_timeout_kills_grandchildren_holding_the_pipes():
    child = ("import subprocess, sys, time; "
             "subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)']); "
             "time.sleep(60)")
    start = time.monotonic()
    with pytest.raises(subprocess.TimeoutExpired):
        shell_server._popen_group([sys.executable, "-c", child], 2, None)
    # A surviving grandchild keeps the pipes open until the 5s drain fallback
    assert time.monotonic() - start < 6


@windows_only
@pytest.mark.skipif(shell_server._git_usr_bin() is None, reason="Git for Windows not installed")
def test_timeout_kills_msys_fork_exec_grandchildren():
    """Git's sh forks and execs, so taskkill /T cannot follow the tree."""
    start = time.monotonic()
    with pytest.raises(subprocess.TimeoutExpired):
        shell_server._run_sh_group("sleep 60 & sleep 60", 2, None)
    assert time.monotonic() - start < 6


def test_batch_file_arguments_with_cmd_metacharacters_are_refused():
    npx = r"C:\Program Files\nodejs\npx.CMD"
    with patch("app.mcp_servers.shell_server.resolve_executable", return_value=npx):
        assert shell_server._windows_executable(["npx", "jest", "-t", "a b"], {}) == [npx, "jest", "-t", "a b"]
        for arg in ["a&whoami", "a|b", 'a"&b', "%PATH%", "a\nb"]:
            with pytest.raises(ValueError):
                shell_server._windows_executable(["npx", "jest", arg], {})


def test_git_usr_bin_only_on_windows():
    usr_bin = shell_server._git_usr_bin()
    if sys.platform != "win32":
        assert usr_bin is None
    elif usr_bin is not None:
        assert os.path.isfile(os.path.join(usr_bin, "sh.exe"))


@windows_only
@pytest.mark.skipif(shell_server._git_usr_bin() is None, reason="Git for Windows not installed")
def test_windows_shell_paths_survive_tokenizing(tmp_path):
    srv = shell_server.ShellServer()
    root = tmp_path.as_posix()
    assert srv._execute_pipeline("pwd", 30, str(tmp_path)).stdout.strip() == root
    assert srv._execute_pipeline("echo $PWD", 30, str(tmp_path)).stdout.strip() == root
    home = os.path.expanduser("~").replace("\\", "/")
    assert srv._execute_pipeline("P=~/x; echo $P", 30, str(tmp_path)).stdout.strip() == home + "/x"
    # find must be GNU find from Git, not System32\find.exe
    (tmp_path / "a.py").write_text("")
    assert srv._execute_pipeline("find . -name '*.py'", 30, str(tmp_path)).stdout.split() == ["./a.py"]
