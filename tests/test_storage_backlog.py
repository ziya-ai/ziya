"""
Tests for app.storage.backlog -- aggregation over a project's chat files
into the backlog payload (design/bead-backlog-browser.md).
"""
import os
import time
from unittest.mock import patch

import pytest

from app.storage.chats import ChatStorage
import app.storage.backlog as backlog_mod
from app.storage.backlog import get_backlog, invalidate, _extract_cache


@pytest.fixture(autouse=True)
def _clear_extract_cache():
    """The memo cache is process-lifetime / module-level; isolate tests."""
    _extract_cache.clear()
    yield
    _extract_cache.clear()


@pytest.fixture
def ziya_home(tmp_path):
    home = tmp_path / ".ziya"
    home.mkdir()
    return home


@pytest.fixture
def project_dir(ziya_home):
    project_id = "test-project-001"
    (ziya_home / "projects" / project_id / "chats").mkdir(parents=True)
    return project_id


@pytest.fixture
def proj_path(ziya_home, project_dir):
    return ziya_home / "projects" / project_dir


@pytest.fixture(autouse=True)
def _patch_project_dir(proj_path):
    with patch("app.storage.backlog.get_project_dir", return_value=proj_path):
        yield


def _msg(i, role, content=None):
    return {"id": f"m{i}", "role": role, "content": content or f"msg {i}", "timestamp": 1000 + i}


def _write_chat(proj_path, chat_id, messages=None, beads=None, extra=None):
    storage = ChatStorage(proj_path)
    now = int(time.time() * 1000)
    data = {
        "id": chat_id,
        "title": f"Title for {chat_id}",
        "messages": messages or [],
        "createdAt": now,
        "lastActiveAt": now,
        "_version": now,
        "folderId": "folder-1",
        "_beads": beads or [],
    }
    if extra:
        data.update(extra)
    storage._write_json(storage._chat_file(chat_id), data)
    return storage


def _bead(id, status, parent_id=None, message_index=None, created_at=None, **kw):
    b = {
        "id": id,
        "content": f"content-{id}",
        "status": status,
        "parent_id": parent_id,
        "message_index": message_index,
        "created_at": created_at if created_at is not None else int(time.time() * 1000),
    }
    b.update(kw)
    return b


# ── Filtering ────────────────────────────────────────────────────────────

def test_parked_filter_returns_only_parked(proj_path):
    messages = [_msg(0, "human"), _msg(1, "assistant")]
    beads = [
        _bead("b1", "parked", message_index=1),
        _bead("b2", "abandoned", message_index=1),
        _bead("b3", "active", message_index=1),
        _bead("b4", "completed", message_index=1),
    ]
    _write_chat(proj_path, "chat-1", messages, beads)

    result = get_backlog("test-project-001", ["parked"])
    ids = {i["bead"]["id"] for i in result["items"]}
    assert ids == {"b1"}


def test_abandoned_filter_returns_only_abandoned(proj_path):
    messages = [_msg(0, "human"), _msg(1, "assistant")]
    beads = [
        _bead("b1", "parked", message_index=1),
        _bead("b2", "abandoned", message_index=1),
    ]
    _write_chat(proj_path, "chat-1", messages, beads)

    result = get_backlog("test-project-001", ["abandoned"])
    ids = {i["bead"]["id"] for i in result["items"]}
    assert ids == {"b2"}


def test_both_statuses_returns_both(proj_path):
    messages = [_msg(0, "human"), _msg(1, "assistant")]
    beads = [
        _bead("b1", "parked", message_index=1),
        _bead("b2", "abandoned", message_index=1),
    ]
    _write_chat(proj_path, "chat-1", messages, beads)

    result = get_backlog("test-project-001", ["parked", "abandoned"])
    ids = {i["bead"]["id"] for i in result["items"]}
    assert ids == {"b1", "b2"}
    assert result["counts"] == {"parked": 1, "abandoned": 1}


# ── Topmost-parked collapse ─────────────────────────────────────────────

def test_topmost_collapse_with_descendant_count(proj_path):
    """A parked ancestor with parked descendants collapses to one item;
    descendant_parked_count reflects the rolled-up descendants."""
    messages = [_msg(i, "human") for i in range(5)]
    beads = [
        _bead("root", "parked", parent_id=None, message_index=1),
        _bead("mid", "parked", parent_id="root", message_index=2),
        _bead("leaf", "parked", parent_id="mid", message_index=3),
        # A non-parked descendant should not count.
        _bead("other", "active", parent_id="root", message_index=4),
    ]
    _write_chat(proj_path, "chat-1", messages, beads)

    result = get_backlog("test-project-001", ["parked"])
    ids = [i["bead"]["id"] for i in result["items"]]
    assert ids == ["root"]
    assert result["items"][0]["descendant_parked_count"] == 2  # mid + leaf
    # Raw counts are uncollapsed (all 3 parked beads counted).
    assert result["counts"]["parked"] == 3


def test_topmost_collapse_does_not_cross_status(proj_path):
    """A parked bead under a non-parked ancestor is still topmost (emitted)."""
    messages = [_msg(i, "human") for i in range(3)]
    beads = [
        _bead("root", "completed", parent_id=None, message_index=1),
        _bead("mid", "parked", parent_id="root", message_index=2),
    ]
    _write_chat(proj_path, "chat-1", messages, beads)

    result = get_backlog("test-project-001", ["parked"])
    ids = {i["bead"]["id"] for i in result["items"]}
    assert ids == {"mid"}
    assert result["items"][0]["descendant_parked_count"] == 0


# ── Lineage-root attribution ─────────────────────────────────────────────

def test_lineage_root_attribution(proj_path):
    """A fork record (lineageRootId set) is skipped by the scan; the root
    record (which owns _beads) is the one attributed and surfaced."""
    root_messages = [_msg(0, "human"), _msg(1, "assistant")]
    root_beads = [_bead("root-bead", "parked", message_index=1)]
    _write_chat(proj_path, "root-chat", root_messages, root_beads,
                extra={"title": "Root Conversation Title"})

    # Fork record: carries lineageRootId pointing elsewhere.  Even if a
    # stray _beads list were present on the fork record, it must be ignored
    # -- the scan skips any record whose lineageRootId != its own id.
    _write_chat(proj_path, "fork-chat", [_msg(0, "human")],
                [_bead("stray-bead", "parked", message_index=1)],
                extra={"lineageRootId": "root-chat", "title": "Fork Title"})

    result = get_backlog("test-project-001", ["parked"])
    assert len(result["items"]) == 1
    item = result["items"][0]
    assert item["conversation_id"] == "root-chat"
    assert item["conversation_title"] == "Root Conversation Title"
    assert item["bead"]["id"] == "root-bead"  # fork's stray bead is ignored


def test_self_root_lineage_is_scanned_normally(proj_path):
    """lineageRootId == own id (self-root) is scanned like a normal chat."""
    messages = [_msg(0, "human"), _msg(1, "assistant")]
    beads = [_bead("b1", "parked", message_index=1)]
    _write_chat(proj_path, "chat-1", messages, beads,
                extra={"lineageRootId": "chat-1"})

    result = get_backlog("test-project-001", ["parked"])
    assert len(result["items"]) == 1
    assert result["items"][0]["conversation_id"] == "chat-1"


# ── null message_index -> can_branch false ───────────────────────────────

def test_null_message_index_can_branch_false(proj_path):
    messages = [_msg(0, "human")]
    beads = [_bead("b1", "parked", message_index=None)]
    _write_chat(proj_path, "chat-1", messages, beads)

    result = get_backlog("test-project-001", ["parked"])
    assert len(result["items"]) == 1
    assert result["items"][0]["can_branch"] is False
    assert result["items"][0]["seam_snippet"] is None


def test_present_message_index_can_branch_true(proj_path):
    messages = [_msg(0, "human"), _msg(1, "assistant")]
    beads = [_bead("b1", "parked", message_index=1)]
    _write_chat(proj_path, "chat-1", messages, beads)

    result = get_backlog("test-project-001", ["parked"])
    assert result["items"][0]["can_branch"] is True


# ── Seam snippet extraction ──────────────────────────────────────────────

def test_seam_snippet_extraction(proj_path):
    messages = [
        _msg(0, "human", "the original question"),
        _msg(1, "assistant", "an answer that spawned a parked thread"),
    ]
    beads = [_bead("b1", "parked", message_index=2)]
    _write_chat(proj_path, "chat-1", messages, beads)

    result = get_backlog("test-project-001", ["parked"])
    snippet = result["items"][0]["seam_snippet"]
    assert snippet is not None
    assert snippet["role"] == "assistant"
    assert snippet["text"] == "an answer that spawned a parked thread"


def test_seam_snippet_truncated_to_240_chars(proj_path):
    long_text = "x" * 500
    messages = [_msg(0, "human"), _msg(1, "assistant", long_text)]
    beads = [_bead("b1", "parked", message_index=2)]
    _write_chat(proj_path, "chat-1", messages, beads)

    result = get_backlog("test-project-001", ["parked"])
    snippet = result["items"][0]["seam_snippet"]
    assert len(snippet["text"]) == 240


def test_seam_snippet_out_of_range_index_is_none(proj_path):
    """message_index pointing past the messages array yields no snippet."""
    messages = [_msg(0, "human")]
    beads = [_bead("b1", "parked", message_index=99)]
    _write_chat(proj_path, "chat-1", messages, beads)

    result = get_backlog("test-project-001", ["parked"])
    assert result["items"][0]["seam_snippet"] is None


# ── mtime memo cache ──────────────────────────────────────────────────────

def test_mtime_cache_second_call_does_not_reread_unchanged_file(proj_path):
    messages = [_msg(0, "human"), _msg(1, "assistant")]
    beads = [_bead("b1", "parked", message_index=1)]
    _write_chat(proj_path, "chat-1", messages, beads)

    get_backlog("test-project-001", ["parked"])  # populates cache

    with patch.object(
        ChatStorage, "_read_json", wraps=ChatStorage._read_json
    ) as read_spy:
        get_backlog("test-project-001", ["parked"])
        # Unchanged mtime -> cache hit -> no re-read of the chat file.
        assert read_spy.call_count == 0


def test_mtime_cache_touched_file_causes_reextract(proj_path):
    messages = [_msg(0, "human"), _msg(1, "assistant")]
    beads = [_bead("b1", "parked", message_index=1)]
    storage = _write_chat(proj_path, "chat-1", messages, beads)

    result1 = get_backlog("test-project-001", ["parked"])
    assert {i["bead"]["id"] for i in result1["items"]} == {"b1"}

    # Mutate the file (simulating a bead status change / new mtime).
    time.sleep(0.01)
    new_beads = [
        _bead("b1", "parked", message_index=1),
        _bead("b2", "parked", message_index=1),
    ]
    storage._write_json(
        storage._chat_file("chat-1"),
        {
            "id": "chat-1",
            "title": "Title for chat-1",
            "messages": messages,
            "createdAt": 1,
            "lastActiveAt": 1,
            "_version": 2,
            "folderId": "folder-1",
            "_beads": new_beads,
        },
    )
    # Force a distinct mtime in case the filesystem clock resolution is
    # coarser than the sleep (some CI filesystems round to whole seconds).
    file_path = storage._chat_file("chat-1")
    st = os.stat(file_path)
    os.utime(file_path, (st.st_atime, st.st_mtime + 1))

    result2 = get_backlog("test-project-001", ["parked"])
    assert {i["bead"]["id"] for i in result2["items"]} == {"b1", "b2"}


def test_invalidate_drops_cached_extract_for_conversation(proj_path):
    messages = [_msg(0, "human"), _msg(1, "assistant")]
    beads = [_bead("b1", "parked", message_index=1)]
    _write_chat(proj_path, "chat-1", messages, beads)

    get_backlog("test-project-001", ["parked"])
    assert any(
        ex is not None and ex.get("conversation_id") == "chat-1"
        for _, ex in _extract_cache.values()
    )
    invalidate("chat-1")
    assert not any(
        ex is not None and ex.get("conversation_id") == "chat-1"
        for _, ex in _extract_cache.values()
    )


def test_scanned_chats_counts_all_chat_files(proj_path):
    _write_chat(proj_path, "chat-1", [], [_bead("b1", "parked")])
    _write_chat(proj_path, "chat-2", [], [])  # no beads at all

    result = get_backlog("test-project-001", ["parked"])
    assert result["scanned_chats"] == 2
