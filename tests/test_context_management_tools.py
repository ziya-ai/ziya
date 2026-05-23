"""
Tests for app.mcp.tools.context_management — model-driven context tools.

Covers the three tools end-to-end against a real ChatStorage instance,
with the request-scoped ContextVars (conversation_id, project_root)
stubbed via app.context.
"""

import asyncio
import json
import os
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from app.mcp.tools.context_management import (
    ContextAddFileTool,
    ContextRemoveFileTool,
    ContextListFilesTool,
    _OWNERSHIP_FIELD,
)


def run(coro):
    """Run an async function synchronously."""
    return asyncio.get_event_loop().run_until_complete(coro)


@pytest.fixture
def chat_env(tmp_path, monkeypatch):
    """
    Build a working environment:
      - tmp_path / "project"  → project files (workspace)
      - tmp_path / "ziya_home" → ziya home with one project + one chat
      - patches get_ziya_home and the request-scoped ContextVars
    """
    project_root = tmp_path / "project"
    project_root.mkdir()
    (project_root / "src").mkdir()
    (project_root / "src" / "main.py").write_text("line1\nline2\nline3\n")
    (project_root / "README.md").write_text("# Hello\n")

    ziya_home = tmp_path / "ziya_home"
    projects_dir = ziya_home / "projects"
    projects_dir.mkdir(parents=True)

    project_id = "p_test_" + os.urandom(4).hex()
    project_dir = projects_dir / project_id
    project_dir.mkdir()

    project_record = {
        "id": project_id,
        "name": "TestProject",
        "path": str(project_root.resolve()),
        "createdAt": int(time.time() * 1000),
        "lastAccessedAt": int(time.time() * 1000),
        "settings": {"defaultContextIds": [], "defaultSkillIds": []},
    }
    (project_dir / "project.json").write_text(json.dumps(project_record))

    # Path index (ProjectStorage uses this for O(1) lookup by path)
    (projects_dir / "_path_index.json").write_text(
        json.dumps({str(project_root.resolve()): project_id})
    )

    # One chat
    chats_dir = project_dir / "chats"
    chats_dir.mkdir()
    chat_id = "c_test_" + os.urandom(4).hex()
    chat_record = {
        "id": chat_id,
        "title": "Test chat",
        "groupId": None,
        "contextIds": [],
        "skillIds": [],
        "additionalFiles": ["existing/user-pinned.txt"],
        "additionalPrompt": None,
        "messages": [],
        "createdAt": int(time.time() * 1000),
        "lastActiveAt": int(time.time() * 1000),
    }
    (chats_dir / f"{chat_id}.json").write_text(json.dumps(chat_record))

    # Patch get_ziya_home in both call sites
    monkeypatch.setattr("app.utils.paths.get_ziya_home", lambda: ziya_home)
    monkeypatch.setattr(
        "app.mcp.tools.context_management.get_ziya_home", lambda: ziya_home,
        raising=False,
    )

    # Stub the request-scoped ContextVars
    from app.context import set_conversation_id, set_project_root
    set_conversation_id(chat_id)
    set_project_root(str(project_root.resolve()))

    # Disable retention enforcement so tests don't trip over it
    monkeypatch.setattr(
        "app.plugins.data_retention.get_retention_enforcer",
        lambda: type("X", (), {"is_expired": lambda *a, **kw: False})(),
        raising=False,
    )

    return {
        "project_root": str(project_root.resolve()),
        "ziya_home": ziya_home,
        "project_id": project_id,
        "chat_id": chat_id,
        "chat_file": chats_dir / f"{chat_id}.json",
    }


def _read_chat(env):
    return json.loads(env["chat_file"].read_text())


# ── ContextAddFileTool ──────────────────────────────────────────────

class TestContextAddFile:

    def test_adds_file_and_returns_inline_content(self, chat_env):
        tool = ContextAddFileTool()
        result = run(tool.execute(path="src/main.py"))
        assert result.get("success") is True
        assert result["path"] == "src/main.py"
        assert "content" in result
        assert "line1" in result["content"]
        # Persisted to chat record
        chat = _read_chat(chat_env)
        assert "src/main.py" in chat["additionalFiles"]
        assert "src/main.py" in chat[_OWNERSHIP_FIELD]
        # User-pinned file is preserved
        assert "existing/user-pinned.txt" in chat["additionalFiles"]

    def test_no_op_when_already_in_context(self, chat_env):
        tool = ContextAddFileTool()
        run(tool.execute(path="src/main.py"))
        result = run(tool.execute(path="src/main.py"))
        assert result.get("success") is True
        assert result.get("already_in_context") is True
        # Still appears exactly once
        chat = _read_chat(chat_env)
        assert chat["additionalFiles"].count("src/main.py") == 1

    def test_rejects_path_traversal(self, chat_env):
        tool = ContextAddFileTool()
        result = run(tool.execute(path="../../../etc/passwd"))
        assert result.get("error") is True
        assert "traversal" in result["message"].lower()

    def test_rejects_missing_file(self, chat_env):
        tool = ContextAddFileTool()
        result = run(tool.execute(path="does/not/exist.txt"))
        assert result.get("error") is True
        assert "not found" in result["message"].lower()

    def test_rejects_empty_path(self, chat_env):
        tool = ContextAddFileTool()
        result = run(tool.execute(path=""))
        assert result.get("error") is True

    def test_truncates_large_inline_content(self, chat_env, tmp_path):
        # Create a file just over the inline limit
        from app.mcp.tools.context_management import _MAX_INLINE_BYTES
        big = Path(chat_env["project_root"]) / "big.txt"
        big.write_text("x" * (_MAX_INLINE_BYTES + 1024))
        tool = ContextAddFileTool()
        result = run(tool.execute(path="big.txt"))
        assert result["success"] is True
        assert result.get("content_truncated") is True
        assert len(result["content"]) <= _MAX_INLINE_BYTES

    def test_no_conversation_id_returns_error(self, chat_env):
        from app.context import _request_conversation_id
        token = _request_conversation_id.set(None)
        try:
            tool = ContextAddFileTool()
            result = run(tool.execute(path="src/main.py"))
            assert result.get("error") is True
            assert "conversation_id" in result["message"].lower()
        finally:
            _request_conversation_id.reset(token)


# ── ContextRemoveFileTool ───────────────────────────────────────────

class TestContextRemoveFile:

    def test_removes_model_added_file(self, chat_env):
        # Model adds, then removes
        run(ContextAddFileTool().execute(path="src/main.py"))
        result = run(ContextRemoveFileTool().execute(path="src/main.py"))
        assert result.get("success") is True
        assert result["path"] == "src/main.py"
        chat = _read_chat(chat_env)
        assert "src/main.py" not in chat["additionalFiles"]
        assert "src/main.py" not in chat[_OWNERSHIP_FIELD]

    def test_refuses_to_remove_user_pinned_file(self, chat_env):
        result = run(
            ContextRemoveFileTool().execute(path="existing/user-pinned.txt")
        )
        assert result.get("error") is True
        # User-pinned file is still there
        chat = _read_chat(chat_env)
        assert "existing/user-pinned.txt" in chat["additionalFiles"]

    def test_refuses_unknown_file(self, chat_env):
        result = run(ContextRemoveFileTool().execute(path="not/in/context.py"))
        assert result.get("error") is True
        assert "not in the conversation context" in result["message"].lower()


# ── ContextListFilesTool ────────────────────────────────────────────

class TestContextListFiles:

    def test_lists_with_ownership_tags(self, chat_env):
        # Start: only the user-pinned entry
        run(ContextAddFileTool().execute(path="src/main.py"))
        run(ContextAddFileTool().execute(path="README.md"))
        result = run(ContextListFilesTool().execute())
        assert result.get("success") is True
        assert result["count"] == 3
        by_path = {f["path"]: f for f in result["files"]}
        assert by_path["src/main.py"]["owner"] == "model"
        assert by_path["src/main.py"]["removable"] is True
        assert by_path["README.md"]["owner"] == "model"
        assert by_path["existing/user-pinned.txt"]["owner"] == "user"
        assert by_path["existing/user-pinned.txt"]["removable"] is False

    def test_empty_list_for_fresh_chat(self, chat_env):
        # Strip everything
        chat = _read_chat(chat_env)
        chat["additionalFiles"] = []
        chat[_OWNERSHIP_FIELD] = []
        chat_env["chat_file"].write_text(json.dumps(chat))
        result = run(ContextListFilesTool().execute())
        assert result["count"] == 0
        assert result["files"] == []


# ── Builtin registration ─────────────────────────────────────────────

def test_category_registered_in_builtin_tools():
    """The context_management category exists and exposes all three tools."""
    from app.mcp.builtin_tools import (
        BUILTIN_TOOL_CATEGORIES,
        get_builtin_tools_for_category,
    )
    assert "context_management" in BUILTIN_TOOL_CATEGORIES
    tools = get_builtin_tools_for_category("context_management")
    names = sorted(t().name for t in tools)
    assert names == sorted([
        "context_add_file", "context_remove_file", "context_list_files",
    ])
