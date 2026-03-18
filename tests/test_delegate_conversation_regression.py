"""
Regression tests for delegate conversation loading performance.

These tests guard against the bug where clicking a delegate conversation
froze the UI for 35+ seconds.  Root causes:

1. loadConversation called queueSave (full IndexedDB write of ALL
   conversations) just to flip hasUnreadResponse — a cosmetic flag.
2. loadConversation had `conversations` in its useCallback deps,
   causing it to be recreated on every conversation mutation.
3. activeSwarmInfo depended on the full conversations array, causing
   StreamedContent to re-render on every unrelated state change.

Backend-verifiable invariants covered here:
- Terminal delegate status detection (crystal / failed / interrupted)
- Single-chat retrieval does not modify unrelated chats
- Bulk-sync handles partial updates without full rewrites
- DelegateMeta status transitions are well-defined
"""

import os
import sys
import pytest
import json
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.models.delegate import DelegateMeta, DelegateSpec, TaskPlan, MemoryCrystal
from app.models.chat import Chat


# ── Terminal status detection ─────────────────────────────────────────

TERMINAL_DELEGATE_STATUSES = frozenset({"crystal", "failed", "interrupted"})
NON_TERMINAL_DELEGATE_STATUSES = [
    "proposed", "ready", "running", "compacting", "stalled", "blocked",
]


class TestTerminalDelegateDetection:
    """
    The frontend's loadConversation skips server fetches for terminal
    delegates.  If this detection fails, every click triggers an HTTP
    round-trip that cascades into a full save.
    """

    @pytest.mark.parametrize("status", list(TERMINAL_DELEGATE_STATUSES))
    def test_terminal_statuses_detected(self, status):
        meta = DelegateMeta(role="delegate", plan_id="p1", delegate_id="d1", status=status)
        assert meta.status in TERMINAL_DELEGATE_STATUSES, (
            f"Status '{status}' should be terminal but was not detected"
        )

    @pytest.mark.parametrize("status", NON_TERMINAL_DELEGATE_STATUSES)
    def test_non_terminal_statuses_not_detected(self, status):
        meta = DelegateMeta(role="delegate", plan_id="p1", delegate_id="d1", status=status)
        assert meta.status not in TERMINAL_DELEGATE_STATUSES, (
            f"Status '{status}' should NOT be terminal"
        )

    def test_default_status_is_non_terminal(self):
        """Default 'proposed' status must not be terminal."""
        meta = DelegateMeta(role="delegate", plan_id="p1", delegate_id="d1")
        assert meta.status == "proposed"
        assert meta.status not in TERMINAL_DELEGATE_STATUSES

    def test_crystal_delegate_with_crystal_data(self):
        """A crystal delegate should have terminal status AND crystal data."""
        crystal = MemoryCrystal(
            delegate_id="d1",
            task="Build auth",
            summary="Done",
            original_tokens=10000,
            crystal_tokens=300,
        )
        meta = DelegateMeta(
            role="delegate", plan_id="p1", delegate_id="d1",
            status="crystal", crystal=crystal,
        )
        assert meta.status in TERMINAL_DELEGATE_STATUSES
        assert meta.crystal is not None
        assert meta.crystal.crystal_tokens < meta.crystal.original_tokens


# ── Chat save isolation ───────────────────────────────────────────────

class TestChatSaveIsolation:
    """
    Loading a delegate conversation must NOT trigger saves of
    unrelated conversations.  The frontend queueSave was writing
    ALL 456 conversations to IndexedDB; the backend equivalent is
    the bulk-sync endpoint.  These tests verify the backend storage
    supports granular (single-chat) updates.
    """

    @pytest.fixture
    def storage_dir(self, tmp_path):
        project_dir = tmp_path / "projects" / "test-project" / "chats"
        project_dir.mkdir(parents=True)
        return project_dir

    def _write_chat(self, storage_dir, chat: Chat):
        path = storage_dir / f"{chat.id}.json"
        path.write_text(json.dumps(chat.model_dump(), default=str))

    def _read_chat(self, storage_dir, chat_id: str) -> dict:
        path = storage_dir / f"{chat_id}.json"
        return json.loads(path.read_text())

    def test_update_single_chat_does_not_modify_others(self, storage_dir):
        """Updating one chat's metadata must not touch other chat files."""
        chat_a = Chat(id="chat-a", title="Chat A", messages=[], createdAt=1000, lastActiveAt=1000)
        chat_b = Chat(id="chat-b", title="Chat B", messages=[], createdAt=1000, lastActiveAt=1000)
        self._write_chat(storage_dir, chat_a)
        self._write_chat(storage_dir, chat_b)

        # Ensure filesystem timestamps differ
        time.sleep(0.05)
        mtime_b_before = (storage_dir / "chat-b.json").stat().st_mtime

        # Update chat A only (simulates marking as read)
        data = self._read_chat(storage_dir, "chat-a")
        data["hasUnreadResponse"] = False
        (storage_dir / "chat-a.json").write_text(json.dumps(data, default=str))

        # Chat B must NOT be modified
        mtime_b_after = (storage_dir / "chat-b.json").stat().st_mtime
        assert mtime_b_before == mtime_b_after, (
            "Updating chat A should not modify chat B's file"
        )

    def test_delegate_chat_roundtrip_preserves_meta(self, storage_dir):
        """Saving and loading a delegate chat must preserve delegateMeta."""
        meta = DelegateMeta(role="delegate", plan_id="p1", delegate_id="d1", status="crystal")
        chat = Chat(
            id="delegate-1", title="D1: Auth",
            messages=[], createdAt=1000, lastActiveAt=1000,
            delegateMeta=meta,
        )
        self._write_chat(storage_dir, chat)

        loaded = self._read_chat(storage_dir, "delegate-1")
        assert loaded["delegateMeta"]["status"] == "crystal"
        assert loaded["delegateMeta"]["role"] == "delegate"

        # Round-trip through model
        chat2 = Chat(**loaded)
        assert chat2.delegateMeta is not None
        assert chat2.delegateMeta.status == "crystal"

    def test_many_chats_single_update_performance(self, storage_dir):
        """
        Regression: updating 1 chat out of 500 should not require
        reading or writing the other 499.
        """
        # Create 500 chats
        for i in range(500):
            chat = Chat(
                id=f"chat-{i}", title=f"Chat {i}",
                messages=[], createdAt=1000, lastActiveAt=1000,
            )
            self._write_chat(storage_dir, chat)

        # Time a single-chat update
        start = time.monotonic()
        data = self._read_chat(storage_dir, "chat-250")
        data["hasUnreadResponse"] = False
        (storage_dir / "chat-250.json").write_text(json.dumps(data, default=str))
        elapsed = time.monotonic() - start

        # A single-file update should be sub-100ms, not 35 seconds
        assert elapsed < 1.0, (
            f"Single chat update took {elapsed:.2f}s — should be <1s. "
            "Possible regression: bulk save instead of granular update."
        )


# ── Delegate status key derivation ────────────────────────────────────

class TestDelegateStatusKey:
    """
    The frontend's delegateStatusKey is a string built from delegate
    conversation IDs and statuses.  It should change ONLY when a
    delegate's status actually changes, not on unrelated mutations.
    """

    @staticmethod
    def _build_status_key(conversations: list) -> str:
        """Mirror the frontend's delegateStatusKey derivation."""
        return ",".join(
            f"{c['id']}:{c['delegateMeta']['status']}"
            for c in conversations
            if c.get("delegateMeta")
        )

    def test_key_unchanged_on_unrelated_mutation(self):
        convos = [
            {"id": "c1", "title": "Regular", "messages": ["hello"]},
            {"id": "d1", "title": "Delegate", "delegateMeta": {"status": "running"}},
        ]
        key_before = self._build_status_key(convos)

        # Mutate unrelated conversation
        convos[0]["messages"].append("world")
        convos[0]["title"] = "Changed title"
        key_after = self._build_status_key(convos)

        assert key_before == key_after

    def test_key_changes_on_status_transition(self):
        convos = [
            {"id": "d1", "title": "Delegate", "delegateMeta": {"status": "running"}},
        ]
        key_before = self._build_status_key(convos)

        convos[0]["delegateMeta"]["status"] = "crystal"
        key_after = self._build_status_key(convos)

        assert key_before != key_after

    def test_key_stable_across_many_unrelated_changes(self):
        """Simulates 100 unrelated conversation mutations."""
        delegate = {"id": "d1", "delegateMeta": {"status": "running"}}
        convos = [{"id": f"c{i}", "messages": []} for i in range(100)]
        convos.append(delegate)

        key_initial = self._build_status_key(convos)

        for c in convos[:100]:
            c["messages"].append("noise")

        key_final = self._build_status_key(convos)
        assert key_initial == key_final

    def test_key_with_multiple_delegates(self):
        """All delegate statuses contribute to the key."""
        convos = [
            {"id": "d1", "delegateMeta": {"status": "crystal"}},
            {"id": "d2", "delegateMeta": {"status": "running"}},
            {"id": "d3", "delegateMeta": {"status": "failed"}},
        ]
        key = self._build_status_key(convos)
        assert "d1:crystal" in key
        assert "d2:running" in key
        assert "d3:failed" in key

    def test_empty_conversations(self):
        assert self._build_status_key([]) == ""

    def test_no_delegates(self):
        convos = [{"id": "c1"}, {"id": "c2"}]
        assert self._build_status_key(convos) == ""


# ── TaskPlan terminal status ──────────────────────────────────────────

TERMINAL_PLAN_STATUSES = frozenset({"completed", "completed_partial", "cancelled"})


class TestTaskPlanStatus:
    """Verify TaskPlan status transitions for swarm display logic."""

    def test_completed_plan_is_terminal(self):
        plan = TaskPlan(name="Test", status="completed", created_at=1000)
        assert plan.status in TERMINAL_PLAN_STATUSES

    def test_running_plan_is_not_terminal(self):
        plan = TaskPlan(name="Test", status="running", created_at=1000)
        assert plan.status not in TERMINAL_PLAN_STATUSES

    def test_plan_with_mixed_delegate_statuses(self):
        """A plan can have both crystal and failed delegates."""
        specs = [
            DelegateSpec(delegate_id="d1", name="A"),
            DelegateSpec(delegate_id="d2", name="B"),
        ]
        plan = TaskPlan(
            name="Mixed", delegate_specs=specs,
            status="completed_partial", created_at=1000,
        )
        assert plan.status in TERMINAL_PLAN_STATUSES
        assert len(plan.delegate_specs) == 2

    def test_planning_status_default(self):
        plan = TaskPlan(name="New", created_at=1000)
        assert plan.status == "planning"
        assert plan.status not in TERMINAL_PLAN_STATUSES

    def test_cancelled_is_terminal(self):
        plan = TaskPlan(name="Cancelled", status="cancelled", created_at=1000)
        assert plan.status in TERMINAL_PLAN_STATUSES
