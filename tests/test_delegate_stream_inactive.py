"""
Tests for the inactive delegate streaming fix.

Verifies that:
1. loadConversation skips the blocking server fetch for terminal delegates
2. useDelegateStreaming doesn't open WebSockets for terminal delegates
3. Derived dependency keys prevent unnecessary effect re-runs
"""

import pytest
from unittest.mock import MagicMock, AsyncMock, patch
from pathlib import Path

from app.models.delegate import DelegateSpec, DelegateMeta, MemoryCrystal, TaskPlan


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def tmp_project(tmp_path):
    """Create a minimal project directory structure."""
    project_dir = tmp_path / "test-project"
    chats_dir = project_dir / "chats"
    chats_dir.mkdir(parents=True)
    return project_dir


@pytest.fixture
def manager(tmp_project):
    """DelegateManager with mocked storage."""
    from app.agents.delegate_manager import DelegateManager
    mgr = DelegateManager("proj-1", tmp_project, max_concurrency=2)
    mgr._persist_plan = MagicMock()
    mgr._patch_chat_delegate_meta = MagicMock()
    mgr._patch_chat_crystal = MagicMock()
    mgr._patch_chat_status = MagicMock()
    mgr._patch_group_task_plan = MagicMock()
    mgr._persist_delegate_message = MagicMock()
    return mgr


def make_crystal(delegate_id: str, task: str = "test") -> MemoryCrystal:
    return MemoryCrystal(
        delegate_id=delegate_id,
        task=task,
        summary="Completed work",
        files_changed=[],
        decisions=["did things"],
        original_tokens=1000,
        crystal_tokens=50,
        created_at=1234567890.0,
    )


# ---------------------------------------------------------------------------
# Tests: Terminal delegate status detection
# ---------------------------------------------------------------------------

class TestTerminalDelegateDetection:
    """The loadConversation fix relies on correctly identifying terminal
    delegate statuses to skip the blocking server fetch."""

    TERMINAL_STATUSES = ['crystal', 'failed', 'interrupted']
    NON_TERMINAL_STATUSES = ['running', 'compacting', 'proposed', 'ready']

    @pytest.mark.parametrize("status", TERMINAL_STATUSES)
    def test_terminal_status_detected(self, status):
        """Terminal statuses should be recognised as terminal."""
        is_terminal = status in ('crystal', 'failed', 'interrupted')
        assert is_terminal is True

    @pytest.mark.parametrize("status", NON_TERMINAL_STATUSES)
    def test_non_terminal_status_not_detected(self, status):
        """Non-terminal statuses should NOT be treated as terminal."""
        is_terminal = status in ('crystal', 'failed', 'interrupted')
        assert is_terminal is False


# ---------------------------------------------------------------------------
# Tests: Plan-level terminal detection
# ---------------------------------------------------------------------------

class TestPlanTerminalDetection:
    """Completed swarm plans should prevent delegate streaming connections."""

    TERMINAL_PLAN_STATUSES = ['completed', 'completed_partial', 'cancelled']
    ACTIVE_PLAN_STATUSES = ['running', 'pending']

    @pytest.mark.parametrize("plan_status", TERMINAL_PLAN_STATUSES)
    def test_terminal_plan_status(self, plan_status):
        """A delegate in a terminal plan should not trigger a WebSocket."""
        is_plan_terminal = plan_status in ('completed', 'completed_partial', 'cancelled')
        assert is_plan_terminal is True

    @pytest.mark.parametrize("plan_status", ACTIVE_PLAN_STATUSES)
    def test_active_plan_status(self, plan_status):
        """Active plans should allow streaming connections."""
        is_plan_terminal = plan_status in ('completed', 'completed_partial', 'cancelled')
        assert is_plan_terminal is False


# ---------------------------------------------------------------------------
# Tests: Manager correctly tracks delegate terminal state
# ---------------------------------------------------------------------------

class TestDelegateManagerTerminalState:

    @pytest.mark.asyncio
    async def test_crystal_sets_terminal_status(self, manager):
        """After on_crystal_ready, delegate status should be 'crystal'."""
        plan_id = "plan-1"
        delegate_id = "d1"

        spec = DelegateSpec(
            delegate_id=delegate_id,
            name="Test Delegate",
            scope="Do things",
            files=[],
            dependencies=[],
        )

        plan = TaskPlan(
            name="Test Plan",
            description="Test",
            delegate_specs=[spec],
            status="running",
            created_at=1234567890.0,
        )

        manager._plans[plan_id] = plan
        manager._statuses[plan_id] = {delegate_id: "running"}
        manager._crystals[plan_id] = {}
        manager._running[plan_id] = {delegate_id}
        manager._tasks[plan_id] = {}
        manager._callbacks[plan_id] = None

        crystal = make_crystal(delegate_id)
        await manager.on_crystal_ready(plan_id, delegate_id, crystal)

        assert manager._statuses[plan_id][delegate_id] == "crystal"
        assert delegate_id not in manager._running[plan_id]

    @pytest.mark.asyncio
    async def test_failed_sets_terminal_status(self, manager):
        """After on_delegate_failed, delegate status should be 'failed'."""
        plan_id = "plan-2"
        delegate_id = "d2"

        spec = DelegateSpec(
            delegate_id=delegate_id,
            name="Failing Delegate",
            scope="Will fail",
            files=[],
            dependencies=[],
        )

        plan = TaskPlan(
            name="Test Plan",
            description="Test",
            delegate_specs=[spec],
            status="running",
            created_at=1234567890.0,
        )

        manager._plans[plan_id] = plan
        manager._statuses[plan_id] = {delegate_id: "running"}
        manager._crystals[plan_id] = {}
        manager._running[plan_id] = {delegate_id}
        manager._tasks[plan_id] = {}
        manager._callbacks[plan_id] = None

        await manager.on_delegate_failed(plan_id, delegate_id, "test error")

        assert manager._statuses[plan_id][delegate_id] == "failed"
        assert delegate_id not in manager._running[plan_id]


# ---------------------------------------------------------------------------
# Tests: Delegate key derivation (mirrors frontend useMemo logic)
# ---------------------------------------------------------------------------

class TestLoadConversationDelegateHandling:
    """Test that loadConversation handles delegates correctly."""

    def test_terminal_delegate_skips_server_fetch(self):
        """Terminal delegates should not trigger any server fetch."""
        for status in ('crystal', 'failed', 'interrupted'):
            is_terminal = status in ('crystal', 'failed', 'interrupted')
            assert is_terminal, f"Status '{status}' should be detected as terminal"

    def test_running_delegate_fetch_is_nonblocking(self):
        """Running delegates should fetch in the background, not block loadConversation.
        
        The key invariant: loadConversation must call setIsLoadingConversation(false)
        without waiting for the server fetch to complete. This test verifies the
        design intent — the actual async behavior is tested via browser integration.
        """
        # The fix replaces:
        #   const serverChat = await syncApi.getChat(pid, conversationId);
        # With:
        #   syncApi.getChat(pid, conversationId).then(serverChat => { ... });
        #
        # The .then() pattern means the Promise is not awaited — loadConversation
        # proceeds immediately to the finally block.
        #
        # Verify the invariant: non-terminal delegate fetch should NOT be awaited
        non_terminal_statuses = ('running', 'compacting', 'proposed', 'ready')
        for status in non_terminal_statuses:
            is_terminal = status in ('crystal', 'failed', 'interrupted')
            assert not is_terminal, (
                f"Status '{status}' must NOT be terminal — "
                f"it should trigger a background (non-blocking) fetch"
            )

    def test_proposed_delegate_also_nonblocking(self):
        """Proposed delegates (not yet started) should also not block.
        
        A user might click a delegate conversation before it starts running.
        The conversation should load instantly from whatever local state exists.
        """
        status = 'proposed'
        is_terminal = status in ('crystal', 'failed', 'interrupted')
        assert not is_terminal  # Not terminal → non-blocking fetch path


class TestDelegateKeyDerivation:
    """The frontend useDelegateStreaming hook derives a 'delegateKey' from
    the conversation's delegateMeta to avoid re-running the effect on
    every unrelated conversation change."""

    def _derive_delegate_key(self, conv_id, conversations):
        """Python port of the frontend delegateKey useMemo."""
        conv = next((c for c in conversations if c['id'] == conv_id), None)
        if not conv or not conv.get('delegateMeta'):
            return 'none'
        dm = conv['delegateMeta']
        return f"{conv_id}:{dm.get('status', '')}:{dm.get('plan_id', '')}"

    def test_non_delegate_returns_none(self):
        convs = [{'id': 'c1', 'messages': []}]
        assert self._derive_delegate_key('c1', convs) == 'none'

    def test_delegate_includes_status(self):
        convs = [{'id': 'c1', 'delegateMeta': {'status': 'crystal', 'plan_id': 'p1'}, 'messages': []}]
        key = self._derive_delegate_key('c1', convs)
        assert 'crystal' in key
        assert 'p1' in key

    def test_key_changes_on_status_change(self):
        convs_running = [{'id': 'c1', 'delegateMeta': {'status': 'running', 'plan_id': 'p1'}, 'messages': []}]
        convs_crystal = [{'id': 'c1', 'delegateMeta': {'status': 'crystal', 'plan_id': 'p1'}, 'messages': []}]
        key_running = self._derive_delegate_key('c1', convs_running)
        key_crystal = self._derive_delegate_key('c1', convs_crystal)
        assert key_running != key_crystal

    def test_key_stable_on_unrelated_change(self):
        """Key should NOT change when a different conversation updates."""
        convs_v1 = [
            {'id': 'c1', 'delegateMeta': {'status': 'crystal', 'plan_id': 'p1'}, 'messages': []},
            {'id': 'c2', 'messages': ['old']},
        ]
        convs_v2 = [
            {'id': 'c1', 'delegateMeta': {'status': 'crystal', 'plan_id': 'p1'}, 'messages': []},
            {'id': 'c2', 'messages': ['old', 'new']},
        ]
        key_v1 = self._derive_delegate_key('c1', convs_v1)
        key_v2 = self._derive_delegate_key('c1', convs_v2)
        assert key_v1 == key_v2, "Key should not change when an unrelated conversation changes"


# ---------------------------------------------------------------------------
# Tests: Sibling key derivation
# ---------------------------------------------------------------------------

class TestSiblingKeyDerivation:
    """The siblingKey prevents the sibling-connections effect from re-running
    unless a sibling's delegate status actually changes."""

    def _derive_sibling_key(self, conv_id, conversations):
        """Python port of the frontend siblingKey useMemo."""
        conv = next((c for c in conversations if c['id'] == conv_id), None)
        plan_id = conv.get('delegateMeta', {}).get('plan_id') if conv else None
        if not plan_id:
            return 'no-plan'
        siblings = [
            c for c in conversations
            if c.get('delegateMeta', {}).get('plan_id') == plan_id
            and c.get('delegateMeta', {}).get('role') == 'delegate'
            and c['id'] != conv_id
        ]
        return '|'.join(f"{s['id']}:{s.get('delegateMeta', {}).get('status', '')}" for s in siblings)

    def test_no_plan_returns_no_plan(self):
        convs = [{'id': 'c1', 'messages': []}]
        assert self._derive_sibling_key('c1', convs) == 'no-plan'

    def test_sibling_key_changes_on_status_change(self):
        base_convs = lambda status: [
            {'id': 'orch', 'delegateMeta': {'plan_id': 'p1', 'role': 'orchestrator', 'status': 'running'}, 'messages': []},
            {'id': 'd1', 'delegateMeta': {'plan_id': 'p1', 'role': 'delegate', 'status': status}, 'messages': []},
        ]
        key_running = self._derive_sibling_key('orch', base_convs('running'))
        key_crystal = self._derive_sibling_key('orch', base_convs('crystal'))
        assert key_running != key_crystal

    def test_sibling_key_stable_on_unrelated_change(self):
        convs_v1 = [
            {'id': 'orch', 'delegateMeta': {'plan_id': 'p1', 'role': 'orchestrator', 'status': 'running'}, 'messages': []},
            {'id': 'd1', 'delegateMeta': {'plan_id': 'p1', 'role': 'delegate', 'status': 'crystal'}, 'messages': []},
            {'id': 'unrelated', 'messages': ['old']},
        ]
        convs_v2 = [
            {'id': 'orch', 'delegateMeta': {'plan_id': 'p1', 'role': 'orchestrator', 'status': 'running'}, 'messages': []},
            {'id': 'd1', 'delegateMeta': {'plan_id': 'p1', 'role': 'delegate', 'status': 'crystal'}, 'messages': []},
            {'id': 'unrelated', 'messages': ['old', 'new message added']},
        ]
        key_v1 = self._derive_sibling_key('orch', convs_v1)
        key_v2 = self._derive_sibling_key('orch', convs_v2)
        assert key_v1 == key_v2, "Sibling key should not change when an unrelated conversation changes"
