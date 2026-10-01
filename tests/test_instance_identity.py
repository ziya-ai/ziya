"""
Server-side instance identity: ~/.ziya/instance_id, the X-Ziya-Instance
response header, StartupInfo.instanceId, missing-project-dir rejection, and
the half-bound loopback probe.

Background: the browser keys IndexedDB / localStorage / BroadcastChannel /
Web Locks by origin (localhost:PORT).  A port reused by a different server,
or an ssh -L tunnel holding ::1 while a local Ziya holds 127.0.0.1, presents
a foreign backend under a familiar origin and the client then trusts a cache
that belongs to someone else.  These tests pin the server half of the fix.
"""
from __future__ import annotations

import json
import os
import socket
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def ziya_home(tmp_path):
    home = tmp_path / ".ziya"
    home.mkdir()
    from app.utils import instance_id
    instance_id.reset_cache_for_tests()
    with patch.dict(os.environ, {"ZIYA_HOME": str(home)}):
        yield home
    instance_id.reset_cache_for_tests()


def _write_project(home: Path, pid: str, path: str, last_accessed: int) -> None:
    d = home / "projects" / pid
    d.mkdir(parents=True)
    (d / "project.json").write_text(json.dumps({
        "id": pid, "name": pid, "path": path,
        "createdAt": 1, "lastAccessedAt": last_accessed,
        "settings": {"defaultContextIds": [], "defaultSkillIds": []},
    }))
    (d / "chats").mkdir()


@pytest.fixture
def projects_client(ziya_home):
    from app.api.projects import router
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


# ---------------------------------------------------------------------------
# instance_id
# ---------------------------------------------------------------------------

class TestInstanceId:
    def test_created_on_first_call_and_persisted(self, ziya_home):
        from app.utils.instance_id import get_instance_id, INSTANCE_ID_FILENAME
        f = ziya_home / INSTANCE_ID_FILENAME
        assert not f.exists()
        first = get_instance_id()
        assert f.exists()
        assert f.read_text().strip() == first
        assert len(first) >= 8

    def test_stable_across_process_restarts(self, ziya_home):
        """A reinstall that keeps ~/.ziya must read as the same installation."""
        from app.utils import instance_id
        first = instance_id.get_instance_id()
        instance_id.reset_cache_for_tests()  # simulate a new process
        assert instance_id.get_instance_id() == first

    def test_wiped_home_is_a_different_installation(self, tmp_path):
        from app.utils import instance_id
        ids = []
        for name in ("a", "b"):
            home = tmp_path / name
            home.mkdir()
            instance_id.reset_cache_for_tests()
            with patch.dict(os.environ, {"ZIYA_HOME": str(home)}):
                ids.append(instance_id.get_instance_id())
        instance_id.reset_cache_for_tests()
        assert ids[0] != ids[1]

    def test_corrupt_file_is_replaced_not_served(self, ziya_home):
        """An id no client could match is worth nothing; regenerate it."""
        from app.utils.instance_id import get_instance_id, INSTANCE_ID_FILENAME
        f = ziya_home / INSTANCE_ID_FILENAME
        f.write_text("not a valid id: has spaces and \n newlines\n")
        got = get_instance_id()
        assert got != "not a valid id: has spaces and"
        assert f.read_text().strip() == got

    def test_hand_written_opaque_id_is_honoured(self, ziya_home):
        from app.utils.instance_id import get_instance_id, INSTANCE_ID_FILENAME
        (ziya_home / INSTANCE_ID_FILENAME).write_text("my-lab-box-01\n")
        assert get_instance_id() == "my-lab-box-01"


# ---------------------------------------------------------------------------
# X-Ziya-Instance header
# ---------------------------------------------------------------------------

def _instance_middleware_or_skip():
    try:
        from app.middleware.instance_header import InstanceHeaderMiddleware
    except ImportError:
        pytest.skip("app/middleware/instance_header.py not applied yet")
    return InstanceHeaderMiddleware


class TestInstanceHeader:
    def test_header_on_every_response(self, ziya_home):
        from app.utils.instance_id import get_instance_id, HEADER_NAME
        mw = _instance_middleware_or_skip()
        app = FastAPI()

        @app.get("/x")
        def x():
            return {"ok": True}

        @app.get("/boom")
        def boom():
            from fastapi import HTTPException
            raise HTTPException(status_code=418)

        app.add_middleware(mw)
        c = TestClient(app)
        assert c.get("/x").headers[HEADER_NAME] == get_instance_id()
        # Error responses too: the latch must not be blinded by a 4xx from
        # the *other* backend.
        assert c.get("/boom").headers[HEADER_NAME] == get_instance_id()

    def test_streaming_body_unmodified(self, ziya_home):
        from fastapi.responses import StreamingResponse
        from app.utils.instance_id import HEADER_NAME
        mw = _instance_middleware_or_skip()
        app = FastAPI()

        @app.get("/s")
        def s():
            def gen():
                yield b"data: 1\n\n"
                yield b"data: 2\n\n"
            return StreamingResponse(gen(), media_type="text/event-stream")

        app.add_middleware(mw)
        r = TestClient(app).get("/s")
        assert HEADER_NAME in r.headers
        assert r.content == b"data: 1\n\ndata: 2\n\n"

    def test_full_app_exposes_header(self, ziya_home):
        """Seam: the middleware is actually mounted on the real app."""
        _instance_middleware_or_skip()
        from app.utils.instance_id import HEADER_NAME, get_instance_id
        from app.server import app
        r = TestClient(app).get("/api/v1/projects/startup")
        assert r.headers.get(HEADER_NAME) == get_instance_id(), (
            "InstanceHeaderMiddleware is not registered on app.server:app"
        )


# ---------------------------------------------------------------------------
# projects API
# ---------------------------------------------------------------------------

class TestStartupInfoInstanceId:
    def test_startup_reports_instance_id(self, projects_client, ziya_home):
        from app.utils.instance_id import get_instance_id
        r = projects_client.get("/api/v1/projects/startup")
        assert r.status_code == 200
        body = r.json()
        assert "instanceId" in body, "StartupInfo.instanceId not applied yet"
        assert body["instanceId"] == get_instance_id()
        assert body["instanceId"]


class TestMissingProjectDirectory:
    """A stale ~/.ziya must not hand a client a project it cannot use."""

    def test_last_accessed_skips_missing_dirs(self, projects_client, ziya_home, tmp_path):
        live = tmp_path / "live"
        live.mkdir()
        _write_project(ziya_home, "gone", "/nonexistent/path/from/last/year", 9_000)
        _write_project(ziya_home, "live", str(live), 1_000)  # older but real
        r = projects_client.get("/api/v1/projects/last-accessed")
        assert r.status_code == 200
        assert r.json()["id"] == "live"

    def test_last_accessed_404_when_every_dir_is_gone(self, projects_client, ziya_home):
        _write_project(ziya_home, "gone1", "/nonexistent/a", 9_000)
        _write_project(ziya_home, "gone2", "/nonexistent/b", 8_000)
        r = projects_client.get("/api/v1/projects/last-accessed")
        # The frontend treats this as "fall through" (root project / picker),
        # which is the correct outcome; a 200 with a phantom project is not.
        assert r.status_code == 404
        # And nothing was auto-created to paper over it.
        assert sorted(p.name for p in (ziya_home / "projects").iterdir()
                      if p.is_dir()) == ["gone1", "gone2"]

    def test_get_by_id_rejects_missing_dir(self, projects_client, ziya_home):
        _write_project(ziya_home, "gone", "/nonexistent/path", 9_000)
        r = projects_client.get("/api/v1/projects/gone")
        assert r.status_code == 404
        assert "/nonexistent/path" in r.json()["detail"]

    def test_get_by_id_still_serves_live_dir(self, projects_client, ziya_home, tmp_path):
        live = tmp_path / "live"
        live.mkdir()
        _write_project(ziya_home, "live", str(live), 9_000)
        r = projects_client.get("/api/v1/projects/live")
        assert r.status_code == 200
        assert r.json()["id"] == "live"

    def test_list_still_shows_missing_dir_projects(self, projects_client, ziya_home):
        """Listing is unchanged: the user must still be able to see and
        delete/repoint a project whose directory moved."""
        _write_project(ziya_home, "gone", "/nonexistent/path", 9_000)
        r = projects_client.get("/api/v1/projects")
        assert r.status_code == 200
        assert [p["id"] for p in r.json()] == ["gone"]


# ---------------------------------------------------------------------------
# half-bound loopback probe
# ---------------------------------------------------------------------------

def _free_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _ipv6_loopback_available() -> bool:
    try:
        s = socket.socket(socket.AF_INET6, socket.SOCK_STREAM)
        s.bind(("::1", 0))
        s.close()
        return True
    except OSError:
        return False


class TestHalfBoundLoopback:
    def test_no_warning_when_port_free(self):
        from app.utils.bind_check import half_bound_loopback_warning
        assert half_bound_loopback_warning("127.0.0.1", _free_port()) is None

    def test_no_warning_for_wildcard_or_non_loopback(self):
        from app.utils.bind_check import half_bound_loopback_warning
        assert half_bound_loopback_warning("0.0.0.0", 8888) is None
        assert half_bound_loopback_warning("192.168.1.5", 8888) is None

    @pytest.mark.skipif(not _ipv6_loopback_available(), reason="no ::1 on this host")
    def test_warns_when_other_family_listening(self):
        """Reproduces the incident: something (ssh -L) holds [::1]:PORT while
        Ziya is about to bind 127.0.0.1:PORT."""
        from app.utils.bind_check import half_bound_loopback_warning
        port = _free_port()
        other = socket.socket(socket.AF_INET6, socket.SOCK_STREAM)
        try:
            other.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
        except OSError:
            pass
        other.bind(("::1", port))
        other.listen(1)
        try:
            msg = half_bound_loopback_warning("127.0.0.1", port)
            assert msg is not None
            assert "::1" in msg and str(port) in msg
            assert "ssh -L 127.0.0.1" in msg
            # Symmetric: binding ::1 while 127.0.0.1 is held.
            v4 = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            v4.bind(("127.0.0.1", port))
            v4.listen(1)
            try:
                assert half_bound_loopback_warning("::1", port) is not None
            finally:
                v4.close()
        finally:
            other.close()

    def test_probe_does_not_leave_socket_bound(self):
        """The probe must not itself occupy the port it reports on, or the
        real bind that follows would fail because of us."""
        from app.utils.bind_check import half_bound_loopback_warning
        port = _free_port()
        half_bound_loopback_warning("127.0.0.1", port)
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            s.bind(("127.0.0.1", port))  # must succeed
        finally:
            s.close()
