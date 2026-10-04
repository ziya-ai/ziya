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


# -- read allowlist and external paths ----------------------------------------

def test_allowed_absolute_prefix_admits_native_paths(tmp_path):
    from app.mcp.tools.fileio import _resolve_and_validate
    project = tmp_path / "project"
    project.mkdir()
    (tmp_path / "outside").mkdir()
    (tmp_path / "outside-other").mkdir()
    target = tmp_path / "outside" / "notes.txt"
    target.write_text("hi")
    sibling = tmp_path / "outside-other" / "x.txt"
    sibling.write_text("no")

    prefixes = [str(tmp_path / "outside")]
    assert _resolve_and_validate(str(target), str(project), prefixes) == target.resolve()
    with pytest.raises(ValueError):
        _resolve_and_validate(str(sibling), str(project), prefixes)


@windows_only
def test_allowed_absolute_prefix_ignores_case_on_windows(tmp_path):
    from app.mcp.tools.fileio import _resolve_and_validate
    (tmp_path / "project").mkdir()
    (tmp_path / "Outside").mkdir()
    target = tmp_path / "Outside" / "notes.txt"
    target.write_text("hi")
    prefixes = [str(tmp_path / "Outside").lower()]
    assert _resolve_and_validate(str(target), str(tmp_path / "project"), prefixes) == target.resolve()


def test_external_tree_keys_use_slashes_and_resolve_to_the_file(tmp_path, monkeypatch):
    from app.utils.file_utils import external_key, resolve_external_path
    outside = tmp_path / "outside"
    (outside / "sub").mkdir(parents=True)
    target = outside / "sub" / "notes.md"
    target.write_text("hi")
    monkeypatch.setattr(folder_service, "_explicit_external_paths", {str(outside)})

    key = external_key(str(target))
    assert key.startswith("[external]/") and "\\" not in key
    assert os.path.samefile(resolve_external_path(key, str(tmp_path / "project")), target)
    assert folder_service.collect_leaf_file_keys(str(outside), False, str(tmp_path / "project")) == [key]


def test_collected_project_keys_use_forward_slashes(tmp_path):
    (tmp_path / "pkg" / "sub").mkdir(parents=True)
    (tmp_path / "pkg" / "sub" / "a.py").write_text("x = 1\n")
    assert folder_service.collect_leaf_file_keys(str(tmp_path), True, str(tmp_path)) == ["pkg/sub/a.py"]


# -- context_add_file's symlink-safe read without O_NOFOLLOW --------------------

@pytest.fixture
def without_o_nofollow(monkeypatch):
    monkeypatch.delattr(os, "O_NOFOLLOW", raising=False)


def test_no_follow_open_reads_regular_files_without_o_nofollow(tmp_path, without_o_nofollow):
    from app.mcp.tools.context_management import _open_no_follow
    path = tmp_path / "a.txt"
    path.write_bytes(b"hello")
    fd = _open_no_follow(path)
    try:
        assert os.read(fd, 10) == b"hello"
    finally:
        os.close(fd)


def test_no_follow_open_refuses_a_file_swapped_in_after_lstat(tmp_path, monkeypatch, without_o_nofollow):
    from app.mcp.tools.context_management import _open_no_follow
    validated = tmp_path / "validated.txt"
    validated.write_text("ok")
    swapped = tmp_path / "secret.txt"
    swapped.write_text("secret")
    real_lstat = os.lstat
    # lstat() sees the validated file; the open() then lands on another one
    monkeypatch.setattr(os, "lstat", lambda p, *a, **k:
                        real_lstat(validated) if str(p) == str(swapped) else real_lstat(p, *a, **k))
    with pytest.raises(OSError):
        _open_no_follow(swapped)


def test_no_follow_open_refuses_symlinks_without_o_nofollow(tmp_path, without_o_nofollow):
    from app.mcp.tools.context_management import _open_no_follow
    secret = tmp_path / "secret.txt"
    secret.write_text("secret")
    link = tmp_path / "link.txt"
    try:
        os.symlink(secret, link)
    except (OSError, NotImplementedError):
        pytest.skip("cannot create symlinks here")
    with pytest.raises(OSError):
        _open_no_follow(link)


# -- encryption key material without os.fchmod --------------------------------

def test_keyring_and_salt_are_written_without_fchmod(tmp_path, monkeypatch):
    import json
    from app.utils.encryption import DataEncryptor, Keyring
    monkeypatch.delattr(os, "fchmod", raising=False)  # as on Windows before Python 3.13
    monkeypatch.setenv("ZIYA_HOME", str(tmp_path / ".ziya"))

    keyring = Keyring()
    keyring._save()
    assert json.loads(keyring.path.read_text())["keys"] == []
    encryptor = DataEncryptor()
    salt = encryptor._get_or_create_passphrase_salt()
    assert len(salt) == 16 and encryptor._get_or_create_passphrase_salt() == salt


# -- file_write line endings -------------------------------------------------

@pytest.mark.parametrize("newline", [b"\n", b"\r\n"])
async def test_file_write_patch_keeps_line_endings(tmp_path, newline):
    from app.mcp.tools.fileio import FileWriteTool
    target = tmp_path / "notes.md"
    target.write_bytes(b"one" + newline + b"two" + newline)
    with patch("app.mcp.tools.fileio._check_write_allowed", return_value=""):
        result = await FileWriteTool().execute(path="notes.md", patch="two", content="2\n3",
                                               _workspace_path=str(tmp_path))
    assert result.get("success") is True
    assert target.read_bytes() == newline.join([b"one", b"2", b"3", b""])


async def test_file_write_writes_content_as_given(tmp_path):
    from app.mcp.tools.fileio import FileWriteTool
    with patch("app.mcp.tools.fileio._check_write_allowed", return_value=""):
        result = await FileWriteTool().execute(path="new.md", content="a\nb\n",
                                               _workspace_path=str(tmp_path))
    assert result.get("success") is True
    assert (tmp_path / "new.md").read_bytes() == b"a\nb\n"


# -- frontend assets ----------------------------------------------------------

def test_frontend_scripts_are_served_as_javascript_despite_a_bad_registry():
    import mimetypes
    from app.server import _pin_frontend_mime_types
    broken = mimetypes.MimeTypes()
    broken.add_type("text/plain", ".js")  # what the Windows registry often says
    _pin_frontend_mime_types(broken)
    assert broken.guess_type("main.js")[0] == "text/javascript"
    assert broken.guess_type("main.css")[0] == "text/css"

    fine = mimetypes.MimeTypes()
    fine.add_type("application/javascript", ".js")
    _pin_frontend_mime_types(fine)
    assert fine.guess_type("main.js")[0] == "application/javascript"  # left alone


# -- upgrades -----------------------------------------------------------------

def test_windows_auto_update_never_runs_pip_in_process(monkeypatch):
    import app.main as ziya_main
    calls = []
    monkeypatch.setattr(ziya_main.sys, "platform", "win32")
    monkeypatch.setattr(ziya_main.subprocess, "check_call", lambda *args, **kw: calls.append(args))
    monkeypatch.setattr(ziya_main.subprocess, "run", lambda *args, **kw: calls.append(args))
    ziya_main.update_package("0.0.1", "0.0.2")
    assert calls == []


@pytest.mark.parametrize("marker, command", [
    ("uv-receipt.toml", "uv tool upgrade ziya"),
    ("pipx_metadata.json", "pipx upgrade ziya"),
    (None, "-m pip install --upgrade ziya"),
])
def test_upgrade_command_matches_the_installer(tmp_path, monkeypatch, marker, command):
    import app.main as ziya_main
    if marker:
        (tmp_path / marker).write_text("")
    monkeypatch.setattr(ziya_main.sys, "prefix", str(tmp_path))
    assert command in ziya_main._upgrade_command()


# -- setup hints ----------------------------------------------------------------

def test_env_hints_use_powershell_syntax_on_windows(monkeypatch):
    from app.utils import process_utils
    monkeypatch.setattr(process_utils.sys, "platform", "win32")
    assert process_utils.env_hint("  export ANTHROPIC_API_KEY=sk-ant-...") == '  $env:ANTHROPIC_API_KEY = "sk-ant-..."'
    assert process_utils.env_hint("export AWS_ACCESS_KEY_ID=...  AWS_SECRET_ACCESS_KEY=...") == (
        '$env:AWS_ACCESS_KEY_ID = "..."; $env:AWS_SECRET_ACCESS_KEY = "..."'
    )
    assert process_utils.env_hint("export A=1\nexport B=2\n") == '$env:A = "1"\n$env:B = "2"\n'
    assert process_utils.env_hint("aws configure") == "aws configure"


def test_env_hints_are_unchanged_off_windows(monkeypatch):
    from app.utils import process_utils
    monkeypatch.setattr(process_utils.sys, "platform", "linux")
    assert process_utils.env_hint("  export AWS_PROFILE=<p>") == "  export AWS_PROFILE=<p>"


def test_missing_credential_help_shows_powershell_syntax_on_windows(monkeypatch):
    from app.utils import process_utils, provider_detection
    monkeypatch.setattr(process_utils.sys, "platform", "win32")
    help_text = provider_detection.build_setup_help()
    assert "export " not in help_text
    assert "$env:ANTHROPIC_API_KEY" in help_text
