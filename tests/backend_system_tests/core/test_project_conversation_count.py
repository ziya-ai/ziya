"""
Tests for conversation count in the project list API.

The ProjectManagerModal was showing wrong conversation counts because it relied
on the in-memory conversations array (which only contains the current project's
conversations). The fix moves the counting to the backend list_projects endpoint
which counts chat JSON files on disk per project.
"""
import json
import os
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest


def _create_project(projects_dir: Path, project_id: str, name: str, path: str, num_chats: int = 0):
    """Helper: create a project directory with N chat files."""
    project_dir = projects_dir / project_id
    project_dir.mkdir(parents=True, exist_ok=True)
    
    project_data = {
        "id": project_id,
        "name": name,
        "path": path,
        "createdAt": 1000000,
        "lastAccessedAt": 2000000,
        "settings": {"defaultContextIds": [], "defaultSkillIds": []},
    }
    (project_dir / "project.json").write_text(json.dumps(project_data))
    
    chats_dir = project_dir / "chats"
    chats_dir.mkdir(exist_ok=True)
    
    # Write the internal _groups.json (should NOT be counted)
    (chats_dir / "_groups.json").write_text(json.dumps([]))
    
    # Write N actual chat files
    for i in range(num_chats):
        chat_id = f"chat-{project_id}-{i}"
        chat_data = {
            "id": chat_id,
            "title": f"Chat {i}",
            "messages": [],
            "lastActiveAt": 2000000 + i,
        }
        (chats_dir / f"{chat_id}.json").write_text(json.dumps(chat_data))


class TestCountChats:
    """Test the _count_chats helper directly."""

    def test_empty_directory(self, tmp_path):
        from app.api.projects import _count_chats
        chats_dir = tmp_path / "chats"
        chats_dir.mkdir()
        assert _count_chats(chats_dir) == 0

    def test_nonexistent_directory(self, tmp_path):
        from app.api.projects import _count_chats
        assert _count_chats(tmp_path / "does_not_exist") == 0

    def test_counts_json_files_only(self, tmp_path):
        from app.api.projects import _count_chats
        chats_dir = tmp_path / "chats"
        chats_dir.mkdir()
        (chats_dir / "chat1.json").write_text("{}")
        (chats_dir / "chat2.json").write_text("{}")
        (chats_dir / "readme.txt").write_text("not a chat")
        assert _count_chats(chats_dir) == 2

    def test_excludes_underscore_prefixed_files(self, tmp_path):
        from app.api.projects import _count_chats
        chats_dir = tmp_path / "chats"
        chats_dir.mkdir()
        (chats_dir / "chat1.json").write_text("{}")
        (chats_dir / "_groups.json").write_text("[]")
        (chats_dir / "_internal.json").write_text("{}")
        assert _count_chats(chats_dir) == 1

    def test_multiple_chats(self, tmp_path):
        from app.api.projects import _count_chats
        chats_dir = tmp_path / "chats"
        chats_dir.mkdir()
        for i in range(25):
            (chats_dir / f"chat-{i}.json").write_text("{}")
        assert _count_chats(chats_dir) == 25


class TestListProjectsConversationCount:
    """Test that the list_projects endpoint returns correct conversationCount."""

    @pytest.mark.asyncio
    async def test_conversation_counts_per_project(self, tmp_path):
        """Each project should report its own chat file count."""
        projects_dir = tmp_path / "projects"
        _create_project(projects_dir, "proj-a", "Project A", "/tmp/a", num_chats=5)
        _create_project(projects_dir, "proj-b", "Project B", "/tmp/b", num_chats=42)
        _create_project(projects_dir, "proj-c", "Project C", "/tmp/c", num_chats=0)

        from app.api.projects import list_projects, get_project_storage
        from app.storage.projects import ProjectStorage

        with patch("app.api.projects.get_project_storage") as mock_storage, \
             patch("app.api.projects.get_ziya_home", return_value=tmp_path), \
             patch("os.getcwd", return_value="/tmp/a"):
            storage = ProjectStorage(tmp_path)
            mock_storage.return_value = storage
            result = await list_projects()

        counts = {item.name: item.conversationCount for item in result}
        assert counts["Project A"] == 5
        assert counts["Project B"] == 42
        assert counts["Project C"] == 0

    @pytest.mark.asyncio
    async def test_no_chats_directory(self, tmp_path):
        """A project with no chats/ subdirectory should report 0."""
        projects_dir = tmp_path / "projects"
        project_dir = projects_dir / "proj-no-chats"
        project_dir.mkdir(parents=True)
        project_data = {
            "id": "proj-no-chats",
            "name": "No Chats",
            "path": "/tmp/nc",
            "createdAt": 1000000,
            "lastAccessedAt": 2000000,
            "settings": {"defaultContextIds": [], "defaultSkillIds": []},
        }
        (project_dir / "project.json").write_text(json.dumps(project_data))

        from app.api.projects import list_projects
        from app.storage.projects import ProjectStorage

        with patch("app.api.projects.get_project_storage") as mock_storage, \
             patch("app.api.projects.get_ziya_home", return_value=tmp_path), \
             patch("os.getcwd", return_value="/tmp/nc"):
            storage = ProjectStorage(tmp_path)
            mock_storage.return_value = storage
            result = await list_projects()

        assert len(result) == 1
        assert result[0].conversationCount == 0
