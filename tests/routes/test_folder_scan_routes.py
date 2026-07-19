"""
Characterization tests for the folder scan-control routes
(/folder-progress and /api/cancel-scan).

These pin the CURRENT route -> directory_util accessor contract BEFORE the
per-path (Piece 3) refactor, so the refactor can be proven behavior-preserving
at the HTTP layer. The service/util layer is covered separately in
tests/test_folder_service.py.

Empirically verified against the live route handlers before being written.
When `get_scan_progress`/`cancel_scan` gain a required `directory` argument,
these tests document exactly what the routes must keep returning; the route
handlers will be updated to thread a directory through, and these assertions
(response shape) must stay green while the accessor CALL changes.
"""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from unittest.mock import patch


@pytest.fixture
def client():
    from app.routes.folder_routes import router
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


class TestFolderProgressRoute:
    """GET /folder-progress -> directory_util.get_scan_progress()."""

    def test_active_progress_passthrough_with_percentage(self, client):
        """When a scan is active with real progress, the route returns the
        progress dict and injects a computed percentage."""
        import app.utils.directory_util as du
        fake = {"active": True, "progress": {"directories": 5},
                "estimated_total": 10}
        with patch.object(du, 'get_scan_progress', return_value=fake):
            resp = client.get("/folder-progress")
        assert resp.status_code == 200
        body = resp.json()
        assert body["active"] is True
        assert body["progress"]["directories"] == 5
        assert body["progress"]["percentage"] == 50

    def test_active_but_no_progress_collapses_to_inactive(self, client):
        """active=True with an empty progress dict is reported as inactive
        (the route suppresses 'active' when there's nothing to show)."""
        import app.utils.directory_util as du
        fake = {"active": True, "progress": {}, "estimated_total": 0}
        with patch.object(du, 'get_scan_progress', return_value=fake):
            resp = client.get("/folder-progress")
        assert resp.status_code == 200
        body = resp.json()
        assert body["active"] is False
        assert body["progress"] == {}

    def test_no_percentage_without_estimated_total(self, client):
        """No percentage is added when estimated_total is 0."""
        import app.utils.directory_util as du
        fake = {"active": True, "progress": {"directories": 3},
                "estimated_total": 0}
        with patch.object(du, 'get_scan_progress', return_value=fake):
            resp = client.get("/folder-progress")
        assert resp.status_code == 200
        assert "percentage" not in resp.json()["progress"]

    def test_route_calls_get_scan_progress(self, client):
        """Pin the contract: the route delegates to get_scan_progress."""
        import app.utils.directory_util as du
        fake = {"active": False, "progress": {}, "estimated_total": 0}
        with patch.object(du, 'get_scan_progress',
                          return_value=fake) as m:
            client.get("/folder-progress")
        assert m.called

    def test_progress_threads_directory_per_path(self, client):
        """Regression for the isScanning flap bug: /folder-progress must
        resolve project_path to an absolute directory and pass it to
        get_scan_progress(directory), reading that project's own scan slot
        rather than always falling back to the server's default project
        root. /folder (POST) and /api/files/validate already key their
        cache/scan work off the caller-supplied project path; before this
        fix, /folder-progress (GET) was the only one that ignored it,
        causing it to report on the wrong project's (usually idle) slot and
        fight with the real per-project result from GET /folder, flapping
        the frontend's "scanning folder structure" indicator on and off.
        """
        import os
        import app.utils.directory_util as du
        captured = {}

        def spy(directory=None):
            captured['directory'] = directory
            return {"active": True, "progress": {"directories": 3},
                    "estimated_total": 6}

        # Explicit project_path -> route must pass its abspath, not the
        # server's default project root.
        with patch.object(du, 'get_scan_progress', spy):
            resp = client.get("/folder-progress?project_path=/tmp/projX")
        assert resp.status_code == 200
        assert captured['directory'] == os.path.abspath("/tmp/projX")

        # A second, different project_path must resolve to its own
        # directory too -- proving the route doesn't cache/pin the first
        # resolved directory across requests.
        with patch.object(du, 'get_scan_progress', spy):
            resp = client.get("/folder-progress?project_path=/tmp/projY")
        assert resp.status_code == 200
        assert captured['directory'] == os.path.abspath("/tmp/projY")

    def test_progress_omitted_path_falls_back_to_project_root(self, client):
        """Legacy no-arg contract: omitting project_path still resolves to
        the current project root (not None), matching /api/cancel-scan's
        documented fallback behavior."""
        import app.routes.folder_routes as fr
        import app.utils.directory_util as du
        captured = {}

        def spy(directory=None):
            captured['directory'] = directory
            return {"active": False, "progress": {}, "estimated_total": 0}

        with patch.object(fr, 'get_project_root', return_value="/fake/root"), \
                patch.object(du, 'get_scan_progress', spy):
            resp = client.get("/folder-progress")
        assert resp.status_code == 200
        assert captured['directory'] == "/fake/root"


class TestCancelScanRoute:
    """POST /api/cancel-scan -> directory_util.cancel_scan()."""

    def test_cancel_active_scan_returns_true(self, client):
        import app.utils.directory_util as du
        with patch.object(du, 'cancel_scan', return_value=True) as m:
            resp = client.post("/api/cancel-scan")
        assert resp.status_code == 200
        assert resp.json() == {"cancelled": True}
        assert m.called

    def test_cancel_when_idle_returns_false(self, client):
        import app.utils.directory_util as du
        with patch.object(du, 'cancel_scan', return_value=False):
            resp = client.post("/api/cancel-scan")
        assert resp.status_code == 200
        assert resp.json() == {"cancelled": False}

    def test_cancel_threads_directory_per_path(self, client):
        """Per-path (Piece 3) contract — the FLIP of the old no-arg pin: the
        route now resolves a directory and passes it to cancel_scan(directory),
        so it cancels only that project's scan. With an explicit project_path
        the route passes its abspath; with none, it falls back to the current
        project root. Either way, exactly one positional directory is passed.
        """
        import os
        import app.utils.directory_util as du
        captured = {}

        def spy(*args, **kwargs):
            captured['args'] = args
            captured['kwargs'] = kwargs
            return False

        # Explicit project_path -> route passes its abspath positionally.
        with patch.object(du, 'cancel_scan', spy):
            resp = client.post("/api/cancel-scan?project_path=/tmp/projX")
        assert resp.status_code == 200
        assert captured['args'] == (os.path.abspath("/tmp/projX"),)
        assert captured['kwargs'] == {}

        # No project_path -> route still passes exactly one directory (the
        # resolved project root), never zero args.
        with patch.object(du, 'cancel_scan', spy):
            resp = client.post("/api/cancel-scan")
        assert resp.status_code == 200
        assert len(captured['args']) == 1 and captured['args'][0]


class TestDefaultIncludedFoldersRoute:
    """GET /api/default-included-folders -> collect_documentation_file_keys()."""

    def test_returns_wrapped_keys_for_project_root(self, client):
        """No project_path: resolves the project root and returns the collected
        keys under the 'defaultIncludedFolders' field."""
        import app.routes.folder_routes as fr
        with patch.object(fr, 'get_project_root', return_value="/tmp/projX"), \
             patch("os.path.isdir", return_value=True), \
             patch.object(fr, '_collect_documentation_file_keys',
                          return_value=["AGENTS.md", "README.md"]) as coll:
            resp = client.get("/api/default-included-folders")
        assert resp.status_code == 200
        assert resp.json() == {"defaultIncludedFolders": ["AGENTS.md", "README.md"]}
        # README is restricted to the root; AGENTS.md stays recursive.
        assert coll.call_args.kwargs.get("readme_root_only") is True

    def test_explicit_project_path_is_abspathed(self, client):
        """An explicit project_path is passed to the collector as its abspath."""
        import os
        import app.routes.folder_routes as fr
        with patch.object(fr, 'get_project_root', return_value="/should/not/be/used"), \
             patch("os.path.isdir", return_value=True), \
             patch.object(fr, '_collect_documentation_file_keys',
                          return_value=[]) as coll:
            resp = client.get("/api/default-included-folders?project_path=/tmp/projY")
        assert resp.status_code == 200
        assert coll.call_args.args[0] == os.path.abspath("/tmp/projY")

    def test_missing_root_returns_empty_list(self, client):
        """When the resolved root is not a directory, returns an empty list."""
        import app.routes.folder_routes as fr
        with patch.object(fr, 'get_project_root', return_value=None):
            resp = client.get("/api/default-included-folders")
        assert resp.status_code == 200
        assert resp.json() == {"defaultIncludedFolders": []}

    def test_collector_error_returns_empty_list(self, client):
        """A collector exception is swallowed and reported as an empty list."""
        import app.routes.folder_routes as fr
        with patch.object(fr, 'get_project_root', return_value="/tmp/projX"), \
             patch("os.path.isdir", return_value=True), \
             patch.object(fr, '_collect_documentation_file_keys',
                          side_effect=RuntimeError("boom")):
            resp = client.get("/api/default-included-folders")
        assert resp.status_code == 200
        assert resp.json() == {"defaultIncludedFolders": []}
