"""
Seam test: the real ``git`` builtin tool through the real executor gate.

    execute_single_tool --gate--> ConsentLedger <--answer-- test
          │                                  (preflight = git_ops plan against
          └──> DirectMCPTool(GitTool).execute   the SAME project root)

Asserts on the repository itself, not on the tool's return value: approve must
change the index, reject must leave it untouched, and ``status`` must run
without ever opening a consent.  Also pins the fail-closed rule: a broken gate
must refuse a mutating git op instead of dispatching it unattended.
"""

import asyncio
import os
import subprocess
from unittest.mock import MagicMock

import pytest

os.environ.setdefault("ZIYA_TESTING", "1")

from app.tool_execution import ToolExecContext, execute_single_tool  # noqa: E402
from app.mcp.enhanced_tools import DirectMCPTool  # noqa: E402
from app.mcp.tools.git_tool import GitTool  # noqa: E402
from app.utils import consent_gate as cg  # noqa: E402
from app.utils import git_ops  # noqa: E402

CONV = "conv-git-seam"


def _git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True)


@pytest.fixture
def repo(tmp_path, monkeypatch):
    cg.reset_for_tests(tmp_path / "consents")
    monkeypatch.setattr(cg, "_consent_config", lambda: {})  # declared defaults only
    monkeypatch.setattr(
        git_ops, "default_write_check",
        lambda rel, root, exists: "" if rel.startswith("docs") else f"{rel} not writable",
    )
    root = tmp_path / "proj"
    root.mkdir()
    _git(root, "init", "-q", "-b", "main")
    for k, v in (("user.name", "T"), ("user.email", "t@example.invalid"),
                 ("commit.gpgsign", "false")):
        _git(root, "config", k, v)
    (root / "docs").mkdir()
    (root / "docs" / "a.md").write_text("a\n")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "init")
    (root / "docs" / "a.md").write_text("changed\n")
    yield root
    cg.reset_for_tests()


def _ctx(root, args) -> ToolExecContext:
    manager = MagicMock()
    manager._exceeds_turn_ceiling = lambda *_: False
    manager._is_repetitive_call = lambda *_: False
    tool = DirectMCPTool(GitTool())
    return ToolExecContext(
        tool_id="tc-git", tool_name="git", actual_tool_name="git", args=dict(args),
        all_tools=[tool], internal_tool_names=set(), mcp_manager=manager,
        project_root=str(root), conversation_id=CONV, conversation=[],
        recent_commands=[],
        inter_tool_delay={'current': 0.0, 'min': 0.0, 'max': 1, 'decay_factor': 0.6,
                          'growth_factor': 2.5, 'last_was_throttled': False},
        iteration_start_time=0, track_yield_fn=lambda x: x,
        drain_feedback_fn=lambda: [], executor=MagicMock(),
    )


async def _until_parked(gen, events, timeout=5.0):
    async def pull():
        async for ev in gen:
            events.append(ev)
            if ev.get("type") == "consent_opened":
                return
    await asyncio.wait_for(pull(), timeout)


async def _finish(gen, events, timeout=10.0):
    async def pull():
        async for ev in gen:
            events.append(ev)
    await asyncio.wait_for(pull(), timeout)


def _cached(root):
    return _git(root, "diff", "--cached", "--name-only").stdout.split()


def _tool_result(events):
    return [e for e in events if e.get("type") == "_tool_result"]


@pytest.mark.asyncio
async def test_add_parks_shows_the_real_preflight_and_stages_only_on_approve(repo):
    events = []
    gen = execute_single_tool(_ctx(repo, {"op": "add", "paths": ["."]}))
    await _until_parked(gen, events)
    opened = [e for e in events if e["type"] == "consent_opened"][-1]
    pf = opened["payload"]["preflight"]
    assert [s["path"] for s in pf["to_stage"]] == ["docs/a.md"], pf
    assert _cached(repo) == [], "nothing may be staged while waiting"
    cg.ledger().record_answer(opened["request_id"], "approve")
    await _finish(gen, events)
    assert _cached(repo) == ["docs/a.md"]


@pytest.mark.asyncio
async def test_reject_leaves_the_index_untouched_and_tells_the_model(repo):
    events = []
    gen = execute_single_tool(_ctx(repo, {"op": "add", "paths": ["docs"]}))
    await _until_parked(gen, events)
    rid = [e for e in events if e["type"] == "consent_opened"][-1]["request_id"]
    cg.ledger().record_answer(rid, "reject", answer="not yet")
    await _finish(gen, events)
    assert _cached(repo) == []
    results = _tool_result(events)
    assert results and "declined" in str(results[-1]["result"]) and "not yet" in str(results[-1]["result"])


@pytest.mark.asyncio
async def test_status_runs_without_asking(repo):
    events = []
    await _finish(execute_single_tool(_ctx(repo, {"op": "status"})), events)
    assert not [e for e in events if e.get("type") == "consent_opened"]
    results = _tool_result(events)
    assert results and "docs/a.md" in str(results[-1]["result"])


@pytest.mark.asyncio
async def test_a_broken_gate_refuses_a_mutating_op_instead_of_dispatching(repo, monkeypatch):
    async def exploding_gate(**_kw):
        raise RuntimeError("ledger unavailable")
        yield  # pragma: no cover — makes this an async generator

    monkeypatch.setattr(cg, "consent_gate", exploding_gate)
    events = []
    await _finish(execute_single_tool(_ctx(repo, {"op": "add", "paths": ["docs"]})), events)
    assert _cached(repo) == [], "must not dispatch unattended"
    results = _tool_result(events)
    assert results and "consent" in str(results[-1]["result"]).lower()
    # Positive control: a read-only op still runs through the same broken gate.
    events = []
    await _finish(execute_single_tool(_ctx(repo, {"op": "status"})), events)
    assert "docs/a.md" in str(_tool_result(events)[-1]["result"])
