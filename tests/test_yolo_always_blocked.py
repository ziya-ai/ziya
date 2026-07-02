"""
Regression guard: YOLO mode must enforce `always_blocked` by BASENAME, not just
the literal first word (ASR follow-up to F-001/F-004).

YOLO bypasses the command allowlist, so `always_blocked` (sudo/su/…) is the only
barrier left. The normal-mode path already matched both the first word and its
basename; the YOLO path matched the first word only, so an absolute-path
invocation (`/usr/bin/sudo …`) slipped through. On an Amazon dev host
(`NOPASSWD: ALL`), that let a YOLO-session agent reach `sudo <venv>/ziya-approve
--provision` and mint escalation signatures. This pins the basename check.

Note: this is defense-in-depth for the YOLO case (a user opts into YOLO = "run
anything"); the durable concern is that a signed approval outlives the session.
"""

import pytest

from app.mcp_servers.shell_server import ShellServer


@pytest.fixture
def yolo_server():
    # Unsigned YOLO_MODE via env is stripped by the F-004 scope gate, so set the
    # flag directly on the instance to exercise the YOLO branch deterministically.
    srv = ShellServer.__new__(ShellServer)
    ShellServer.__init__(srv)
    srv.yolo_mode = True
    return srv


BLOCKED = [
    "sudo reboot",
    "/usr/bin/sudo /path/ziya-approve --provision",   # the fix: absolute-path sudo
    "/bin/su -",
    "su root",
    "echo hi\n/usr/bin/sudo reboot",                  # after a newline separator
    "ls | /usr/bin/sudo tee /etc/x",                  # in a pipeline segment
]

ALLOWED = [
    "ls -la",
    "cat f.txt | grep x",
    "python3 -c 'print(1)'",
]


@pytest.mark.parametrize("cmd", BLOCKED)
def test_yolo_blocks_always_blocked_by_basename(yolo_server, cmd):
    ok, _ = yolo_server.is_command_allowed(cmd)
    assert ok is False, f"YOLO should block {cmd!r} via always_blocked basename"


@pytest.mark.parametrize("cmd", ALLOWED)
def test_yolo_allows_ordinary_commands(yolo_server, cmd):
    ok, _ = yolo_server.is_command_allowed(cmd)
    assert ok is True, f"YOLO should still allow {cmd!r}"
