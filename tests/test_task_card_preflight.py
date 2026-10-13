"""Card preflight: required skills / tools / MCP servers are checked before
the first model call, from the card's ``requires`` block AND every scope.

Pure checks use real card models with injected environments; the live
seam (SkillStorage discovery roots) is covered by the last test.
"""
import pytest

from app.models.task_card import Block, TaskCard, TaskScope
from app.utils.task_card_preflight import (
    check_requirements, collect_requirements, run_preflight,
)

MCP_UP = {"search": {"connected": True}, "shell": {"connected": True, "builtin": True}}


def card(requires=None, scope=None, body_scopes=()):
    tasks = [Block(block_type="task", id=f"t{i}", name=f"task {i}", instructions="x",
                   scope=sc) for i, sc in enumerate(body_scopes)]
    root = Block(block_type="group", id="g", name="root", body=tasks) if tasks \
        else Block(block_type="task", id="t0", name="only", instructions="x")
    return TaskCard(id="c1", name="card", root=root, scope=scope, requires=requires)


def test_collects_from_requires_and_every_scope():
    c = card(requires={"skills": ["code_review"], "mcp_servers": ["search"]},
             scope=TaskScope(tools=["mcp_search"]),
             body_scopes=[TaskScope(skills=["Packet Diagrams"]), TaskScope(skills=["code_review"])])
    w = collect_requirements(c)
    assert w["skills"] == {"code_review": ["requires", "task 1"], "Packet Diagrams": ["task 0"]}
    assert w["tools"] == {"mcp_search": ["card"]}
    assert w["mcp_servers"] == {"search": ["requires"]}


def test_all_present_is_ok_and_matches_skill_by_id_or_name_case_insensitively():
    c = card(requires={"skills": ["Code Review"], "mcp_servers": ["search"], "tools": ["mcp_search"]})
    r = check_requirements(c, available_skills=["code_review"], available_tools=["mcp_search"],
                           mcp_status=MCP_UP)
    assert r.ok and not r.refuse and r.messages() == []
    r = check_requirements(c, available_skills=["skill_7", "code review"],
                           available_tools=["mcp_search"], mcp_status=MCP_UP)
    assert r.ok


def test_reports_each_missing_kind_with_requester():
    c = card(requires={"skills": ["code_review"], "mcp_servers": ["search", "jira"], "tools": ["mcp_jira_get"]},
             body_scopes=[TaskScope(skills=["ghost"])])
    status = {"search": {"connected": False}, "shell": {"connected": True}}
    r = check_requirements(c, available_skills=["other"], available_tools=["file_read"], mcp_status=status)
    assert not r.ok
    assert r.missing_skills == ["code_review", "ghost"]
    assert r.missing_tools == ["mcp_jira_get"]
    assert r.missing_mcp_servers == ["jira"]
    assert r.unavailable_mcp_servers == ["search"]
    msgs = "\n".join(r.messages())
    assert "'ghost'" in msgs and "needed by task 0" in msgs
    assert "'jira' is not configured" in msgs
    assert "'search' is configured but not connected" in msgs
    # Default (non-strict): a warning, not a refusal.
    assert not r.refuse


def test_quarantined_server_counts_as_unavailable():
    c = card(requires={"mcp_servers": ["search"]})
    r = check_requirements(c, available_skills=[], available_tools=[],
                           mcp_status={"search": {"connected": True, "quarantined": True}})
    assert r.unavailable_mcp_servers == ["search"] and not r.ok


def test_strict_from_card_or_caller_turns_missing_into_refusal():
    c = card(requires={"mcp_servers": ["search"], "strict": True})
    r = check_requirements(c, available_skills=[], available_tools=[], mcp_status={})
    assert r.refuse
    c2 = card(requires={"mcp_servers": ["search"]})
    assert not check_requirements(c2, available_skills=[], available_tools=[], mcp_status={}).refuse
    assert check_requirements(c2, available_skills=[], available_tools=[], mcp_status={},
                              strict=True).refuse
    # Caller can also relax a strict card.
    assert not check_requirements(c, available_skills=[], available_tools=[], mcp_status={},
                                  strict=False).refuse
    # Strict with everything present is still fine.
    assert not check_requirements(c, available_skills=[], available_tools=[],
                                  mcp_status=MCP_UP).refuse


def test_floor_tools_are_not_reported_missing():
    # emit_artifact is always available; its absence from the loaded list is
    # not the author's error (same rule as unmatched_scope_tools).
    c = card(scope=TaskScope(tools=["emit_artifact", "mcp_search"]))
    r = check_requirements(c, available_skills=[], available_tools=["mcp_search"], mcp_status={})
    assert r.ok


def test_card_without_requirements_needs_no_environment():
    c = card()
    assert check_requirements(c, available_skills=[], available_tools=[], mcp_status={}).ok
    assert run_preflight(c, project_id=None, project_root=None).ok


def test_live_preflight_resolves_repo_local_skill(tmp_path, monkeypatch):
    """Seam: a SKILL.md under .ziya/skills in the project root satisfies a
    requirement, exactly as the executor would resolve it."""
    monkeypatch.setenv("ZIYA_HOME", str(tmp_path / "home"))
    root = tmp_path / "repo"
    (root / ".ziya" / "skills" / "log_sweep").mkdir(parents=True)
    (root / ".ziya" / "skills" / "log_sweep" / "SKILL.md").write_text(
        "---\nname: Log Sweep\ndescription: sweep\n---\nDo the sweep.\n")
    c = card(requires={"skills": ["Log Sweep", "absent_skill"]})
    r = run_preflight(c, project_id="proj1", project_root=str(root))
    assert r.missing_skills == ["absent_skill"], r.messages()
