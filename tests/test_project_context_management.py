"""
Tests for project-level context management settings.

Covers:
  - Default contextManagement settings
  - Updating contextManagement without clobbering other settings
  - Persistence round-trip through project update API
"""

import json
import os
import time
from pathlib import Path
from unittest.mock import patch

import pytest


@pytest.fixture
def ziya_home(tmp_path):
    home = tmp_path / ".ziya"
    home.mkdir()
    return home


@pytest.fixture
def project_with_settings(ziya_home):
    """Create a project that already has defaultContextIds set."""
    project_id = "ctx-mgmt-test"
    proj_dir = ziya_home / "projects" / project_id
    proj_dir.mkdir(parents=True)
    for sub in ("chats", "contexts", "skills"):
        (proj_dir / sub).mkdir()

    project_data = {
        "id": project_id,
        "name": "Context Mgmt Test",
        "path": "/test/path",
        "settings": {
            "defaultContextIds": ["ctx-aaa", "ctx-bbb"],
            "defaultSkillIds": ["skill-111"],
        },
        "createdAt": int(time.time() * 1000),
        "lastAccessedAt": int(time.time() * 1000),
    }
    (proj_dir / "project.json").write_text(json.dumps(project_data))
    return project_id


@pytest.fixture
def client(ziya_home, project_with_settings):
    with patch.dict(os.environ, {"ZIYA_HOME": str(ziya_home)}):
        with patch("app.api.projects.get_ziya_home", return_value=ziya_home):
            from fastapi import FastAPI
            from app.api.projects import router

            app = FastAPI()
            app.include_router(router)
            from fastapi.testclient import TestClient
            yield TestClient(app), project_with_settings


class TestContextManagementSettings:

    def test_default_has_no_context_management(self, client):
        """A project created without contextManagement should return None for it."""
        tc, pid = client
        resp = tc.get(f"/api/v1/projects/{pid}")
        assert resp.status_code == 200
        settings = resp.json()["settings"]
        assert settings.get("contextManagement") is None

    def test_update_context_management_preserves_other_settings(self, client):
        """Updating contextManagement must not wipe defaultContextIds."""
        tc, pid = client
        resp = tc.put(f"/api/v1/projects/{pid}", json={
            "settings": {
                "contextManagement": {"auto_add_diff_files": False}
            }
        })
        assert resp.status_code == 200
        settings = resp.json()["settings"]

        # contextManagement should be set
        assert settings["contextManagement"]["auto_add_diff_files"] is False

        # existing settings must survive
        assert settings["defaultContextIds"] == ["ctx-aaa", "ctx-bbb"]
        assert settings["defaultSkillIds"] == ["skill-111"]

    def test_update_context_management_round_trip(self, client):
        """Set, read back, toggle, read back."""
        tc, pid = client

        # Enable (default)
        tc.put(f"/api/v1/projects/{pid}", json={
            "settings": {"contextManagement": {"auto_add_diff_files": True}}
        })
        resp = tc.get(f"/api/v1/projects/{pid}")
        assert resp.json()["settings"]["contextManagement"]["auto_add_diff_files"] is True

        # Disable
        tc.put(f"/api/v1/projects/{pid}", json={
            "settings": {"contextManagement": {"auto_add_diff_files": False}}
        })
        resp = tc.get(f"/api/v1/projects/{pid}")
        assert resp.json()["settings"]["contextManagement"]["auto_add_diff_files"] is False

        # Other settings still intact
        assert resp.json()["settings"]["defaultContextIds"] == ["ctx-aaa", "ctx-bbb"]

    def test_update_name_preserves_settings(self, client):
        """Updating just the project name must not wipe settings."""
        tc, pid = client

        # First set contextManagement
        tc.put(f"/api/v1/projects/{pid}", json={
            "settings": {"contextManagement": {"auto_add_diff_files": False}}
        })

        # Then update only the name
        resp = tc.put(f"/api/v1/projects/{pid}", json={"name": "Renamed Project"})
        assert resp.status_code == 200
        assert resp.json()["name"] == "Renamed Project"

        # Settings must be fully intact
        settings = resp.json()["settings"]
        assert settings["defaultContextIds"] == ["ctx-aaa", "ctx-bbb"]
        assert settings["contextManagement"]["auto_add_diff_files"] is False
