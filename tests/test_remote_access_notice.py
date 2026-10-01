"""
A server started on a remote/headless host must tell the user, loudly and with
the exact ssh -L line, that http://localhost:PORT is on the wrong machine.

Observed: users ssh into a dev desktop, run `ziya`, and are shown only
uvicorn's "running on http://127.0.0.1:6969", which their laptop browser cannot
reach. A launcher that already opened the tunnel exports ZIYA_TUNNELED, and the
notice stays silent; a laptop (no ssh vars, not headless Linux) is never nagged.
"""
import io
import re
from pathlib import Path

import app.utils.remote_access as ra

ROOT = Path(__file__).resolve().parents[1]


def test_ssh_session_looks_remote_on_any_platform():
    assert ra.looks_remote({"SSH_CONNECTION": "1.2.3.4 5 6.7.8.9 22"}, platform="darwin")
    assert ra.looks_remote({"SSH_TTY": "/dev/pts/3"}, platform="linux")


def test_headless_linux_looks_remote_but_desktop_linux_and_mac_do_not():
    assert ra.looks_remote({}, platform="linux")
    assert not ra.looks_remote({"DISPLAY": ":0"}, platform="linux")
    assert not ra.looks_remote({"WAYLAND_DISPLAY": "wayland-0"}, platform="linux")
    assert not ra.looks_remote({}, platform="darwin")
    assert not ra.looks_remote({}, platform="win32")


def _capture(env, port=6969):
    out = io.StringIO()
    lines = ra.maybe_print_notice(port, out=out, environ=env)
    return lines, out.getvalue()


def test_remote_untunnelled_prints_boxed_notice_with_tunnel_command(monkeypatch):
    monkeypatch.setattr(ra, "_host", lambda: "dev-dsk-x.example.com")
    monkeypatch.setenv("USER", "alice")      # notice_lines reads the process env for the login
    lines, text = _capture({"SSH_CONNECTION": "x"}, port=8080)
    assert lines is not None
    assert "REMOTE HOST" in text
    assert "ssh -L 8080:localhost:8080 alice@dev-dsk-x.example.com" in text
    assert "http://localhost:8080" in text
    assert "dev-dsk-x.example.com" in text
    assert text.count("!!!!") >= 3                    # visibly boxed, not a log line


def test_tunnelled_launch_is_silent():
    lines, text = _capture({"SSH_CONNECTION": "x", "ZIYA_TUNNELED": "8080"})
    assert lines is None and text == ""


def test_local_machine_is_silent():
    import sys
    if sys.platform.startswith("linux"):
        env = {"DISPLAY": ":0"}
    else:
        env = {}
    lines, text = _capture(env)
    assert lines is None and text == ""


def test_opt_out_env_is_silent():
    lines, text = _capture({"SSH_CONNECTION": "x", "ZIYA_REMOTE_NOTICE": "0"})
    assert lines is None and text == ""


def test_editions_can_replace_the_wording_without_touching_detection(monkeypatch):
    # The override seam: an edition swaps notice_lines; detection/printing stay.
    monkeypatch.setattr(ra, "notice_lines", lambda port, host: [f"EDITION {port} {host}"])
    monkeypatch.setattr(ra, "_host", lambda: "h")
    lines, text = _capture({"SSH_TTY": "x"}, port=7)
    assert lines == ["EDITION 7 h"] and "EDITION 7 h" in text


def test_main_prints_the_notice_at_the_listen_site():
    # Seam: the module is worthless unless the server calls it, and it must
    # run BEFORE uvicorn starts so it lands beside uvicorn's own URL line.
    src = (ROOT / "app" / "main.py").read_text()
    call = src.find("maybe_print_notice(args.port)")
    run = src.find("uvicorn.run(app, host=bind_host, port=args.port)")
    assert call != -1, "main.py never prints the remote-access notice"
    assert run != -1
    assert call < run, "notice must be printed before uvicorn.run"
    assert not re.search(r"amazon|dev desktop|ziya-remote", (ROOT / "app" / "utils" / "remote_access.py").read_text(), re.I)
