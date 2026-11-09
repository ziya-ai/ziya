"""
The per-turn bead summary must see the conversation's beads on the turn pool.

Reported 2026-11-09 (.ziya/diagnostics/ziya-beads-phantom-failure-2026-11-09.md):
every bead_create had succeeded (68 creates, all ok in the audit log), but
build_messages_for_streaming ran on the turn pool under a bare
run_in_executor, which does not copy contextvars.  load_bead_tree() then
found no conversation id, returned an empty tree, and the model was told
"Bead check: no threads tracked yet" every turn, so it re-created its
beads and told the user the earlier saves had failed.

Covered here:
  * via run_on_turn_executor with the id only in the ContextVar, the
    summary lists the parked beads (and a bare run_in_executor control
    reproduces the empty-tree nudge, so the test distinguishes the two);
  * an explicit conversation_id works with no request context at all;
  * a tree with only completed beads is not described as "no threads
    tracked yet";
  * the directive tells the model bead calls are absent from replay.
"""
import asyncio
import functools

import pytest

from app.models.bead import Bead
from app.utils.bead_prompt import BEAD_DIRECTIVE, get_bead_status_summary
from app.utils.turn_executor import run_on_turn_executor, turn_executor

CID = "conv-bead-turnpool-0001"


@pytest.fixture
def bead_home(tmp_path, monkeypatch):
    """Isolated ZIYA_HOME with no project root, so beads use the fallback store."""
    monkeypatch.setenv("ZIYA_HOME", str(tmp_path))
    monkeypatch.delenv("ZIYA_USER_CODEBASE_DIR", raising=False)
    monkeypatch.setattr("app.mcp.builtin_tools.is_builtin_category_enabled",
                        lambda name: True)
    monkeypatch.setattr("app.utils.bead_prompt._is_global_ephemeral", lambda: False)
    return tmp_path


def _seed(cid, beads):
    from app.storage.beads import _save_fallback
    _save_fallback(cid, [b.model_dump() for b in beads])


def _parked_tree():
    return [
        Bead(id="b_main", content="Beads phantom-failure bug", status="active"),
        Bead(id="b_p1", content="sat Reconciler session churn", status="parked",
             parent_id="b_main"),
        Bead(id="b_p2", content="hio501 switch-mgr error storm", status="parked",
             parent_id="b_main"),
    ]


async def _in_fresh_task(coro_fn):
    # A task gets its own copy of the context: set_conversation_id inside
    # cannot leak into other tests.
    return await asyncio.get_running_loop().create_task(coro_fn())


async def test_turn_pool_summary_lists_parked_beads(bead_home):
    _seed(CID, _parked_tree())

    async def _body():
        from app.context import set_conversation_id
        set_conversation_id(CID)
        on_pool = await run_on_turn_executor(get_bead_status_summary, 5)
        # Control: the pre-fix call shape (no context copy) loses the id.
        bare = await asyncio.get_running_loop().run_in_executor(
            turn_executor(), functools.partial(get_bead_status_summary, 5))
        return on_pool, bare

    on_pool, bare = await _in_fresh_task(_body)
    assert "### Pending Beads (parked threads):" in on_pool
    assert "sat Reconciler session churn" in on_pool
    assert "hio501 switch-mgr error storm" in on_pool
    assert "Currently working on: Beads phantom-failure bug" in on_pool
    assert "no threads tracked yet" not in on_pool
    assert "no threads tracked yet" in bare, (
        "control did not reproduce the empty-tree read; the test cannot "
        "tell a context-propagating pool from a bare one")


async def test_explicit_conversation_id_needs_no_request_context(bead_home):
    _seed(CID, _parked_tree())

    async def _body():
        # Nothing set in this context.
        explicit = await asyncio.get_running_loop().run_in_executor(
            turn_executor(),
            functools.partial(get_bead_status_summary, 5, conversation_id=CID))
        implicit = get_bead_status_summary(5)
        return explicit, implicit

    explicit, implicit = await _in_fresh_task(_body)
    assert "sat Reconciler session churn" in explicit
    assert "Pending Beads" in explicit
    # Positive control: without the id and without context, it is empty.
    assert "no threads tracked yet" in implicit


def test_completed_only_tree_is_not_reported_as_empty(bead_home):
    _seed(CID, [Bead(id="b_done", content="finished thing", status="completed")])
    out = get_bead_status_summary(5, conversation_id=CID)
    assert "no threads tracked yet" not in out
    assert "no parked threads (1 bead(s) on record, none parked)" in out
    # Short conversations stay silent either way.
    assert get_bead_status_summary(1, conversation_id=CID) == ""


def test_empty_tree_keeps_the_original_nudge(bead_home):
    out = get_bead_status_summary(5, conversation_id="conv-with-no-beads")
    assert out.startswith("\n\n### Bead check: no threads tracked yet.")


def test_directive_says_bead_calls_are_not_replayed():
    assert "Bead tool calls are NOT replayed" in BEAD_DIRECTIVE
    assert "do not re-create a bead" in BEAD_DIRECTIVE
    # The binding on the current turn's wording is kept.
    assert 'are a CLAIM' in BEAD_DIRECTIVE
