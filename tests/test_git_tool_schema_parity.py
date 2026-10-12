"""
Seam: the ``git`` tool's input schema and argument forwarding must match
``git_ops``.  An op added to ``git_ops.OPS`` but not to ``GitInput.op`` can
never be called (the schema rejects it); an argument read by ``run_op`` but
missing from ``_ARG_KEYS`` is silently dropped before dispatch.
"""

import typing

from app.mcp.tools import git_tool as gt
from app.utils import git_ops


def _schema_ops():
    return set(typing.get_args(gt.GitInput.model_fields["op"].annotation))


def test_schema_op_enum_matches_git_ops():
    assert _schema_ops() == set(git_ops.OPS)


def test_alias_targets_are_real_ops():
    for alias, target in git_ops.OP_ALIASES.items():
        assert alias in git_ops.OPS and target in git_ops.OPS


def test_new_op_arguments_are_declared_and_forwarded():
    for key in ("index", "url", "dest", "remote", "branch"):
        assert key in gt.GitInput.model_fields, key
        assert key in gt._ARG_KEYS, key
    args = gt._op_args({"op": "clone", "url": "https://x.invalid/r.git", "dest": "d", "index": 2})
    assert args["url"] == "https://x.invalid/r.git" and args["dest"] == "d" and args["index"] == 2


def test_read_only_consent_defaults_cover_exactly_the_read_only_ops():
    off = {op for op, mode in gt.GitTool.consent_op_defaults.items() if mode == "off"}
    assert off == set(git_ops.READ_ONLY_OPS)
    # fetch / clone / stash_pop are mutating and must ask.
    for op in ("fetch", "clone", "stash_pop", "stash"):
        assert op not in gt.GitTool.consent_op_defaults


def test_preflight_forwards_new_args(tmp_path, monkeypatch):
    """End to end through the tool's own arg plumbing: clone dest reaches the plan."""
    import asyncio
    monkeypatch.setattr(git_ops, "default_write_check", lambda *a: "")
    plan = asyncio.run(gt.GitTool().preflight(
        op="clone", url="https://x.invalid/r.git", dest="vendored/r",
        _workspace_path=str(tmp_path)))
    assert not plan.get("error"), plan
    # Without forwarding, dest is dropped and the plan falls back to "r".
    assert plan["dest"] == "vendored/r"


def test_fixup_target_reaches_the_plan_through_the_tool(tmp_path):
    """Without ``target`` in GitInput and _ARG_KEYS, fixup refuses as 'target is required'."""
    import asyncio
    import subprocess

    def git(*a):
        subprocess.run(["git", *a], cwd=tmp_path, check=True, capture_output=True)
    git("init", "-q", "-b", "main")
    git("config", "user.name", "T"); git("config", "user.email", "t@example.invalid")
    git("config", "commit.gpgsign", "false"); git("config", "core.hooksPath", "/dev/null")
    for name in ("a", "b"):
        (tmp_path / name).write_text(name + "\n")
        git("add", name); git("commit", "-qm", "add " + name)
    (tmp_path / "a").write_text("a2\n")
    git("add", "a")
    assert "target" in gt.GitInput.model_fields and "target" in gt._ARG_KEYS
    plan = asyncio.run(gt.GitTool().preflight(op="fixup", target="HEAD~1",
                                              _workspace_path=str(tmp_path)))
    assert not plan.get("error"), plan
    assert plan["target_subject"] == "add a" and plan["message"].startswith("fixup! add a")
    assert "fixup" not in gt.GitTool.consent_op_defaults, "fixup must ask"
