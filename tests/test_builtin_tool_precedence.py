"""
Seam tests: builtin ``[DIRECT]`` tools must be presented to the model
ahead of external ``mcp_*`` tools, and the MCP usage rules must define
``[DIRECT]`` and route parallel/delegated work to the Task Cards skill.

Background: with a large external MCP toolset the builtins previously
landed at the bottom of a ~200-entry list, nothing re-sorted them, and the
``[DIRECT]`` prefix was never explained in any prompt text.  The model
therefore picked an external "Delegate" tool (whose sub-agents run with
the external server's toolset, not Ziya's) over the task-card skill.

Both tests are verified to FAIL against the pre-fix source (see
``test_prefix_ordering_regresses_on_prefix_source`` which reconstructs
the old behaviour from git history when available).
"""
from __future__ import annotations

import subprocess
from unittest.mock import Mock, patch

import pytest


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _mock_manager(n_external: int = 5):
    manager = Mock()
    manager.is_initialized = True
    manager.server_configs = {"ext_server": {"command": ["echo"], "enabled": True}}
    manager.clients = {}
    tools = []
    for i in range(n_external):
        t = Mock()
        t.name = f"ext_tool_{i}"
        t.description = f"External tool {i}"
        t.inputSchema = {"type": "object", "properties": {}}
        t._server_name = "ext_server"
        tools.append(t)
    manager.get_all_tools.return_value = tools
    return manager


def _fake_builtin(name: str):
    inst = Mock()
    inst.name = name
    inst.description = f"builtin {name}"
    inst.is_internal = False
    # DirectMCPTool reads .InputSchema via hasattr; make it absent.
    del inst.InputSchema
    return inst


def _classify(tools):
    """Return list of 'D' (DirectMCPTool) / 'S' (SecureMCPTool) in order."""
    from app.mcp.enhanced_tools import DirectMCPTool, SecureMCPTool
    out = []
    for t in tools:
        if isinstance(t, DirectMCPTool):
            out.append("D")
        elif isinstance(t, SecureMCPTool):
            out.append("S")
    return out


# ---------------------------------------------------------------------------
# 1. ordering of the assembled tool list
# ---------------------------------------------------------------------------

def test_builtins_precede_external_tools(monkeypatch):
    import app.mcp.enhanced_tools as et

    monkeypatch.setenv("ZIYA_SECURE_MCP", "true")
    et.invalidate_secure_tools_cache()

    fake_builtins = [_fake_builtin("file_read"), _fake_builtin("task_card_list")]

    with patch("app.mcp.manager.get_mcp_manager", return_value=_mock_manager(5)), \
         patch("app.mcp.builtin_tools.get_enabled_builtin_tools", return_value=fake_builtins), \
         patch("app.mcp.connection_pool.get_connection_pool", return_value=Mock()):
        tools = et.create_secure_mcp_tools()

    et.invalidate_secure_tools_cache()

    kinds = _classify(tools)
    # Positive: both populations are present (the path ran).
    assert kinds.count("D") == 2, kinds
    assert kinds.count("S") == 5, kinds
    # Seam: every builtin index < every external index.
    last_direct = max(i for i, k in enumerate(kinds) if k == "D")
    first_secure = min(i for i, k in enumerate(kinds) if k == "S")
    assert last_direct < first_secure, f"builtins not first: {kinds}"
    # Builtin descriptions carry the [DIRECT] marker the prompt defines.
    for t in tools[:2]:
        assert t.description.startswith("[DIRECT] "), t.description


def _git_show(path: str) -> str | None:
    try:
        return subprocess.check_output(
            ["git", "show", f"HEAD:{path}"], stderr=subprocess.DEVNULL, text=True
        )
    except Exception:
        return None


def test_prefix_ordering_regresses_on_prefix_source():
    """Guard against a test that would pass on unpatched code.

    Reconstruct the *committed* (pre-fix) ordering from git and confirm it
    is the inverse — external tools first.  Skipped if the fix has already
    been committed (the committed source then equals the working tree).
    """
    src = _git_show("app/mcp/enhanced_tools.py")
    if src is None:
        pytest.skip("git history unavailable")
    fixed_marker = 'Builtin "[DIRECT]" tools go FIRST'
    if fixed_marker in src:
        pytest.skip("fix already committed; pre-fix source not reconstructible")
    # Pre-fix: the builtin block appears AFTER the external loop.
    ext_loop = src.index("for tool in mcp_tools:")
    builtin_block = src.index("get_enabled_builtin_tools()")
    assert builtin_block > ext_loop, (
        "expected pre-fix source to append builtins after external tools"
    )


# ---------------------------------------------------------------------------
# 2. usage-rules text: [DIRECT] defined, Task Cards routing present
# ---------------------------------------------------------------------------

def _render_usage_rules(monkeypatch) -> str:
    from app.extensions.prompt_extensions import mcp_prompt_extensions as ext

    monkeypatch.setenv("ZIYA_ENABLE_MCP", "true")
    caps = {"native_function_calling": True}
    with patch("app.mcp.manager.get_mcp_manager", return_value=_mock_manager(2)), \
         patch("app.config.models_config.get_model_capabilities", return_value=caps):
        return ext.mcp_usage_guidelines(
            "BASE PROMPT", {"endpoint": "bedrock", "model_name": "sonnet4"}
        )


def test_usage_rules_define_direct_and_route_to_task_cards(monkeypatch):
    out = _render_usage_rules(monkeypatch)
    # Positive: the guidelines block was actually appended.
    assert "## MCP Tool Usage" in out
    assert "**Usage Rules:**" in out
    # [DIRECT] is defined and preferred over mcp_* look-alikes.
    assert "Prefer `[DIRECT]` tools over `mcp_*` look-alikes" in out
    assert "run in this process" in out
    # Parallel work routes to the [DIRECT] task_card_stage builtin, which
    # loads the task_cards grammar itself (no get_skill_details hop).
    assert "goes through Task Cards" in out
    assert "`task_card_stage`" in out
    assert "stage it for the user to launch" in out
    # Steering is cards-only: the swarm skill is not advertised here.
    assert "task_decomposition" not in out


def test_usage_rules_regress_on_prefix_source():
    src = _git_show("app/extensions/prompt_extensions/mcp_prompt_extensions.py")
    if src is None:
        pytest.skip("git history unavailable")
    if "Prefer `[DIRECT]` tools" in src:
        pytest.skip("fix already committed")
    assert "[DIRECT]" not in src.split("**Usage Rules:**", 1)[1].split("HALLUCINATION")[0]


# ── Skill catalog: task_decomposition hidden, task_cards advertised ──────────

def test_skill_catalog_hides_task_decomposition_but_lists_task_cards(monkeypatch):
    """The catalog the model sees must advertise Task Cards and NOT the swarm
    skill, so the catalog stops counter-advertising "parallel"/"delegate"
    against usage rule 4."""
    monkeypatch.setattr(
        "app.mcp.builtin_tools.is_builtin_category_enabled", lambda c: True
    )
    from app.utils.skill_catalog_prompt import get_skill_catalog_section

    catalog = get_skill_catalog_section()
    assert catalog, "catalog rendered empty — positive control failed"
    assert "task_cards" in catalog
    assert "task_decomposition" not in catalog


@pytest.mark.asyncio
async def test_task_decomposition_still_loadable_by_explicit_name():
    """Hiding from the catalog must not make the skill unreachable: an explicit
    get_skill_details("task_decomposition") still returns the body."""
    from app.data.built_in_skills import get_skill_by_id, get_model_discoverable_skills
    from app.mcp.tools.skill_tools import GetSkillDetailsTool

    assert get_skill_by_id("task_decomposition") is not None
    assert all(s["id"] != "task_decomposition" for s in get_model_discoverable_skills())

    result = await GetSkillDetailsTool().execute(skill_name="task_decomposition")
    text = str(result)
    assert not (isinstance(result, dict) and result.get("error")), result
    assert "swarm" in text.lower()


def test_task_decomposition_visibility_regresses_on_prefix_source():
    src = _git_show("app/data/built_in_skills.py")
    if src is None:
        pytest.skip("no git HEAD available")
    idx = src.find("'id': 'task_decomposition'")
    if idx < 0:
        pytest.skip("skill not in HEAD")
    window = src[idx: idx + 600]
    if "'visibility': MODEL_DISCOVERABLE" not in window:
        pytest.skip("HEAD already carries the fix")
    # HEAD advertised it; the working tree must not.
    from app.data.built_in_skills import get_model_discoverable_skills
    assert all(s["id"] != "task_decomposition" for s in get_model_discoverable_skills())
