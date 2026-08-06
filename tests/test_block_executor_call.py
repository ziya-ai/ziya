"""Tests for the Call block — invoking a named card or file task inline.

``execute_task_block`` is stubbed throughout so call semantics are checked
without model calls.  The properties under test are the ones that are not
obvious from reading the dispatcher:

  * the callee runs with the CALLEE's scope frame, not the caller's
    (which is what lets the callee's own signed approval verify);
  * a cycle and an over-deep chain both produce a failed artifact rather
    than an exception or a hang;
  * provenance lands in ``decisions``, never in ``summary``, so a caller
    Repeat's ``repeat_until`` substring match is unaffected.
"""

import pytest

from app.agents import block_executor
from app.agents.block_executor import (
    MAX_CALL_DEPTH,
    ExecutionContext,
    execute_block,
)
from app.agents.task_call import CallResolutionError, ResolvedCall
from app.models.task_card import Artifact, Block, ScopeEntry, TaskScope


def _task(bid: str, instr: str = "do it") -> Block:
    return Block(block_type="task", id=bid, name=bid, instructions=instr)


def _call(bid: str, target: str, kind=None) -> Block:
    return Block(block_type="call", id=bid, name=bid,
                 call_target=target, call_target_kind=kind)


@pytest.fixture
def ctx():
    return ExecutionContext(run_id="run-1", project_id="proj-1",
                            project_root="/tmp/proj")


@pytest.fixture
def recorder(monkeypatch):
    """Stub execute_task_block, recording the scope each task ran with."""
    seen = []

    async def _stub(block, project_root=None, project_id=None, run_id=None,
                    pre_authorized_shell_commands=None,
                    pre_authorized_writable=None):
        seen.append({
            "id": block.id,
            "scope": block.scope,
            "pre_shell": list(pre_authorized_shell_commands or []),
            "pre_writable": list(pre_authorized_writable or []),
        })
        return Artifact(summary=f"ran {block.id}")

    monkeypatch.setattr(block_executor, "execute_task_block", _stub)
    return seen


def _stub_resolver(monkeypatch, table):
    """Resolve call targets from a dict of name -> ResolvedCall."""
    def _resolve(target, kind, *, project_id, project_root):
        if target not in table:
            raise CallResolutionError(f"no such target {target!r}")
        return table[target]
    monkeypatch.setattr("app.agents.task_call.resolve_call_target", _resolve)


# ── happy path ──────────────────────────────────────────────────────────

def test_call_runs_callee_and_adopts_its_artifact(monkeypatch, ctx, recorder):
    _stub_resolver(monkeypatch, {
        "Helper": ResolvedCall(kind="card", key="card:c2", label="Helper",
                               root=_task("callee-1")),
    })
    art = _run(execute_block(_call("call-1", "Helper"), ctx))
    assert [s["id"] for s in recorder] == ["callee-1"]
    assert art.summary == "ran callee-1"
    assert art.failed is False


def test_provenance_goes_in_decisions_not_summary(monkeypatch, ctx, recorder):
    # A caller Repeat may substring-match the summary via repeat_until, so
    # decorating the summary would silently change the caller's loop
    # condition.  Provenance therefore belongs in decisions only.
    _stub_resolver(monkeypatch, {
        "Helper": ResolvedCall(kind="card", key="card:c2", label="Helper",
                               root=_task("callee-1")),
    })
    art = _run(execute_block(_call("call-1", "Helper"), ctx))
    assert art.summary == "ran callee-1"
    assert any("Helper" in d for d in art.decisions)


def test_callee_artifact_registered_under_call_block_id(monkeypatch, ctx, recorder):
    _stub_resolver(monkeypatch, {
        "Helper": ResolvedCall(kind="card", key="card:c2", label="Helper",
                               root=_task("callee-1")),
    })
    _run(execute_block(_call("call-1", "Helper"), ctx))
    assert ctx.artifact_registry["call-1"].summary == "ran callee-1"


# ── scope isolation (the security-relevant property) ────────────────────

def test_caller_scope_does_not_reach_callee(monkeypatch, ctx, recorder):
    # The caller grants a shell command; the callee must not see it.  If it
    # did, authorize_scope would hash a scope the callee's own approval was
    # never signed over, flooring an approved callee — and a caller the
    # agent may freely author would confer grants it never earned.
    ctx.card_scope = TaskScope(shell_commands=["rm"])
    _stub_resolver(monkeypatch, {
        "Helper": ResolvedCall(kind="card", key="card:c2", label="Helper",
                               root=_task("callee-1"),
                               card_scope=TaskScope(shell_commands=["pytest"])),
    })
    _run(execute_block(_call("call-1", "Helper"), ctx))
    granted = recorder[0]["scope"].shell_commands
    assert "pytest" in granted
    assert "rm" not in granted


def test_ancestor_block_scope_does_not_reach_callee(monkeypatch, ctx, recorder):
    outer = Block(block_type="group", id="g1", name="g1",
                  scope=TaskScope(shell_commands=["curl"]),
                  body=[_call("call-1", "Helper")])
    _stub_resolver(monkeypatch, {
        "Helper": ResolvedCall(kind="card", key="card:c2", label="Helper",
                               root=_task("callee-1")),
    })
    _run(execute_block(outer, ctx))
    scope = recorder[0]["scope"]
    assert scope is None or "curl" not in (scope.shell_commands or [])


def test_deck_scope_still_reaches_callee(monkeypatch, ctx, recorder):
    # deck_scope is the project-wide baseline and both cards live in the
    # same project, so it is deliberately NOT reset at the boundary.
    ctx.deck_scope = TaskScope(paths=[ScopeEntry(path="src", is_dir=True)])
    _stub_resolver(monkeypatch, {
        "Helper": ResolvedCall(kind="card", key="card:c2", label="Helper",
                               root=_task("callee-1")),
    })
    _run(execute_block(_call("call-1", "Helper"), ctx))
    assert [e.path for e in recorder[0]["scope"].paths] == ["src"]


def test_caller_scope_restored_after_call(monkeypatch, ctx, recorder):
    ctx.card_scope = TaskScope(shell_commands=["rm"])
    _stub_resolver(monkeypatch, {
        "Helper": ResolvedCall(kind="card", key="card:c2", label="Helper",
                               root=_task("callee-1")),
    })
    root = Block(block_type="group", id="g1", name="g1", body=[
        _call("call-1", "Helper"), _task("after-1"),
    ])
    _run(execute_block(root, ctx))
    after = next(s for s in recorder if s["id"] == "after-1")
    assert "rm" in after["scope"].shell_commands
    assert ctx.scope_stack == []
    assert ctx.call_stack == []


# ── file-task targets ───────────────────────────────────────────────────

def test_file_task_grants_passed_as_pre_authorized(monkeypatch, ctx, recorder):
    # A file task's allow block is authorized under a CLI-ledger key that
    # execute_task_block cannot check, so it arrives as an explicit
    # pre-authorized grant rather than as a TaskScope (which would be
    # re-hashed against a synthetic block id and denied).
    _stub_resolver(monkeypatch, {
        "release": ResolvedCall(
            kind="file_task", key="file_task:release", label="release",
            root=_task("synthetic-1"),
            shell_grants=["pytest"],
            writable_grants=[{"pattern": "*.toml"}],
        ),
    })
    _run(execute_block(_call("call-1", "release", kind="file_task"), ctx))
    assert recorder[0]["pre_shell"] == ["pytest"]
    assert recorder[0]["pre_writable"] == [{"pattern": "*.toml"}]


def test_unapproved_file_task_notes_surface_in_decisions(monkeypatch, ctx, recorder):
    _stub_resolver(monkeypatch, {
        "release": ResolvedCall(
            kind="file_task", key="file_task:release", label="release",
            root=_task("synthetic-1"),
            notes=["call: escalation for file task 'release' is not approved"],
        ),
    })
    art = _run(execute_block(_call("call-1", "release", kind="file_task"), ctx))
    assert any("not approved" in d for d in art.decisions)


# ── failure modes ───────────────────────────────────────────────────────

def test_unresolvable_target_fails_without_raising(monkeypatch, ctx, recorder):
    _stub_resolver(monkeypatch, {})
    art = _run(execute_block(_call("call-1", "Nope"), ctx))
    assert art.failed is True
    assert "could not be resolved" in art.summary
    assert recorder == []


def test_empty_target_fails(ctx, recorder):
    art = _run(execute_block(_call("call-1", ""), ctx))
    assert art.failed is True


def test_direct_cycle_rejected(monkeypatch, ctx, recorder):
    # A card that calls itself.  Depth alone would not catch this usefully:
    # it would burn the whole budget re-running the same work.
    self_call = _call("call-inner", "Self")
    _stub_resolver(monkeypatch, {
        "Self": ResolvedCall(kind="card", key="card:c1", label="Self",
                             root=self_call),
    })
    art = _run(execute_block(_call("call-1", "Self"), ctx))
    assert art.failed is True
    assert "cycle" in art.summary


def test_mutual_cycle_rejected(monkeypatch, ctx, recorder):
    _stub_resolver(monkeypatch, {
        "A": ResolvedCall(kind="card", key="card:a", label="A",
                          root=_call("call-b", "B")),
        "B": ResolvedCall(kind="card", key="card:b", label="B",
                          root=_call("call-a", "A")),
    })
    art = _run(execute_block(_call("call-1", "A"), ctx))
    assert art.failed is True
    assert "cycle" in art.summary


def test_depth_cap_enforced_on_acyclic_chain(monkeypatch, ctx, recorder):
    # Distinct targets each time, so cycle detection cannot fire — only the
    # depth cap can stop this.
    table = {}
    for i in range(MAX_CALL_DEPTH + 3):
        table[f"c{i}"] = ResolvedCall(
            kind="card", key=f"card:{i}", label=f"c{i}",
            root=_call(f"call-{i}", f"c{i + 1}"),
        )
    _stub_resolver(monkeypatch, table)
    art = _run(execute_block(_call("call-root", "c0"), ctx))
    assert art.failed is True
    assert "depth limit" in art.summary


# ── audit trail (run map + side-effect reporting) ────────────────────────

class _FakeStorage:
    """Minimal TaskRunStorage stand-in recording record_call arguments."""

    def __init__(self):
        self.calls = []
        self.states = {}

    def get(self, run_id):
        return None          # cancel/pause checks read as False

    def set_block_state(self, run_id, state):
        self.states[state.block_id] = state

    def update_block_status(self, *a, **k):
        pass

    def record_call(self, run_id, block_id, call_snapshot, block_scopes=None):
        self.calls.append((block_id, call_snapshot, block_scopes or {}))


def test_call_records_callee_tree_for_the_run_map(monkeypatch, ctx, recorder):
    # Without the tree the map draws a call row whose artifact came from
    # nothing, while the callee's status events land on no row at all.
    ctx.storage = _FakeStorage()
    _stub_resolver(monkeypatch, {
        "Helper": ResolvedCall(kind="card", key="card:c2", label="Helper",
                               root=_task("callee-1")),
    })
    _run(execute_block(_call("call-1", "Helper"), ctx))
    assert len(ctx.storage.calls) == 1
    block_id, snap, _ = ctx.storage.calls[0]
    assert block_id == "call-1"
    assert snap["target"] == "Helper"
    assert snap["key"] == "card:c2"
    assert snap["root"]["id"] == "callee-1"


def test_call_records_callee_write_grant_in_block_scopes(monkeypatch, ctx, recorder):
    # The security-relevant half: a callee holding write access must reach
    # permissions_snapshot, or summarize_side_effects intersects it to
    # nothing and reports NO workspace hazard.
    ctx.storage = _FakeStorage()
    callee_scope = TaskScope(paths=[
        ScopeEntry(path="out/", is_dir=True, write=True),
    ])
    _stub_resolver(monkeypatch, {
        "Helper": ResolvedCall(kind="card", key="card:c2", label="Helper",
                               root=_task("callee-1"),
                               card_scope=callee_scope),
    })
    _run(execute_block(_call("call-1", "Helper"), ctx))
    _, _, scopes = ctx.storage.calls[0]
    assert scopes["callee-1"]["paths"][0]["write"] is True
    assert scopes["callee-1"]["via_call"]["call_block_id"] == "call-1"


def test_file_task_grants_recorded_as_write_patterns(monkeypatch, ctx, recorder):
    ctx.storage = _FakeStorage()
    _stub_resolver(monkeypatch, {
        "release": ResolvedCall(
            kind="file_task", key="file_task:release", label="release",
            root=_task("synthetic-1"),
            shell_grants=["pytest"],
            writable_grants=[{"pattern": "*.toml"}],
        ),
    })
    _run(execute_block(_call("call-1", "release", kind="file_task"), ctx))
    _, _, scopes = ctx.storage.calls[0]
    assert scopes["synthetic-1"]["write_patterns"] == ["*.toml"]
    assert scopes["synthetic-1"]["shell_commands"] == ["pytest"]


def test_caller_card_scope_absent_from_recorded_callee_scopes(monkeypatch, ctx, recorder):
    # The audit must not credit the callee with the caller's grants —
    # the run-time isolation means it never held them.
    ctx.storage = _FakeStorage()
    ctx.card_scope = TaskScope(shell_commands=["rm"])
    _stub_resolver(monkeypatch, {
        "Helper": ResolvedCall(kind="card", key="card:c2", label="Helper",
                               root=_task("callee-1"),
                               card_scope=TaskScope(shell_commands=["pytest"])),
    })
    _run(execute_block(_call("call-1", "Helper"), ctx))
    _, _, scopes = ctx.storage.calls[0]
    assert scopes["callee-1"]["shell_commands"] == ["pytest"]


def test_audit_failure_does_not_break_the_call(monkeypatch, ctx, recorder):
    class _Boom(_FakeStorage):
        def record_call(self, *a, **k):
            raise RuntimeError("disk full")
    ctx.storage = _Boom()
    _stub_resolver(monkeypatch, {
        "Helper": ResolvedCall(kind="card", key="card:c2", label="Helper",
                               root=_task("callee-1")),
    })
    art = _run(execute_block(_call("call-1", "Helper"), ctx))
    assert art.failed is False
    assert art.summary == "ran callee-1"


def _run(coro):
    import asyncio
    return asyncio.run(coro)
