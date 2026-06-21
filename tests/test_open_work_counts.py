"""
Tests for the sidebar open-work indicators: bead counts, the WorkItem
primitive shell, and their propagation through the chat-summary path.

Covers:
  - WorkItem model: factories, scope discriminator, status machine
  - count_open_work_items: open-state counting, tolerant input handling
  - count_open_beads: active+parked counting, tolerant input handling
  - _get_conversation_id REGRESSION GUARD: must NOT be a no-op (a misapplied
    diff once spliced count_open_beads into its body, severing bead
    persistence — this guard goes green only when that is repaired)
  - ChatStorage.list_summaries populates openBeadCount / openWorkItemCount
  - collect_global_chat_summaries populates the same counts
"""
import os
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from app.models.work_item import (
    WorkItem,
    WorkItemScope,
    count_open_work_items,
    WORK_ITEM_OPEN_STATUSES,
    WORK_ITEM_STATUSES,
)
from app.storage.beads import count_open_beads, _get_conversation_id
from app.storage.chats import ChatStorage


# ── WorkItem primitive ──────────────────────────────────────────────

class TestWorkItemModel:

    def test_for_session_factory(self):
        wi = WorkItem.for_session("conv-1", "wire the indicator")
        assert wi.content == "wire the indicator"
        assert wi.conversation_id == "conv-1"
        assert wi.scope.type == "session"
        assert wi.scope.key == "conv-1"
        assert wi.status == "todo"           # default
        assert wi.id.startswith("wi_")

    def test_for_project_factory(self):
        wi = WorkItem.for_project("proj-9", "backlog item", conversation_id="conv-3")
        assert wi.scope.type == "project"
        assert wi.scope.key == "proj-9"
        assert wi.conversation_id == "conv-3"

    def test_for_project_conversation_id_optional(self):
        wi = WorkItem.for_project("proj-9", "authored against backlog")
        assert wi.conversation_id == ""      # empty when authored directly

    def test_scope_discriminator_only_two_values(self):
        assert WorkItemScope(type="session", key="x").type == "session"
        assert WorkItemScope(type="project", key="y").type == "project"
        with pytest.raises(Exception):
            WorkItemScope(type="feature", key="z")  # no intermediate tier

    def test_status_machine_constants(self):
        # Status set matches the taxonomy doc (todo→doing→done + blocked/abandoned)
        assert set(WORK_ITEM_STATUSES) == {"todo", "doing", "done", "blocked", "abandoned"}
        # Open == not terminal; done and abandoned are closed.
        assert WORK_ITEM_OPEN_STATUSES == frozenset({"todo", "doing", "blocked"})
        assert "done" not in WORK_ITEM_OPEN_STATUSES
        assert "abandoned" not in WORK_ITEM_OPEN_STATUSES


class TestCountOpenWorkItems:

    def test_counts_open_states(self):
        items = [
            {"status": "todo"}, {"status": "doing"}, {"status": "blocked"},
            {"status": "done"}, {"status": "abandoned"},
        ]
        assert count_open_work_items(items) == 3   # todo + doing + blocked

    def test_empty_and_none_are_zero(self):
        assert count_open_work_items(None) == 0
        assert count_open_work_items([]) == 0
        assert count_open_work_items("not a list") == 0
        assert count_open_work_items({"status": "todo"}) == 0  # dict, not list

    def test_accepts_workitem_objects(self):
        items = [
            WorkItem.for_session("c", "a"),               # todo (open)
            WorkItem.for_session("c", "b", status="done"),  # closed
        ]
        assert count_open_work_items(items) == 1

    def test_shell_behavior_chat_record_has_no_work_items(self):
        # The whole point of the shell: a chat record never carries
        # _work_items today, so the count is always 0.
        chat_record = {"id": "c1", "title": "x", "messages": []}
        assert count_open_work_items(chat_record.get("_work_items")) == 0


# ── count_open_beads ────────────────────────────────────────────────

class TestCountOpenBeads:

    def test_counts_active_and_parked(self):
        beads = [
            {"status": "active"}, {"status": "parked"}, {"status": "parked"},
            {"status": "completed"}, {"status": "abandoned"},
        ]
        assert count_open_beads(beads) == 3   # active + 2 parked

    def test_active_only(self):
        assert count_open_beads([{"status": "active"}]) == 1

    def test_completed_abandoned_not_counted(self):
        assert count_open_beads([{"status": "completed"}, {"status": "abandoned"}]) == 0

    def test_empty_and_none_are_zero(self):
        assert count_open_beads(None) == 0
        assert count_open_beads([]) == 0
        assert count_open_beads("nope") == 0

    def test_accepts_bead_objects(self):
        from app.models.bead import Bead
        beads = [
            Bead(content="a", status="active"),
            Bead(content="b", status="parked"),
            Bead(content="c", status="completed"),
        ]
        assert count_open_beads(beads) == 2


# ── count_open_beads_for_conversation (fallback-aware) ───────────────
# The sidebar summary count must mirror load_bead_tree's SOURCES (record
# first, then the standalone ~/.ziya/beads/<id>.json fallback store).  The
# original record-only count showed 0 for conversations whose beads lived in
# the fallback (CLI sessions, not-yet-synced web conversations) while the
# bead chip — which reads the fallback via load_bead_tree — showed them.
# That chip-vs-summary divergence is exactly what this helper repairs.

class TestCountOpenBeadsForConversation:

    def test_reads_record_when_record_has_beads(self):
        from app.storage.beads import count_open_beads_for_conversation
        rec = {"id": "c1", "_beads": [{"status": "active"}, {"status": "parked"}]}
        # Record present → counted directly, fallback never consulted.
        assert count_open_beads_for_conversation(rec, "c1") == 2

    def test_record_wins_over_fallback_during_migration_window(self):
        # save_bead_tree writes the record then removes the fallback; if both
        # momentarily exist, the record is authoritative.  A non-empty _beads
        # must short-circuit before _load_fallback is ever called.
        from app.storage import beads as beads_mod
        rec = {"id": "c1", "_beads": [{"status": "active"}]}
        with patch.object(beads_mod, "_load_fallback") as mock_fb:
            assert beads_mod.count_open_beads_for_conversation(rec, "c1") == 1
            mock_fb.assert_not_called()

    def test_falls_back_to_store_when_record_has_no_beads(self):
        # The headline case: record carries no _beads, but the fallback store
        # holds open beads (the chip's source).  Summary must match the chip.
        from app.storage import beads as beads_mod
        rec = {"id": "c1"}  # no _beads key
        with patch.object(beads_mod, "_load_fallback",
                          return_value=[{"status": "active"}, {"status": "parked"}]):
            assert beads_mod.count_open_beads_for_conversation(rec, "c1") == 2

    def test_empty_record_beads_still_consults_fallback(self):
        # _beads present but empty list is falsy → fallback is consulted.
        from app.storage import beads as beads_mod
        rec = {"id": "c1", "_beads": []}
        with patch.object(beads_mod, "_load_fallback",
                          return_value=[{"status": "parked"}]):
            assert beads_mod.count_open_beads_for_conversation(rec, "c1") == 1

    def test_no_conversation_id_skips_fallback(self):
        from app.storage import beads as beads_mod
        rec = {"_beads": None}
        with patch.object(beads_mod, "_load_fallback") as mock_fb:
            assert beads_mod.count_open_beads_for_conversation(rec, None) == 0
            mock_fb.assert_not_called()

    def test_non_dict_record_is_zero(self):
        from app.storage.beads import count_open_beads_for_conversation
        # Defensive: a non-dict raw record (shouldn't happen) → 0, no crash,
        # and no fallback lookup because conversation_id-driven path needs a
        # real id; passing None keeps it a clean zero.
        assert count_open_beads_for_conversation(None, None) == 0

    def test_fallback_with_only_terminal_beads_is_zero(self):
        from app.storage import beads as beads_mod
        rec = {"id": "c1"}
        with patch.object(beads_mod, "_load_fallback",
                          return_value=[{"status": "completed"}, {"status": "abandoned"}]):
            assert beads_mod.count_open_beads_for_conversation(rec, "c1") == 0


# ── _get_conversation_id regression guard ───────────────────────────
# A misapplied diff once spliced count_open_beads between this function's
# docstring and its body, leaving the body unreachable and the function a
# no-op that always returned None — silently severing ALL bead persistence
# (load/save resolve the id to None).  This guard fails on the broken
# state and passes only when the function actually resolves its argument.
# Mirrors the set_conversation_id no-op guard documented in the CHANGELOG.

class TestGetConversationIdNotNoOp:

    def test_explicit_arg_is_returned(self):
        # The single most important assertion: the explicit argument must
        # round-trip.  If this returns None, _get_conversation_id has been
        # severed again and bead persistence is broken.
        assert _get_conversation_id("explicit-conv-123") == "explicit-conv-123"

    def test_falls_back_to_contextvar_when_no_arg(self, monkeypatch):
        # With no explicit id, it consults the request ContextVar.  Patch the
        # resolver to a known value and confirm it's threaded through.
        import app.context as ctx
        monkeypatch.setattr(ctx, "get_conversation_id_or_none", lambda: "ctx-conv-9")
        assert _get_conversation_id(None) == "ctx-conv-9"

    def test_none_when_no_arg_and_no_context(self, monkeypatch):
        import app.context as ctx
        monkeypatch.setattr(ctx, "get_conversation_id_or_none", lambda: None)
        assert _get_conversation_id(None) is None


# ── Summary population (the path the sidebar reads) ──────────────────

@pytest.fixture
def storage(tmp_path):
    project_dir = tmp_path / "proj"
    project_dir.mkdir()
    return ChatStorage(project_dir)


def _write_chat(storage, chat_id, *, beads=None, work_items=None, messages=None):
    now = int(time.time() * 1000)
    rec = {
        "id": chat_id,
        "title": f"chat {chat_id}",
        "messages": messages or [],
        "createdAt": now,
        "lastActiveAt": now,
        "_version": now,
    }
    if beads is not None:
        rec["_beads"] = beads
    if work_items is not None:
        rec["_work_items"] = work_items
    storage._write_json(storage._chat_file(chat_id), rec)


class TestListSummariesPopulatesCounts:

    def test_open_bead_count_from_record(self, storage):
        _write_chat(storage, "c1", beads=[
            {"id": "b1", "status": "active", "content": "x"},
            {"id": "b2", "status": "parked", "content": "y"},
            {"id": "b3", "status": "completed", "content": "z"},
        ])
        summary = next(s for s in storage.list_summaries() if s.id == "c1")
        assert summary.openBeadCount == 2          # active + parked
        assert summary.openWorkItemCount == 0      # shell: no _work_items

    def test_no_beads_is_zero(self, storage):
        _write_chat(storage, "c2")  # no _beads field at all
        summary = next(s for s in storage.list_summaries() if s.id == "c2")
        assert summary.openBeadCount == 0
        assert summary.openWorkItemCount == 0

    def test_work_item_count_is_shell_zero_even_if_field_present(self, storage):
        # Defensive: even if a future writer leaves _work_items, the count
        # reflects open states.  Today nothing writes it, so this also proves
        # the helper is wired (not hard-coded 0).
        _write_chat(storage, "c3", work_items=[
            {"id": "wi1", "status": "todo"},
            {"id": "wi2", "status": "done"},
        ])
        summary = next(s for s in storage.list_summaries() if s.id == "c3")
        assert summary.openWorkItemCount == 1      # todo open, done closed
        assert summary.openBeadCount == 0

    def test_counts_survive_summary_cache(self, storage):
        # list_summaries has a per-file mtime cache; a second call must
        # return the same counts (cache stores the built summary).
        _write_chat(storage, "c4", beads=[{"id": "b", "status": "parked", "content": "q"}])
        first = next(s for s in storage.list_summaries() if s.id == "c4")
        second = next(s for s in storage.list_summaries() if s.id == "c4")
        assert first.openBeadCount == second.openBeadCount == 1


class TestGlobalSummariesPopulateCounts:

    def test_collect_global_chat_summaries_counts(self, tmp_path, monkeypatch):
        from app.storage import global_items
        ziya_home = tmp_path / ".ziya"
        # Two projects; the global chat lives in "other" and must surface
        # for the requesting project with its bead count intact.
        other = ziya_home / "projects" / "other" / "chats"
        other.mkdir(parents=True)
        now = int(time.time() * 1000)
        other_storage = ChatStorage(ziya_home / "projects" / "other")
        other_storage._write_json(other_storage._chat_file("g1"), {
            "id": "g1", "title": "global chat", "messages": [],
            "createdAt": now, "lastActiveAt": now, "_version": now,
            "isGlobal": True,
            "_beads": [
                {"id": "b1", "status": "parked", "content": "thread"},
                {"id": "b2", "status": "active", "content": "cur"},
            ],
        })
        # Clear the module-level summary cache so this temp file is read fresh.
        global_items._summary_cache.clear()
        results = global_items.collect_global_chat_summaries(
            ziya_home, exclude_project_id="requesting-proj"
        )
        g1 = next(s for s in results if s.id == "g1")
        assert g1.openBeadCount == 2
        assert g1.openWorkItemCount == 0
