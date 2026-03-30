"""
Tests for fast project startup optimizations:
- Path index for O(1) get_by_path lookups
- /projects/last-accessed endpoint
"""
import json
from pathlib import Path
from unittest.mock import patch

import pytest


def _create_project(projects_dir: Path, project_id: str, name: str, path: str,
                     last_accessed: int = 2000000):
    """Helper: create a project directory with a project.json."""
    project_dir = projects_dir / project_id
    project_dir.mkdir(parents=True, exist_ok=True)
    project_data = {
        "id": project_id,
        "name": name,
        "path": path,
        "createdAt": 1000000,
        "lastAccessedAt": last_accessed,
        "settings": {"defaultContextIds": [], "defaultSkillIds": []},
    }
    (project_dir / "project.json").write_text(json.dumps(project_data))
    (project_dir / "chats").mkdir(exist_ok=True)


class TestPathIndex:
    """Test the path→project_id index in ProjectStorage."""

    def test_get_by_path_uses_index(self, tmp_path):
        """get_by_path should use the index for fast lookups."""
        from app.storage.projects import ProjectStorage
        projects_dir = tmp_path / "projects"
        _create_project(projects_dir, "proj-1", "Alpha", "/home/user/alpha")
        _create_project(projects_dir, "proj-2", "Beta", "/home/user/beta")

        storage = ProjectStorage(tmp_path)
        # First call builds the index
        result = storage.get_by_path("/home/user/alpha")
        assert result is not None
        assert result.id == "proj-1"

        # Verify index file was created
        index_file = projects_dir / "_path_index.json"
        assert index_file.exists()

    def test_get_by_path_returns_none_for_unknown(self, tmp_path):
        from app.storage.projects import ProjectStorage
        projects_dir = tmp_path / "projects"
        _create_project(projects_dir, "proj-1", "Alpha", "/home/user/alpha")

        storage = ProjectStorage(tmp_path)
        result = storage.get_by_path("/nonexistent/path")
        assert result is None

    def test_create_updates_index(self, tmp_path):
        from app.storage.projects import ProjectStorage
        from app.models.project import ProjectCreate

        storage = ProjectStorage(tmp_path)
        project = storage.create(ProjectCreate(path="/home/user/new_project"))

        index = storage._load_index()
        normalized = str(Path("/home/user/new_project").resolve())
        assert normalized in index
        assert index[normalized] == project.id

    def test_delete_cleans_index(self, tmp_path):
        from app.storage.projects import ProjectStorage
        from app.models.project import ProjectCreate

        storage = ProjectStorage(tmp_path)
        project = storage.create(ProjectCreate(path="/home/user/deleteme"))
        normalized = str(Path("/home/user/deleteme").resolve())

        # Verify it's in the index
        assert normalized in storage._load_index()

        # Delete
        storage.delete(project.id)
        assert normalized not in storage._load_index()

    def test_index_rebuilt_on_stale_entry(self, tmp_path):
        """If index points to a deleted project, rebuild should fix it."""
        from app.storage.projects import ProjectStorage
        projects_dir = tmp_path / "projects"
        _create_project(projects_dir, "proj-1", "Alpha", "/home/user/alpha")

        storage = ProjectStorage(tmp_path)
        # Write a stale index entry pointing to nonexistent project
        storage._save_index({str(Path("/home/user/alpha").resolve()): "ghost-id"})

        # get_by_path should detect the stale entry and rebuild
        result = storage.get_by_path("/home/user/alpha")
        assert result is not None
        assert result.id == "proj-1"


class TestLastAccessedEndpoint:
    """Test the /projects/last-accessed API endpoint."""

    @pytest.mark.asyncio
    async def test_returns_most_recent_project(self, tmp_path):
        projects_dir = tmp_path / "projects"
        _create_project(projects_dir, "old", "Old Project", "/tmp/old", last_accessed=1000)
        _create_project(projects_dir, "new", "New Project", "/tmp/new", last_accessed=9000)
        _create_project(projects_dir, "mid", "Mid Project", "/tmp/mid", last_accessed=5000)

        from app.api.projects import get_last_accessed_project
        from app.storage.projects import ProjectStorage

        with patch("app.api.projects.get_project_storage") as mock_storage:
            mock_storage.return_value = ProjectStorage(tmp_path)
            result = await get_last_accessed_project()

        assert result.id == "new"
        assert result.name == "New Project"

    @pytest.mark.asyncio
    async def test_creates_cwd_project_when_none_exist(self, tmp_path):
        projects_dir = tmp_path / "projects"
        projects_dir.mkdir(parents=True, exist_ok=True)

        from app.api.projects import get_last_accessed_project
        from app.storage.projects import ProjectStorage

        with patch("app.api.projects.get_project_storage") as mock_storage, \
             patch("os.getcwd", return_value="/tmp/brand_new"):
            mock_storage.return_value = ProjectStorage(tmp_path)
            result = await get_last_accessed_project()

        assert result.path == str(Path("/tmp/brand_new").resolve())

    @pytest.mark.asyncio
    async def test_touches_returned_project(self, tmp_path):
        """Returned project should have its access time updated."""
        import time
        projects_dir = tmp_path / "projects"
        _create_project(projects_dir, "proj-1", "My Project", "/tmp/mine", last_accessed=1000)

        from app.api.projects import get_last_accessed_project
        from app.storage.projects import ProjectStorage

        before = int(time.time() * 1000)
        with patch("app.api.projects.get_project_storage") as mock_storage:
            mock_storage.return_value = ProjectStorage(tmp_path)
            result = await get_last_accessed_project()

        # touch() should have updated lastAccessedAt to a recent timestamp
        assert result.id == "proj-1"
        # Re-read from disk to verify touch persisted
        storage = ProjectStorage(tmp_path)
        refreshed = storage.get("proj-1")
        assert refreshed.lastAccessedAt >= before
