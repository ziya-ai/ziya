"""GET /api/shadow/attached — the chat input-box chip's data source.

Asserts the seam the chip depends on: the route returns exactly the
sessions attached to the given conversation (never another chat's), and
surfaces the control-lease state so the chip can distinguish "the model
can read this" from "the model can type here".  Runs against a real
headless shadow host; same data helper feeds the model's context tag.
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


@pytest.fixture
def live(home):
    from app.shadow.pty_host import ShadowCore
    core = ShadowCore(["sh", "-c", "echo up; read x; exit 0"], label="chipsess",
                      headless=True, control_ceiling="gated")
    core.spawn()
    t = threading.Thread(target=core.run, kwargs={"stdin_fd": None}, daemon=True)
    t.start()
    time.sleep(0.6)
    yield core
    core.handle_input(b"go\r")
    t.join(timeout=5)


@pytest.fixture
def api():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.routes.shadow_routes import router
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def test_route_lists_only_this_conversations_sessions(api, live):
    from app.shadow import client
    sid = live.entry.session_id
    # Unattached: nobody sees it.
    assert api.get("/api/shadow/attached", params={"conversation_id": "conv-A"}).json()["sessions"] == []
    client.attach(sid, "conv-A")
    a = api.get("/api/shadow/attached", params={"conversation_id": "conv-A"}).json()["sessions"]
    b = api.get("/api/shadow/attached", params={"conversation_id": "conv-B"}).json()["sessions"]
    assert [s["session_id"] for s in a] == [sid]
    assert b == []                               # another chat's terminal is invisible
    row = a[0]
    assert row["label"] == "chipsess" and row["display"] == f"chipsess ({sid})"
    assert row["reachable"] is True and row["records"] >= 1
    assert row["control"] is None                # observe-only so far


def test_route_surfaces_control_lease_state(api, live):
    from app.shadow import client
    sid = live.entry.session_id
    client.attach(sid, "conv-A")
    # Headless → implicit grant, clamped to strict when spawned_by is set;
    # here spawned_by is None so restriction stays as requested.
    client.control_acquire(sid, "conv-A", restriction="gated", policy="builtin")
    row = api.get("/api/shadow/attached", params={"conversation_id": "conv-A"}).json()["sessions"][0]
    assert row["control"] == {"restriction": "gated", "policy": "builtin", "granted": True}
    # A lease held by conv-A is not reported to conv-B even if B were attached.
    client.attach(sid, "conv-B")                 # last-writer-wins display slot
    rowb = api.get("/api/shadow/attached", params={"conversation_id": "conv-B"}).json()["sessions"][0]
    assert rowb["control"] is None


def test_route_validates_query(api):
    assert api.get("/api/shadow/attached").status_code == 422
    assert api.get("/api/shadow/attached", params={"conversation_id": ""}).status_code == 422
    r = api.get("/api/shadow/attached", params={"conversation_id": "nobody"})
    assert r.status_code == 200 and r.json()["sessions"] == []
