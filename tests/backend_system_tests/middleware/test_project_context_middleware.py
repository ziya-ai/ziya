"""
Integration tests for ProjectContextMiddleware and app.context ContextVar.

Verifies that:
  - X-Project-Root header sets the per-request project root
  - Requests without the header fall back to env var / cwd
  - Non-existent paths in the header are ignored (fallback)
  - Concurrent requests with different headers don't interfere
"""

import os
import unittest
import tempfile

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.context import get_project_root, get_project_root_or_none
from app.middleware.project_context import ProjectContextMiddleware


def _make_app() -> FastAPI:
    """Create a minimal FastAPI app with ProjectContextMiddleware."""
    app = FastAPI()
    app.add_middleware(ProjectContextMiddleware)

    @app.get("/project-root")
    async def project_root_endpoint():
        return {
            "root": get_project_root(),
            "root_or_none": get_project_root_or_none(),
        }

    return app


class TestProjectContextMiddleware(unittest.TestCase):
    """Test suite for the project-context middleware and ContextVar."""

    def setUp(self):
        self.app = _make_app()
        self.client = TestClient(self.app)
        self._saved_env = os.environ.get("ZIYA_USER_CODEBASE_DIR")

    def tearDown(self):
        if self._saved_env is not None:
            os.environ["ZIYA_USER_CODEBASE_DIR"] = self._saved_env
        else:
            os.environ.pop("ZIYA_USER_CODEBASE_DIR", None)

    def test_header_sets_project_root(self):
        """X-Project-Root header should set the request-scoped project root."""
        with tempfile.TemporaryDirectory() as tmp:
            resp = self.client.get("/project-root", headers={"X-Project-Root": tmp})
            data = resp.json()
            self.assertEqual(data["root"], tmp)
            self.assertEqual(data["root_or_none"], tmp)

    def test_no_header_falls_back_to_env(self):
        """Without the header, get_project_root() should return the env var."""
        os.environ["ZIYA_USER_CODEBASE_DIR"] = "/tmp/fallback-env"
        resp = self.client.get("/project-root")
        data = resp.json()
        self.assertEqual(data["root"], "/tmp/fallback-env")
        self.assertIsNone(data["root_or_none"])

    def test_no_header_no_env_falls_back_to_cwd(self):
        """Without header or env var, get_project_root() returns cwd."""
        os.environ.pop("ZIYA_USER_CODEBASE_DIR", None)
        resp = self.client.get("/project-root")
        data = resp.json()
        self.assertEqual(data["root"], os.getcwd())
        self.assertIsNone(data["root_or_none"])

    def test_nonexistent_path_ignored(self):
        """A non-existent X-Project-Root path should be ignored (fallback)."""
        os.environ["ZIYA_USER_CODEBASE_DIR"] = "/tmp/fallback-env"
        resp = self.client.get(
            "/project-root",
            headers={"X-Project-Root": "/this/path/does/not/exist"},
        )
        data = resp.json()
        # Middleware logs a warning and does NOT set the ContextVar
        self.assertEqual(data["root"], "/tmp/fallback-env")
        self.assertIsNone(data["root_or_none"])

    def test_contextvar_resets_between_requests(self):
        """ContextVar must not leak between sequential requests."""
        with tempfile.TemporaryDirectory() as tmp:
            # First request sets the header
            resp1 = self.client.get(
                "/project-root", headers={"X-Project-Root": tmp}
            )
            self.assertEqual(resp1.json()["root_or_none"], tmp)

            # Second request without header -- should NOT see previous value
            os.environ["ZIYA_USER_CODEBASE_DIR"] = "/tmp/env-val"
            resp2 = self.client.get("/project-root")
            self.assertIsNone(resp2.json()["root_or_none"])
            self.assertEqual(resp2.json()["root"], "/tmp/env-val")

    def test_different_headers_isolated(self):
        """Back-to-back requests with different headers should each see
        only their own project root."""
        with tempfile.TemporaryDirectory() as tmp_a, \
             tempfile.TemporaryDirectory() as tmp_b:
            resp_a = self.client.get(
                "/project-root", headers={"X-Project-Root": tmp_a}
            )
            resp_b = self.client.get(
                "/project-root", headers={"X-Project-Root": tmp_b}
            )
            self.assertEqual(resp_a.json()["root"], tmp_a)
            self.assertEqual(resp_b.json()["root"], tmp_b)


if __name__ == "__main__":
    unittest.main()
