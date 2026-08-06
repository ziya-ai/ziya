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

    def test_refuses_file_over_token_limit(self, chat_env, tmp_path):
        """A file over auto_add_token_limit is refused, not stored.

        Supersedes the previous test_truncates_large_inline_content, which
        used a 65KB file to reach the inline-truncation path.  That file is
        ~16k estimated tokens, over the 12500 default limit, so it is now
        refused before the read — the intended behaviour, since a stored
        file is re-sent on every subsequent turn.
        """
        from app.mcp.tools.context_management import _MAX_INLINE_BYTES
        big = Path(chat_env["project_root"]) / "big.txt"
        big.write_text("x" * (_MAX_INLINE_BYTES + 1024))
        tool = ContextAddFileTool()
        result = run(tool.execute(path="big.txt"))
        assert result["error"] is True
        assert result["limit_exceeded"] is True
        assert result["estimated_tokens"] > result["token_limit"]
        # Refusal must not persist anything — the whole point is that an
        # oversized file never enters the store.
        chat = json.loads(Path(chat_env["chat_file"]).read_text())
        assert "big.txt" not in chat["additionalFiles"]
        assert "big.txt" not in (chat.get(_OWNERSHIP_FIELD) or [])
        # And the message must tell the model what to do instead.
        assert "file_read" in result["message"]

    def test_truncates_large_inline_content_when_limit_raised(self, chat_env, tmp_path):
        """The inline-truncation path, reachable only above the token limit.

        _MAX_INLINE_BYTES (64KB) and auto_add_token_limit (12500 tokens,
        ~50KB) were chosen independently, and the limit is the stricter of
        the two — so with default settings the content_truncated branch is
        dead code.  Raising the limit is the only way to exercise it.
        """
        from app.mcp.tools.context_management import _MAX_INLINE_BYTES
        big = Path(chat_env["project_root"]) / "big.txt"
        big.write_text("x" * (_MAX_INLINE_BYTES + 1024))
        tool = ContextAddFileTool()
        with patch("app.utils.chat_context_files.resolve_auto_add_token_limit",
                   return_value=0):  # 0 disables the limit
            result = run(tool.execute(path="big.txt"))
        assert result["success"] is True
        assert result["content_truncated"] is True
        assert len(result["content"]) == _MAX_INLINE_BYTES
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

class TestContextAddFileExternalPath:
    """Regression coverage for the 4th call site of the same bug: an
    absolute path outside the project root -- approved via Project
    Settings' 'Add External Path' feature or --include/
    ZIYA_INCLUDE_DIRS -- must be usable with context_add_file, not just
    file_read/file_list/pdf_*.  _validate_relative_path now builds its
    allowlist from _get_all_readable_prefixes()."""

    @pytest.fixture
    def external_file(self, tmp_path):
        ext_dir = tmp_path.parent / f"external-{tmp_path.name}"
        ext_dir.mkdir(exist_ok=True)
        f = ext_dir / "vendor-spec.txt"
        f.write_text("VENDOR SPEC CONTENT\n")
        return str(f), str(ext_dir)

    def test_rejects_unapproved_external_path(self, chat_env, external_file, monkeypatch):
        ext_path, _ext_dir = external_file
        monkeypatch.delenv("ZIYA_INCLUDE_DIRS", raising=False)
        monkeypatch.setattr(
            "app.services.folder_service._explicit_external_paths", set(), raising=False
        )
        result = run(ContextAddFileTool().execute(path=ext_path))
        assert result.get("error") is True

    def test_allows_approved_external_path(self, chat_env, external_file, monkeypatch):
        ext_path, ext_dir = external_file
        monkeypatch.delenv("ZIYA_INCLUDE_DIRS", raising=False)
        monkeypatch.setattr(
            "app.services.folder_service._explicit_external_paths",
            {ext_dir},
            raising=False,
        )
        result = run(ContextAddFileTool().execute(path=ext_path))
        assert result.get("error") is not True, result
        assert result.get("success") is True
        assert "VENDOR SPEC CONTENT" in result["content"]
        chat = _read_chat(chat_env)
        assert ext_path in chat["additionalFiles"]
        assert ext_path in chat[_OWNERSHIP_FIELD]

    def test_allows_include_dirs_env_var_path(self, chat_env, external_file, monkeypatch):
        ext_path, ext_dir = external_file
        monkeypatch.setattr(
            "app.services.folder_service._explicit_external_paths", set(), raising=False
        )
        monkeypatch.setenv("ZIYA_INCLUDE_DIRS", ext_dir)
        result = run(ContextAddFileTool().execute(path=ext_path))
        assert result.get("error") is not True, result
        assert "VENDOR SPEC CONTENT" in result["content"]


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


# ── _resolve_chat_for_request auto-vivification (CLI root-cause fix) ────
#
# Regression coverage for the bug where a CLI session's synthetic
# conversation_id (cli_<timestamp>_<pid>) — and any brand-new web
# conversation that hasn't synced yet — was never persisted via
# ChatStorage.create() ahead of a tool call, so _resolve_chat_for_request
# unconditionally returned "not found" and the tools could never succeed
# from the CLI. Both the missing-project and missing-chat branches now
# self-heal by auto-vivifying a minimal record instead of erroring.

@pytest.fixture
def unregistered_env(tmp_path, monkeypatch):
    """
    A project root that ProjectStorage has never seen, and a
    conversation_id with no chat file on disk — mirrors a CLI session
    hitting a context tool for the very first time.
    """
    project_root = tmp_path / "project"
    project_root.mkdir()
    (project_root / "src").mkdir()
    (project_root / "src" / "main.py").write_text("line1\nline2\n")

    ziya_home = tmp_path / "ziya_home"
    (ziya_home / "projects").mkdir(parents=True)

    monkeypatch.setattr("app.utils.paths.get_ziya_home", lambda: ziya_home)
    monkeypatch.setattr(
        "app.mcp.tools.context_management.get_ziya_home", lambda: ziya_home,
        raising=False,
    )

    conversation_id = f"cli_20260708_{os.urandom(3).hex()}"
    from app.context import set_conversation_id, set_project_root
    set_conversation_id(conversation_id)
    set_project_root(str(project_root.resolve()))

    monkeypatch.setattr(
        "app.plugins.data_retention.get_retention_enforcer",
        lambda: type("X", (), {"is_expired": lambda *a, **kw: False})(),
        raising=False,
    )

    return {
        "project_root": str(project_root.resolve()),
        "ziya_home": ziya_home,
        "conversation_id": conversation_id,
    }


class TestAutoVivifyProjectAndChat:

    def test_context_add_file_succeeds_on_first_call_from_unregistered_state(self, unregistered_env):
        """This is the exact CLI failure mode: no project record, no chat
        record. context_add_file must succeed on the first call instead
        of returning 'not found' / 'not registered'."""
        result = run(ContextAddFileTool().execute(path="src/main.py"))
        assert result.get("error") is not True, result
        assert result.get("success") is True
        assert "line1" in result["content"]

    def test_project_is_registered_on_disk_after_auto_vivify(self, unregistered_env):
        run(ContextAddFileTool().execute(path="src/main.py"))

        from app.storage.projects import ProjectStorage
        storage = ProjectStorage(unregistered_env["ziya_home"])
        project = storage.get_by_path(unregistered_env["project_root"])
        assert project is not None
        assert project.path == unregistered_env["project_root"]

    def test_project_auto_registration_is_idempotent(self, unregistered_env):
        """Two tool calls in a row must not create two project records
        for the same path (ProjectStorage.create() is idempotent by path,
        this just confirms that guarantee actually holds through our
        call site)."""
        run(ContextAddFileTool().execute(path="src/main.py"))
        run(ContextListFilesTool().execute())

        from app.storage.projects import ProjectStorage
        storage = ProjectStorage(unregistered_env["ziya_home"])
        projects = [p for p in storage.list() if p.path == unregistered_env["project_root"]]
        assert len(projects) == 1

    def test_chat_file_is_created_at_exact_conversation_id(self, unregistered_env):
        """The auto-vivified chat must be created at the SAME
        conversation_id the caller used — not a fresh uuid, which would
        silently start an invisible second chat record on the next call
        in the same conversation."""
        run(ContextAddFileTool().execute(path="src/main.py"))

        from app.storage.projects import ProjectStorage
        from app.storage.chats import ChatStorage
        from app.utils.paths import get_project_dir

        pstorage = ProjectStorage(unregistered_env["ziya_home"])
        project = pstorage.get_by_path(unregistered_env["project_root"])
        chat_file = get_project_dir(project.id) / "chats" / f"{unregistered_env['conversation_id']}.json"
        assert chat_file.exists()
        chat_data = json.loads(chat_file.read_text())
        assert chat_data["id"] == unregistered_env["conversation_id"]

    def test_second_call_in_same_conversation_reuses_the_same_chat_file(self, unregistered_env):
        """Two calls in the same conversation must resolve to the SAME
        chat file/record, not vivify a new one each time (which would
        silently drop the first call's context_add_file)."""
        run(ContextAddFileTool().execute(path="src/main.py"))
        result = run(ContextListFilesTool().execute())
        assert result["count"] == 1
        assert result["files"][0]["path"] == "src/main.py"

    def test_auto_vivified_chat_is_registered_in_chat_index(self, unregistered_env):
        """The auto-vivified chat must be discoverable via the
        cross-project chat_index, the same as a chat created through the
        normal ChatStorage.create() path — otherwise it exists on disk
        but is invisible to cross-project bulk-get lookups."""
        from app.storage import chat_index
        chat_index.invalidate()

        run(ContextAddFileTool().execute(path="src/main.py"))

        from app.storage.projects import ProjectStorage
        pstorage = ProjectStorage(unregistered_env["ziya_home"])
        project = pstorage.get_by_path(unregistered_env["project_root"])

        resolved, missing = chat_index.lookup_many(
            unregistered_env["ziya_home"], [unregistered_env["conversation_id"]]
        )
        assert unregistered_env["conversation_id"] in resolved
        assert unregistered_env["conversation_id"] not in missing

    def test_cli_style_conversation_id_gets_cli_session_title(self, unregistered_env):
        """conversation_ids prefixed cli_ get a distinguishable title so
        they're recognizable in any chat listing, instead of the generic
        'New conversation' title used for un-synced web chats."""
        run(ContextAddFileTool().execute(path="src/main.py"))

        from app.storage.projects import ProjectStorage
        from app.utils.paths import get_project_dir

        pstorage = ProjectStorage(unregistered_env["ziya_home"])
        project = pstorage.get_by_path(unregistered_env["project_root"])
        chat_file = get_project_dir(project.id) / "chats" / f"{unregistered_env['conversation_id']}.json"
        chat_data = json.loads(chat_file.read_text())
        assert chat_data["title"] == "CLI session"

    def test_context_remove_file_works_end_to_end_from_unregistered_state(self, unregistered_env):
        """The full add-then-remove cycle must work from a cold start,
        not just context_add_file in isolation."""
        run(ContextAddFileTool().execute(path="src/main.py"))
        result = run(ContextRemoveFileTool().execute(path="src/main.py"))
        assert result.get("success") is True

        list_result = run(ContextListFilesTool().execute())
        assert list_result["count"] == 0

    def test_context_list_files_alone_auto_vivifies_and_returns_empty(self, unregistered_env):
        """Calling context_list_files first (before any add) must not
        error — it should auto-vivify an empty chat record and report
        zero files, not 'chat not found'."""
        result = run(ContextListFilesTool().execute())
        assert result.get("error") is not True, result
        assert result["count"] == 0
