"""
Seam: an out-of-policy write approved in the consent panel actually happens,
through the real executor (design/scope-consent.md).

    execute_single_tool --gate(preflight: outside_policy)--> panel --approve-->
        decision.write_grant --executor activates--> tool.execute
            --> write_grants.check (policy, then grant) --> disk

Asserts on the filesystem, not on return values.  Every approve case is
paired with a reject case that must leave the file untouched, and with a
check that the grant does not outlive the call.

These need the executor to activate ``decision["write_grant"]`` around
execution (app/tool_execution.py) and, for file_write, its preflight and
grant-aware check (app/mcp/tools/fileio.py).  Without those they fail: the
approval is given and the write is still refused.
"""

import asyncio
import os
import subprocess
from unittest.mock import MagicMock

import pytest

os.environ.setdefault("ZIYA_TESTING", "1")

from app.tool_execution import ToolExecContext, execute_single_tool  # noqa: E402
from app.mcp.enhanced_tools import DirectMCPTool  # noqa: E402
from app.mcp.tools.fileio import FileWriteTool  # noqa: E402
from app.mcp.tools.git_tool import GitTool  # noqa: E402
from app.utils import consent_gate as cg  # noqa: E402
from app.utils import write_grants as wg  # noqa: E402

CONV = "conv-scope-seam"
WAIT = 30.0   # first execute_single_tool in a process pays for cold imports


def _git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True)


@pytest.fixture
def repo(tmp_path, monkeypatch):
    cg.reset_for_tests(tmp_path / "consents")
    monkeypatch.setattr(cg, "_consent_config", lambda: {})
    monkeypatch.setattr(
        wg, "_default_base",
        lambda rel, root, exists: "" if str(rel).startswith("docs") else f"{rel} not writable")
    root = tmp_path / "proj"
    root.mkdir()
    _git(root, "init", "-q", "-b", "main")
    for k, v in (("user.name", "T"), ("user.email", "t@example.invalid"),
                 ("commit.gpgsign", "false")):
        _git(root, "config", k, v)
    (root / "docs").mkdir()
    (root / "docs" / "a.md").write_text("a\n")
    (root / "src.py").write_text("x = 1\n")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "init")
    yield root
    cg.reset_for_tests()


def _ctx(root, tool, name, args, tid=None) -> ToolExecContext:
    manager = MagicMock()
    manager._exceeds_turn_ceiling = lambda *_: False
    manager._is_repetitive_call = lambda *_: False
    return ToolExecContext(
        tool_id=tid or f"tc-{name}", tool_name=name, actual_tool_name=name, args=dict(args),
        all_tools=[DirectMCPTool(tool)], internal_tool_names=set(), mcp_manager=manager,
        project_root=str(root), conversation_id=CONV, conversation=[],
        recent_commands=[],
        inter_tool_delay={'current': 0.0, 'min': 0.0, 'max': 1, 'decay_factor': 0.6,
                          'growth_factor': 2.5, 'last_was_throttled': False},
        iteration_start_time=0, track_yield_fn=lambda x: x,
        drain_feedback_fn=lambda: [], executor=MagicMock(),
    )


async def _until_parked(gen, events):
    async def pull():
        async for ev in gen:
            events.append(ev)
            if ev.get("type") == "consent_opened":
                return
    await asyncio.wait_for(pull(), WAIT)


async def _finish(gen, events):
    async def pull():
        async for ev in gen:
            events.append(ev)
    await asyncio.wait_for(pull(), WAIT)


def _opened(events):
    return [e for e in events if e.get("type") == "consent_opened"][-1]


def _result(events):
    return str([e for e in events if e.get("type") == "_tool_result"][-1]["result"])


# ── git checkout (C1) ─────────────────────────────────────────────────────

@pytest.mark.asyncio
@pytest.mark.parametrize("decision, expect", [("approve", "x = 1\n"), ("reject", "edited\n")])
async def test_git_checkout_out_of_policy_follows_the_decision(repo, decision, expect):
    (repo / "src.py").write_text("edited\n")
    events = []
    gen = execute_single_tool(_ctx(repo, GitTool(), "git", {"op": "checkout", "paths": ["src.py"]}))
    await _until_parked(gen, events)
    opened = _opened(events)
    assert [o["path"] for o in opened["payload"]["outside_policy"]] == ["src.py"]
    assert (repo / "src.py").read_text() == "edited\n", "nothing written while waiting"
    cg.ledger().record_answer(opened["request_id"], decision)
    await _finish(gen, events)
    assert (repo / "src.py").read_text() == expect, _result(events)
    assert wg.active() is None, "the grant must not outlive the call"


@pytest.mark.asyncio
async def test_git_commit_of_an_out_of_policy_file_needs_no_grant(repo):
    """Part A: commit records existing content; the panel shows no policy rows."""
    (repo / "src.py").write_text("x = 2\n")
    events = []
    gen = execute_single_tool(_ctx(repo, GitTool(), "git",
                                   {"op": "commit", "message": "m", "paths": ["src.py"]}))
    await _until_parked(gen, events)
    assert "outside_policy" not in _opened(events)["payload"]
    cg.ledger().record_answer(_opened(events)["request_id"], "approve")
    await _finish(gen, events)
    assert _git(repo, "show", "--name-only", "--format=", "HEAD").stdout.split() == ["src.py"]


@pytest.mark.asyncio
async def test_git_preflight_refusal_returns_without_a_panel(repo):
    events = []
    await _finish(execute_single_tool(_ctx(repo, GitTool(), "git",
                                           {"op": "commit", "message": "m"})), events)
    assert not [e for e in events if e.get("type") == "consent_opened"]
    text = _result(events)
    assert "was not run" in text and "nothing staged" in text and "declined" not in text


# ── file_write ────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_file_write_inside_policy_does_not_ask(repo):
    events = []
    await _finish(execute_single_tool(_ctx(repo, FileWriteTool(), "file_write",
                                           {"path": "docs/b.md", "content": "b\n"})), events)
    assert not [e for e in events if e.get("type") == "consent_opened"]
    assert (repo / "docs" / "b.md").read_text() == "b\n"


@pytest.mark.asyncio
@pytest.mark.parametrize("decision, expect", [("approve", "x = 2\n"), ("reject", "x = 1\n")])
async def test_file_write_out_of_policy_asks_with_a_diff_and_follows_the_decision(
        repo, decision, expect):
    events = []
    gen = execute_single_tool(_ctx(repo, FileWriteTool(), "file_write",
                                   {"path": "src.py", "content": "x = 2\n"}))
    await _until_parked(gen, events)
    pf = _opened(events)["payload"]["preflight"]
    assert pf["outside_policy"][0]["path"] == "src.py"
    assert "-x = 1" in pf["diff"] and "+x = 2" in pf["diff"]
    assert (repo / "src.py").read_text() == "x = 1\n"
    cg.ledger().record_answer(_opened(events)["request_id"], decision)
    await _finish(gen, events)
    assert (repo / "src.py").read_text() == expect, _result(events)
    assert wg.active() is None


@pytest.mark.asyncio
async def test_a_conversation_scope_approval_remembers_the_path_not_more(repo):
    """Regression: a conversation-scope approval asked again on the next
    write to the same file.  It now remembers that exact path for file_write
    in this conversation, and nothing else."""
    events = []
    gen = execute_single_tool(_ctx(repo, FileWriteTool(), "file_write",
                                   {"path": "src.py", "content": "x = 2\n"}))
    await _until_parked(gen, events)
    cg.ledger().record_answer(_opened(events)["request_id"], "approve", scope="conversation")
    await _finish(gen, events)
    assert (repo / "src.py").read_text() == "x = 2\n"

    # Same path: written without a panel.
    events = []
    await _finish(execute_single_tool(_ctx(repo, FileWriteTool(), "file_write",
                                           {"path": "src.py", "content": "x = 3\n"},
                                           tid="tc-second")), events)
    assert not [e for e in events if e.get("type") == "consent_opened"]
    assert (repo / "src.py").read_text() == "x = 3\n", _result(events)
    assert wg.active() is None, "the remembered approval still grants per call only"

    # A different out-of-policy path still asks, and nothing lands meanwhile.
    events = []
    gen = execute_single_tool(_ctx(repo, FileWriteTool(), "file_write",
                                   {"path": "other.py", "content": "z\n"}, tid="tc-third"))
    await _until_parked(gen, events)
    await gen.aclose()
    assert not (repo / "other.py").exists()


@pytest.mark.asyncio
async def test_a_once_approval_asks_again_for_the_same_path(repo):
    events = []
    gen = execute_single_tool(_ctx(repo, FileWriteTool(), "file_write",
                                   {"path": "src.py", "content": "x = 2\n"}))
    await _until_parked(gen, events)
    cg.ledger().record_answer(_opened(events)["request_id"], "approve", scope="once")
    await _finish(gen, events)
    events = []
    gen = execute_single_tool(_ctx(repo, FileWriteTool(), "file_write",
                                   {"path": "src.py", "content": "x = 3\n"}, tid="tc-second"))
    await _until_parked(gen, events)                 # asks again
    await gen.aclose()
    assert (repo / "src.py").read_text() == "x = 2\n"
