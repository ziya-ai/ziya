"""Confirm banner: [a] approves this and all further commands for the lease;
[x] refuses and revokes.  Design doc §6.3."""
import os, sys, threading, time
import pytest
pytestmark = pytest.mark.skipif(sys.platform.startswith("win"), reason="POSIX only")


@pytest.fixture
def host(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    from app.shadow.pty_host import ShadowCore, InteractiveFrontend
    core = ShadowCore(["sh", "-c", "while read l; do eval \"$l\"; done"], label="aa",
                      control_ceiling="gated")
    r, w = os.pipe()
    fe = InteractiveFrontend(core, stdin_fd=r, stdout_fd=w)
    core.spawn()
    t = threading.Thread(target=core.run, kwargs={"stdin_fd": None}, daemon=True); t.start()
    time.sleep(0.4)
    yield core, fe
    core.terminate_child(); t.join(5)


def _acquire_and_grant(core, fe):
    from app.shadow import client
    resp = client.control_acquire(core.entry.session_id, "conv-1", restriction="gated",
                                  policy="builtin")
    fe.handle_keys(b"g")
    return resp["lease_id"]


def _send_async(core, lease, text):
    from app.shadow import client
    box = {}
    def go():
        try: box["r"] = client.send_line(core.entry.session_id, lease, text)
        except client.ShadowError as e: box["e"] = e.code
    th = threading.Thread(target=go, daemon=True); th.start()
    for _ in range(50):
        if fe_mode(core) == "confirm" or box: break
        time.sleep(0.05)
    return th, box


def fe_mode(core):
    return core.frontend._mode


def test_a_approves_all_further_commands(host):
    core, fe = host
    lease = _acquire_and_grant(core, fe)
    th, box = _send_async(core, lease, "tee /tmp/one")
    assert fe_mode(core) == "confirm"
    fe.handle_keys(b"a")
    th.join(5)
    assert box.get("r", {}).get("ok") is True
    assert core.server.current_lease().auto_approve is True
    # Second not-allowed command: no banner, runs.
    th, box = _send_async(core, lease, "tee /tmp/two")
    th.join(5)
    assert fe_mode(core) != "confirm" and box.get("r", {}).get("ok") is True
    seen = core.server.reader.read(1, 500)["records"]
    assert any(r.get("event") == "control_auto_approved" for r in seen)
    assert any(r.get("event") == "control_auto_approve" for r in seen)


def test_x_refuses_and_revokes(host):
    core, fe = host
    lease = _acquire_and_grant(core, fe)
    th, box = _send_async(core, lease, "tee /tmp/one")
    fe.handle_keys(b"x")
    th.join(5)
    assert box.get("e") == "command_denied"
    assert core.server.current_lease() is None


def test_y_still_approves_once_only(host):
    core, fe = host
    lease = _acquire_and_grant(core, fe)
    th, box = _send_async(core, lease, "tee /tmp/one")
    fe.handle_keys(b"y"); th.join(5)
    assert box.get("r", {}).get("ok") is True
    th, box = _send_async(core, lease, "tee /tmp/two")
    assert fe_mode(core) == "confirm"          # asked again
    fe.handle_keys(b"n"); th.join(5)
    assert box.get("e") == "command_denied"
