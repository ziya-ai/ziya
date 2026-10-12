"""
Out-of-policy writes behind human approval (design/scope-consent.md).

Three layers:
  * app/utils/write_grants.py: a grant covers exactly the approved paths, for
    the current context only, and never the floor;
  * git_ops under an active grant: the worktree ops that used to refuse now
    run for granted paths only, and nothing new slips in;
  * consent_gate: a preflight refusal returns without a panel (part B), and
    ``outside_policy`` always asks and yields a per-call grant (C1).
"""

import asyncio
import os
import subprocess

import pytest

from app.utils import consent_gate as cg
from app.utils import git_ops as g
from app.utils import write_grants as wg


def docs_only(rel, root, exists):
    return "" if rel == "docs" or rel.startswith("docs" + os.sep) else f"{rel} is not writable"


def granted_check(rel, root, exists):
    """Production composition (policy, then grant) with a test policy."""
    return wg.check(rel, root, exists, base=docs_only)


def _git(cwd, *args, check=True):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=check)


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "proj"
    root.mkdir()
    _git(root, "init", "-q", "-b", "main")
    for k, v in (("user.name", "T"), ("user.email", "t@example.invalid"),
                 ("commit.gpgsign", "false"), ("core.hooksPath", "/dev/null")):
        _git(root, "config", k, v)
    (root / "docs").mkdir()
    (root / "docs" / "a.md").write_text("a\n")
    (root / "src.py").write_text("x = 1\n")
    (root / "lib.py").write_text("y = 1\n")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "init")
    return root


# ── write_grants ──────────────────────────────────────────────────────────

def test_no_grant_means_the_base_policy_decides(tmp_path):
    assert wg.check("docs/x", str(tmp_path), True, base=docs_only) == ""
    assert "not writable" in wg.check("src.py", str(tmp_path), True, base=docs_only)


def test_grant_covers_exactly_the_listed_paths_and_ends_on_reset(tmp_path):
    tok = wg.activate({"root": str(tmp_path), "paths": ["src.py"]})
    try:
        assert wg.check("src.py", str(tmp_path), True, base=docs_only) == ""
        assert wg.check("./src.py", str(tmp_path), True, base=docs_only) == ""
        assert wg.check(str(tmp_path / "src.py"), str(tmp_path), True, base=docs_only) == ""
        assert "not writable" in wg.check("lib.py", str(tmp_path), True, base=docs_only)
    finally:
        wg.reset(tok)
    assert "not writable" in wg.check("src.py", str(tmp_path), True, base=docs_only)


def test_grant_is_bound_to_its_root(tmp_path):
    other = tmp_path / "other"
    other.mkdir()
    tok = wg.activate({"root": str(tmp_path), "paths": ["src.py"]})
    try:
        assert "not writable" in wg.check("src.py", str(other), True, base=docs_only)
    finally:
        wg.reset(tok)


@pytest.mark.parametrize("path", ["../escape.txt", ".git/config", "/etc/passwd"])
def test_the_floor_is_never_granted(tmp_path, path):
    assert wg.floor_reason(path, str(tmp_path))
    tok = wg.activate({"root": str(tmp_path), "paths": [path]})
    try:
        assert wg.check(path, str(tmp_path), True, base=lambda *a: "blocked") == "blocked"
    finally:
        wg.reset(tok)


def test_a_symlink_out_of_the_project_is_floor(tmp_path):
    proj, out = tmp_path / "p", tmp_path / "out"
    proj.mkdir(); out.mkdir()
    (proj / "link").symlink_to(out)
    assert "outside the project root" in wg.floor_reason("link/x", str(proj))


def test_grant_reaches_a_thread_started_inside_the_call(tmp_path):
    async def call():
        return await asyncio.to_thread(wg.covers, "src.py", str(tmp_path))
    tok = wg.activate({"root": str(tmp_path), "paths": ["src.py"]})
    try:
        assert asyncio.run(call()) is True
    finally:
        wg.reset(tok)


def test_classify_separates_grantable_from_floor(tmp_path):
    assert wg.classify("docs/a", str(tmp_path), True, base=docs_only) == ("", False)
    reason, grantable = wg.classify("src.py", str(tmp_path), True, base=docs_only)
    assert reason and grantable
    reason, grantable = wg.classify(".git/x", str(tmp_path), True, base=lambda *a: "no")
    assert reason == "no" and not grantable


def test_write_preview_is_a_capped_unified_diff():
    d = wg.write_preview("a\n", "b\n", "f.txt")
    assert "-a" in d and "+b" in d and "a/f.txt" in d
    assert "/dev/null" in wg.write_preview(None, "new\n", "f.txt")
    assert "more characters" in wg.write_preview(None, "x\n" * 10000, "big")


# ── git_ops under a grant ─────────────────────────────────────────────────

def test_checkout_runs_for_granted_paths_only(repo):
    (repo / "src.py").write_text("edited\n")
    (repo / "docs" / "a.md").write_text("edited\n")
    pf = g.preflight("checkout", {"paths": ["."]}, str(repo), write_check=granted_check)
    assert [o["path"] for o in pf["outside_policy"]] == ["src.py"]
    tok = wg.activate({"root": str(repo), "paths": ["src.py"]})
    try:
        out = g.run_op("checkout", {"paths": ["."]}, str(repo), write_check=granted_check)
    finally:
        wg.reset(tok)
    assert not out.get("error"), out
    assert "outside_policy" not in out
    assert (repo / "src.py").read_text() == "x = 1\n"
    assert (repo / "docs" / "a.md").read_text() == "a\n"


def test_a_path_that_appears_after_approval_is_not_covered(repo):
    """The grant is the list the human saw; a grown plan refuses as a whole."""
    (repo / "src.py").write_text("edited\n")
    tok = wg.activate({"root": str(repo), "paths": ["src.py"]})
    try:
        (repo / "lib.py").write_text("edited too\n")       # appears after approval
        out = g.run_op("checkout", {"paths": ["."]}, str(repo), write_check=granted_check)
    finally:
        wg.reset(tok)
    assert out["error"] and [o["path"] for o in out["outside_policy"]] == ["lib.py"]
    assert (repo / "src.py").read_text() == "edited\n", "nothing written"


def test_stash_pop_runs_under_a_grant(repo):
    (repo / "src.py").write_text("stashed\n")
    _git(repo, "stash", "push", "-q")
    tok = wg.activate({"root": str(repo), "paths": ["src.py"]})
    try:
        out = g.run_op("stash_pop", {}, str(repo), write_check=granted_check)
    finally:
        wg.reset(tok)
    assert not out.get("error"), out
    assert (repo / "src.py").read_text() == "stashed\n"


# ── the gate ──────────────────────────────────────────────────────────────

class _Writer:
    """A tool with a scope preflight, consent otherwise off (like file_write)."""
    name = "writer"
    scope_preflight = True

    def __init__(self, preflight):
        self._pf = preflight

    def preflight(self, **_):
        return self._pf


class _Asker(_Writer):
    name = "asker"
    consent_default = "ask"
    consent_op_key = "op"


@pytest.fixture
def gate(tmp_path, monkeypatch):
    cg.reset_for_tests(tmp_path / "consents")
    monkeypatch.setattr(cg, "_consent_config", lambda: {})
    yield
    cg.reset_for_tests()


async def _drain(gen):
    return [ev async for ev in gen]


def _decision(events):
    return [e for e in events if e["type"] == cg.DECISION_EVENT][-1]


OUT = {"op": "write", "outside_policy": [{"path": "src.py", "reason": "src.py is not writable"}]}


@pytest.mark.asyncio
async def test_in_policy_write_with_consent_off_runs_without_asking(gate):
    ev = await _drain(cg.consent_gate(tool_name="writer", tool_id="t", args={},
                                      conversation_id="c", tool=_Writer(None),
                                      project_root="/p"))
    assert [e["type"] for e in ev] == [cg.DECISION_EVENT]
    assert ev[0]["approved"] is True and cg.ledger().open_for("c") == []


@pytest.mark.asyncio
async def test_out_of_policy_write_asks_even_with_consent_off_and_grants_on_approve(gate):
    gen = cg.consent_gate(tool_name="writer", tool_id="t", args={"path": "src.py"},
                          conversation_id="c", tool=_Writer(OUT), project_root="/p")
    opened = await gen.__anext__()
    assert opened["type"] == "consent_opened"
    assert opened["payload"]["outside_policy"] == OUT["outside_policy"]
    cg.ledger().record_answer(opened["request_id"], "approve", scope="conversation")
    rest = [ev async for ev in gen]
    d = _decision(rest)
    assert d["approved"] is True
    assert d["write_grant"] == {"root": "/p", "paths": ["src.py"]}
    answered = [e for e in rest if e["type"] == "consent_answered"][-1]
    assert answered["write_grant_paths"] == ["src.py"]


@pytest.mark.asyncio
async def test_a_standing_grant_does_not_skip_an_out_of_policy_ask(gate):
    cg.grants().remember("conversation", "asker", "*", "c")
    gen = cg.consent_gate(tool_name="asker", tool_id="t2", args={}, conversation_id="c",
                          tool=_Asker(OUT), project_root="/p")
    assert (await gen.__anext__())["type"] == "consent_opened"
    await gen.aclose()
    # Positive control: in-policy, the same standing grant does skip the ask.
    ev = await _drain(cg.consent_gate(tool_name="asker", tool_id="t3", args={},
                                      conversation_id="c", tool=_Asker({"ok": 1}),
                                      project_root="/p"))
    assert [e["type"] for e in ev] == [cg.DECISION_EVENT] and ev[0]["approved"]


@pytest.mark.asyncio
async def test_autonomous_project_still_asks_for_out_of_policy_paths(gate, monkeypatch):
    import app.utils.project_tool_modes as ptm
    monkeypatch.setattr(ptm, "mode_for", lambda *_a, **_k: "autonomous")
    gen = cg.consent_gate(tool_name="asker", tool_id="t", args={}, conversation_id="c",
                          tool=_Asker(OUT), project_root="/p")
    assert (await gen.__anext__())["type"] == "consent_opened"
    await gen.aclose()


@pytest.mark.asyncio
async def test_rejection_carries_no_grant(gate):
    gen = cg.consent_gate(tool_name="writer", tool_id="t", args={}, conversation_id="c",
                          tool=_Writer(OUT), project_root="/p")
    opened = await gen.__anext__()
    cg.ledger().record_answer(opened["request_id"], "reject", answer="no")
    d = _decision([ev async for ev in gen])
    assert d["approved"] is False and d["write_grant"] is None


@pytest.mark.asyncio
async def test_no_conversation_refuses_an_out_of_policy_write_without_saying_declined(gate):
    ev = await _drain(cg.consent_gate(tool_name="writer", tool_id="t", args={},
                                      conversation_id=None, tool=_Writer(OUT),
                                      project_root="/p"))
    d = _decision(ev)
    assert d["approved"] is False
    text = cg.refusal_text("writer", "*", d["answer"])
    assert "src.py" in text and "declined" not in text


@pytest.mark.asyncio
async def test_a_preflight_refusal_returns_without_opening_a_panel(gate):
    pf = {"error": True, "op": "commit", "message": "nothing staged to commit"}
    ev = await _drain(cg.consent_gate(tool_name="asker", tool_id="t", args={"op": "commit"},
                                      conversation_id="c", tool=_Asker(pf), project_root="/p"))
    assert [e["type"] for e in ev] == [cg.DECISION_EVENT]
    assert cg.ledger().open_for("c") == []
    text = cg.refusal_text("asker", "commit", ev[0]["answer"])
    assert "was not run: nothing staged to commit" in text
    assert "declined" not in text and "Do not retry" not in text


@pytest.mark.asyncio
async def test_a_preflight_that_raised_still_asks(gate):
    class Broken(_Asker):
        def preflight(self, **_):
            raise RuntimeError("boom")
    gen = cg.consent_gate(tool_name="asker", tool_id="t", args={}, conversation_id="c",
                          tool=Broken(None), project_root="/p")
    opened = await gen.__anext__()
    assert opened["type"] == "consent_opened" and "boom" in opened["payload"]["preflight"]["error"]
    await gen.aclose()


async def _approve(tool, tool_name, tid, scope, conv="c", pf=OUT):
    """Run one gated call to the panel and approve it with *scope*."""
    gen = cg.consent_gate(tool_name=tool_name, tool_id=tid, args={"path": "src.py"},
                          conversation_id=conv, tool=tool(pf), project_root="/p")
    opened = await gen.__anext__()
    assert opened["type"] == "consent_opened"
    cg.ledger().record_answer(opened["request_id"], "approve", scope=scope)
    return _decision([ev async for ev in gen])


async def _asks(tool, tool_name, tid, conv="c", pf=OUT):
    """True if the call opens a panel; False if it is decided silently."""
    gen = cg.consent_gate(tool_name=tool_name, tool_id=tid, args={"path": "src.py"},
                          conversation_id=conv, tool=tool(pf), project_root="/p")
    first = await gen.__anext__()
    await gen.aclose()
    return first["type"] == "consent_opened"


@pytest.mark.asyncio
@pytest.mark.parametrize("scope", ["conversation", "session", "always"])
async def test_a_standing_scope_remembers_the_approved_paths(gate, scope):
    """Regression: approving a write for the conversation/session asked again
    on the very next write to the same file."""
    await _approve(_Writer, "writer", "t1", scope)
    gen = cg.consent_gate(tool_name="writer", tool_id="t2", args={"path": "src.py"},
                          conversation_id="c", tool=_Writer(OUT), project_root="/p")
    ev = [e async for e in gen]
    assert [e["type"] for e in ev] == [cg.DECISION_EVENT], "must not ask again"
    d = ev[0]
    assert d["approved"] is True and d["asked"] is False
    # Still a per-call grant for exactly the shown paths: the executor
    # activates it around this call only.
    assert d["write_grant"] == {"root": "/p", "paths": ["src.py"]}
    assert cg.ledger().open_for("c") == []


@pytest.mark.asyncio
async def test_once_remembers_nothing(gate):
    await _approve(_Writer, "writer", "t1", "once")
    assert await _asks(_Writer, "writer", "t2")


@pytest.mark.asyncio
async def test_a_remembered_path_does_not_cover_a_new_path(gate):
    await _approve(_Writer, "writer", "t1", "session")
    both = {"op": "write", "outside_policy": [
        {"path": "src.py", "reason": "x"}, {"path": "lib.py", "reason": "x"}]}
    assert await _asks(_Writer, "writer", "t2", pf=both), "lib.py was never shown"
    # Positive control in the same state: src.py alone is covered.
    assert not await _asks(_Writer, "writer", "t3")


@pytest.mark.asyncio
async def test_remembered_paths_are_per_tool_and_conversation_scope_is_per_conversation(gate):
    await _approve(_Writer, "writer", "t1", "conversation")

    class _Other(_Writer):
        name = "other"
    assert await _asks(_Other, "other", "t2"), "another tool must ask"
    assert await _asks(_Writer, "writer", "t3", conv="c2"), "another conversation starts cold"
    assert not await _asks(_Writer, "writer", "t4"), "positive control: same tool, same conversation"


@pytest.mark.asyncio
async def test_session_scope_spans_conversations_and_forget_clears_conversation_paths(gate):
    await _approve(_Writer, "writer", "t1", "conversation")
    cg.grants().forget_conversation("c")
    assert await _asks(_Writer, "writer", "t2")
    await _approve(_Writer, "writer", "t3", "session")
    assert not await _asks(_Writer, "writer", "t4", conv="c-other")


@pytest.mark.asyncio
async def test_a_rejection_remembers_nothing(gate):
    gen = cg.consent_gate(tool_name="writer", tool_id="t1", args={}, conversation_id="c",
                          tool=_Writer(OUT), project_root="/p")
    opened = await gen.__anext__()
    cg.ledger().record_answer(opened["request_id"], "reject", scope="session")
    [e async for e in gen]
    assert await _asks(_Writer, "writer", "t2")


@pytest.mark.asyncio
async def test_remembered_paths_do_not_skip_an_ask_mode_tool_op(gate):
    """Path memory answers the policy question only; an ``ask`` tool still
    needs its (tool, op) consent."""
    cg.grants().remember_paths("session", "asker", "c", "/p", ["src.py"])
    assert await _asks(_Asker, "asker", "t1")
    cg.grants().remember("session", "asker", "*", "c")
    assert not await _asks(_Asker, "asker", "t2"), "both covered: no ask"


def test_floor_paths_are_never_remembered(tmp_path):
    g = cg.GrantCache()
    root = str(tmp_path)
    g.remember_paths("session", "w", "c", root, [".git/config", "../outside.txt"])
    assert not g.covers_paths("w", "c", root, [".git/config"])
    assert not g.covers_paths("w", "c", root, ["../outside.txt"])
    g.remember_paths("session", "w", "c", root, ["src.py"])
    assert g.covers_paths("w", "c", root, ["src.py"])
    assert g.covers_paths("w", "c", root, ["./src.py"]), "matching is on the realpath"
    assert not g.covers_paths("w", "c", str(tmp_path / "sub"), ["src.py"]), "bound to its root"


def test_a_remembered_path_that_becomes_a_symlink_out_is_not_covered(tmp_path):
    root = tmp_path / "proj"
    root.mkdir()
    (root / "f.txt").write_text("x")
    g = cg.GrantCache()
    g.remember_paths("session", "w", "c", str(root), ["f.txt"])
    assert g.covers_paths("w", "c", str(root), ["f.txt"])
    (root / "f.txt").unlink()
    (tmp_path / "secret").write_text("s")
    (root / "f.txt").symlink_to(tmp_path / "secret")
    assert not g.covers_paths("w", "c", str(root), ["f.txt"])


@pytest.mark.asyncio
async def test_long_args_are_truncated_for_the_panel_only(gate):
    big = "x" * 10000
    gen = cg.consent_gate(tool_name="writer", tool_id="t", args={"content": big},
                          conversation_id="c", tool=_Writer(OUT), project_root="/p")
    opened = await gen.__anext__()
    shown = opened["payload"]["args"]["content"]
    assert len(shown) < 5000 and "more characters" in shown
    await gen.aclose()
