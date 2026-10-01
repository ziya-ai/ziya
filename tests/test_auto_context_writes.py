"""Files a conversation writes are auto-added to its context, or tracked.

THE DEFECT THIS EXISTS FOR
--------------------------
2026-09-24: this conversation's diff to pyproject.toml was applied twice and
the conversation never learned the file existed -- no change markers, and the
model reported the (already fixed) file as broken.  Neither write path told the
conversation anything: the apply button's request carries no conversation_id,
and the ``file_write`` tool never touches the chat record.

Phase 1 of design/auto-context-tiers.md:
  * a successful write of a file under ``auto_add_token_limit`` adds it to the
    chat's ``additionalFiles`` with owner ``auto`` (``_autoAddedFiles``), so it
    reaches the next prompt through the existing model-pinned merge;
  * a larger file is NOT added; it is recorded in ``_autoTrackedFiles`` (hashes,
    no content) and the model is told each turn that it exists and whether it
    changed on disk since this conversation last wrote it;
  * user-pinned files keep their owner; scratch paths (.ziya/, /tmp) are
    ignored; ``context_remove_file`` may remove ``auto`` files.

Every test drives a real entry point and asserts on the chat record or the
assembled prompt, not on an intermediate this file built.
"""
import asyncio
import json
import os
import time
from pathlib import Path

import pytest

from tests.test_context_management_tools import chat_env, _read_chat  # noqa: F401  (fixture)


def run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


@pytest.fixture
def writable(monkeypatch):
    """Let file_write write anywhere in the temp project (policy is not under test)."""
    monkeypatch.setattr("app.mcp.tools.fileio._check_write_allowed",
                        lambda *a, **kw: None)


@pytest.fixture
def small_limit(monkeypatch):
    """Make the size tier boundary tiny so 'large' is cheap to produce."""
    monkeypatch.setattr("app.utils.chat_context_files.resolve_auto_add_token_limit",
                        lambda *a, **kw: 20)


def _file_write(**kw):
    from app.mcp.tools.fileio import FileWriteTool
    return run(FileWriteTool().execute(**kw))


def _auto(chat):
    return chat.get("_autoAddedFiles") or []


def _tracked(chat):
    return chat.get("_autoTrackedFiles") or {}


# -- file_write ------------------------------------------------------------

class TestFileWrite:
    def test_small_write_is_auto_added(self, chat_env, writable):
        r = _file_write(path="notes.md", content="# notes\n")
        assert r.get("success"), r
        chat = _read_chat(chat_env)
        assert "notes.md" in chat["additionalFiles"]
        assert "notes.md" in _auto(chat)
        assert "notes.md" not in _tracked(chat)
        # the tool result tells the frontend what happened
        assert r.get("auto_context", {}).get("tier") == "included"

    def test_auto_added_file_reaches_the_prompt_merge(self, chat_env, writable):
        from app.utils.chat_context_files import get_model_pinned_files
        _file_write(path="notes.md", content="# notes\n")
        assert "notes.md" in get_model_pinned_files(chat_env["chat_id"])

    def test_large_write_is_tracked_not_added(self, chat_env, writable, small_limit):
        r = _file_write(path="big.md", content="word " * 400)
        assert r.get("success"), r
        chat = _read_chat(chat_env)
        assert "big.md" not in chat["additionalFiles"]
        assert "big.md" not in _auto(chat)
        entry = _tracked(chat)["big.md"]
        assert entry["tokens"] > 20
        assert entry["source"] == "file_write"
        assert entry["written_hash"] and entry["baseline_hash"] is None  # new file
        assert r.get("auto_context", {}).get("tier") == "tracked"

    def test_patch_mode_write_counts(self, chat_env, writable):
        r = _file_write(path="README.md", patch="# Hello", content="# Hi")
        assert r.get("success"), r
        chat = _read_chat(chat_env)
        assert "README.md" in _auto(chat)

    def test_existing_file_baseline_is_the_pre_write_content(self, chat_env, writable, small_limit):
        import hashlib
        p = Path(chat_env["project_root"]) / "big.md"
        p.write_text("old " * 400)
        before = hashlib.sha256(p.read_bytes()).hexdigest()
        _file_write(path="big.md", content="new " * 400)
        entry = _tracked(_read_chat(chat_env))["big.md"]
        assert entry["baseline_hash"] == before
        assert entry["written_hash"] == hashlib.sha256(p.read_bytes()).hexdigest()

    def test_user_pinned_file_keeps_user_ownership(self, chat_env, writable):
        p = Path(chat_env["project_root"]) / "existing" / "user-pinned.txt"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("x\n")
        _file_write(path="existing/user-pinned.txt", content="y\n")
        chat = _read_chat(chat_env)
        assert chat["additionalFiles"].count("existing/user-pinned.txt") == 1
        assert "existing/user-pinned.txt" not in _auto(chat)

    def test_scratch_write_is_ignored(self, chat_env, writable):
        r = _file_write(path=".ziya/scratch.md", content="x\n")
        assert r.get("success"), r
        chat = _read_chat(chat_env)
        assert not any(".ziya" in f for f in chat["additionalFiles"])
        assert not _tracked(chat)

    def test_growing_past_the_limit_does_not_duplicate(self, chat_env, writable, monkeypatch):
        """Included first, then rewritten large: stays included (phase 1 has
        no demotion), and is not also tracked."""
        _file_write(path="grow.md", content="small\n")
        monkeypatch.setattr("app.utils.chat_context_files.resolve_auto_add_token_limit",
                            lambda *a, **kw: 20)
        _file_write(path="grow.md", content="word " * 400)
        chat = _read_chat(chat_env)
        assert chat["additionalFiles"].count("grow.md") == 1
        assert "grow.md" not in _tracked(chat)

    def test_tracked_file_that_shrinks_is_promoted(self, chat_env, writable, monkeypatch):
        monkeypatch.setattr("app.utils.chat_context_files.resolve_auto_add_token_limit",
                            lambda *a, **kw: 20)
        _file_write(path="shrink.md", content="word " * 400)
        _file_write(path="shrink.md", content="tiny\n")
        chat = _read_chat(chat_env)
        assert "shrink.md" in _auto(chat)
        assert "shrink.md" not in _tracked(chat)


# -- the per-turn notice for tracked files ---------------------------------

class TestTrackedNotice:
    def test_notice_reports_unchanged_then_changed(self, chat_env, writable, small_limit):
        from app.utils.auto_context import format_tracked_files_notice
        _file_write(path="big.md", content="word " * 400)
        n1 = format_tracked_files_notice(chat_env["chat_id"], chat_env["project_root"])
        assert "big.md" in n1 and "unchanged since" in n1
        (Path(chat_env["project_root"]) / "big.md").write_text("edited " * 400)
        n2 = format_tracked_files_notice(chat_env["chat_id"], chat_env["project_root"])
        assert "big.md" in n2 and "CHANGED on disk" in n2
        # content is never included, only its existence and state
        assert "edited" not in n2 and "word word" not in n1

    def test_notice_reports_deleted_file(self, chat_env, writable, small_limit):
        from app.utils.auto_context import format_tracked_files_notice
        _file_write(path="big.md", content="word " * 400)
        (Path(chat_env["project_root"]) / "big.md").unlink()
        n = format_tracked_files_notice(chat_env["chat_id"], chat_env["project_root"])
        assert "big.md" in n and "no longer exists" in n

    def test_notice_empty_when_nothing_tracked(self, chat_env):
        from app.utils.auto_context import format_tracked_files_notice
        assert format_tracked_files_notice(chat_env["chat_id"], chat_env["project_root"]) == ""

    def test_notice_reaches_the_assembled_prompt(self, chat_env, writable, small_limit):
        """End to end: the notice is in what build_messages_for_streaming sends."""
        from app.server import build_messages_for_streaming
        _file_write(path="big.md", content="word " * 400)
        msgs = build_messages_for_streaming("hi", [], [], chat_env["chat_id"])
        blob = json.dumps(msgs, default=str)
        assert "big.md" in blob and "tracked" in blob.lower()


# -- apply-changes route ---------------------------------------------------

class _Req:
    def __init__(self, body):
        self._body = body

    async def json(self):
        return self._body


def _apply(env, conversation_id):
    # A .txt target keeps the pipeline's language validation out of the way;
    # the fixture's src/main.py ("line1\nline2\nline3") is not valid Python.
    from app.routes.diff_routes import apply_changes
    (Path(env["project_root"]) / "src" / "data.txt").write_text("line1\nline2\nline3\n")
    diff = ("diff --git a/src/data.txt b/src/data.txt\n--- a/src/data.txt\n+++ b/src/data.txt\n"
            "@@ -1,3 +1,3 @@\n line1\n-line2\n+line2 changed\n line3\n")
    body = {"diff": diff, "filePath": "src/data.txt", "requestId": "t1",
            "projectRoot": env["project_root"]}
    if conversation_id:
        body["conversation_id"] = conversation_id
    resp = run(apply_changes(_Req(body)))
    return resp, json.loads(resp.body)


class TestApplyRoute:
    def test_apply_with_conversation_id_auto_adds(self, chat_env):
        resp, data = _apply(chat_env, chat_env["chat_id"])
        assert resp.status_code == 200, data
        assert "line2 changed" in (Path(chat_env["project_root"]) / "src/data.txt").read_text()
        chat = _read_chat(chat_env)
        assert "src/data.txt" in chat["additionalFiles"]
        assert "src/data.txt" in _auto(chat)
        assert data.get("auto_context", {}).get("tier") == "included"

    def test_apply_without_conversation_id_changes_nothing(self, chat_env):
        resp, data = _apply(chat_env, None)
        assert resp.status_code == 200, data
        assert "line2 changed" in (Path(chat_env["project_root"]) / "src/data.txt").read_text()
        chat = _read_chat(chat_env)
        assert "src/data.txt" not in chat["additionalFiles"]


# -- context tools know about the auto owner -------------------------------

class TestContextTools:
    def test_remove_file_can_remove_an_auto_file(self, chat_env, writable):
        from app.mcp.tools.context_management import ContextRemoveFileTool
        _file_write(path="notes.md", content="# notes\n")
        r = run(ContextRemoveFileTool().execute(path="notes.md"))
        assert r.get("success"), r
        chat = _read_chat(chat_env)
        assert "notes.md" not in chat["additionalFiles"]
        assert "notes.md" not in _auto(chat)

    def test_remove_file_still_refuses_user_pinned(self, chat_env):
        from app.mcp.tools.context_management import ContextRemoveFileTool
        r = run(ContextRemoveFileTool().execute(path="existing/user-pinned.txt"))
        assert r.get("error") is True

    def test_list_files_reports_auto_owner_and_tracked(self, chat_env, writable, monkeypatch):
        from app.mcp.tools.context_management import ContextListFilesTool
        _file_write(path="notes.md", content="# notes\n")
        monkeypatch.setattr("app.utils.chat_context_files.resolve_auto_add_token_limit",
                            lambda *a, **kw: 20)
        _file_write(path="big.md", content="word " * 400)
        r = run(ContextListFilesTool().execute())
        owners = {f["path"]: f["owner"] for f in r["files"]}
        assert owners["notes.md"] == "auto"
        assert owners["existing/user-pinned.txt"] == "user"
        assert [t["path"] for t in r.get("tracked", [])] == ["big.md"]
