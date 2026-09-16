"""Yield-to-human for line control (design doc §6.1a).

The human never learns a pause key or a precedence model: a control-lease
write simply waits until the terminal is free — the human has been quiet,
has no partial line typed, and the child's output has settled — and reports
``terminal_busy`` if that does not happen within its wait budget.  Two
typists can therefore never interleave inside one line.
"""
import sys
import threading
import time

import pytest

pytestmark = pytest.mark.skipif(sys.platform.startswith("win"), reason="POSIX pty only")


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    return tmp_path


def _wait(pred, timeout=5.0):
    end = time.time() + timeout
    while time.time() < end:
        if pred():
            return True
        time.sleep(0.05)
    return False


@pytest.fixture
def core(home):
    from app.shadow.pty_host import ShadowCore
    c = ShadowCore(["sh", "-c", "while read l; do eval \"$l\"; done"], label="y",
                   headless=True, control_ceiling="unrestricted")
    c.spawn()
    seen = []
    c.journal.add_listener(seen.append)
    c.seen = seen
    t = threading.Thread(target=c.run, kwargs={"stdin_fd": None}, daemon=True)
    t.start()
    time.sleep(0.6)
    yield c
    c.terminate_child()
    t.join(timeout=5)


def test_waits_while_human_recently_typed(core):
    from app.shadow import pty_host
    core.handle_input(b"echo human-line\r")
    r = core.send_line_to_child("echo model-line", wait_s=0.3)
    assert r == {"error": "terminal_busy", "detail": "human typing"}
    # Once the human has been quiet, the model gets its turn.
    time.sleep(pty_host.HUMAN_QUIET_S + 0.2)
    r = core.send_line_to_child("echo model-line", wait_s=1.0)
    assert "error" not in r, r
    assert _wait(lambda: any(x["t"] == "output" and "model-line" in x["text"] for x in core.seen))


def test_waits_while_human_has_partial_line(core):
    from app.shadow import pty_host
    core.handle_input(b"echo hum")                       # no Enter yet
    time.sleep(pty_host.HUMAN_QUIET_S + 0.2)             # quiet, but line unfinished
    r = core.send_line_to_child("echo model-line", wait_s=0.3)
    assert r["error"] == "terminal_busy" and "partial line" in r["detail"]
    # The human's keystrokes reached the shell untouched; nothing interleaved.
    core.handle_input(b"an-done\r")
    assert _wait(lambda: any(x["t"] == "output" and "human-done" in x["text"] for x in core.seen))
    assert not any(x["t"] == "output" and "model-line" in x["text"] for x in core.seen)
    time.sleep(pty_host.HUMAN_QUIET_S + 0.2)
    assert "error" not in core.send_line_to_child("echo model-line", wait_s=1.0)
    assert _wait(lambda: any(x["t"] == "output" and "model-line" in x["text"] for x in core.seen))


def test_waits_while_output_still_arriving(core):
    # A chatty command; the model must not type into its output stream.
    assert "error" not in core.send_line_to_child(
        "i=0; while [ $i -lt 30 ]; do echo tick-$i; i=$((i+1)); sleep 0.05; done", wait_s=2.0)
    time.sleep(0.2)
    r = core.send_line_to_child("echo model-line", wait_s=0.2)
    assert r == {"error": "terminal_busy", "detail": "output still arriving"}
    # Default wait budget outlasts the chatter; the write goes through after it settles.
    r = core.send_line_to_child("echo model-line")
    assert "error" not in r, r
    assert _wait(lambda: any(x["t"] == "output" and "model-line" in x["text"] for x in core.seen))


def test_terminal_busy_surfaces_over_the_socket(core):
    from app.shadow import client, pty_host
    monkey_wait = pty_host.SEND_WAIT_S
    pty_host.SEND_WAIT_S = 0.2
    try:
        lease = client.control_acquire(core.entry.session_id, "conv", restriction="unrestricted",
                                       policy="none")
        core.handle_input(b"echo human\r")
        # send_line_to_child default arg was bound at def time; drive the
        # wait budget through the module constant the server relies on.
        core.send_line_to_child.__func__.__defaults__ = (0.2,)
        with pytest.raises(client.ShadowError) as ei:
            client.send_line(core.entry.session_id, lease["lease_id"], "echo model")
        assert ei.value.code == "terminal_busy" and "human is using the terminal" in ei.value.msg
    finally:
        pty_host.SEND_WAIT_S = monkey_wait
        core.send_line_to_child.__func__.__defaults__ = (monkey_wait,)
