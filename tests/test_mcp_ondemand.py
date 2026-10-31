"""
MCP servers within reach (design/mcp-on-demand.md, capabilities-hub slice 9).

  * the loaded record: cumulative merge, chat-record round trip, sticky copy
  * the filter: shelved / unloaded / partial / scope-named, and tool ORDER
    (builtins, on-hand by name, loaded within-reach in load order -- a load
    appends, so a prefix of the tool array can still cache-hit)
  * the catalog: placement + config only, unchanged by connection state
  * search: ranking, flags, refusals; loads nothing
  * the loader: refusals, whole and partial loads, unknown tool names
  * the ✦ event for a load
  * the executor seam: a successful load in one iteration makes the server's
    tools part of the NEXT model call in the same turn; a refusal does not
    rebuild the tool list
"""
from __future__ import annotations

import os
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.utils import bench_prompt as bp
from app.utils import mcp_ondemand as od
from app.utils.capability_events import capability_used_event

PID, CID = "proj1", "chat1"


@pytest.fixture(autouse=True)
def _clean(tmp_path, monkeypatch):
    monkeypatch.setenv("ZIYA_HOME", str(tmp_path))
    od._sticky.clear()
    import app.mcp.builtin_tools as bt
    monkeypatch.setattr(bt, "is_builtin_category_enabled", lambda c: True)
    monkeypatch.setattr(bp, "project_id_for_root", lambda root: PID if root else None)
    yield
    od._sticky.clear()


def _mgr(connected=True, quarantined=(), description="Jira issues and boards"):
    tools = [SimpleNamespace(name="search_issues", description="Search issues by JQL.\nLong detail."),
             SimpleNamespace(name="create_issue", description="Create an issue."),
             SimpleNamespace(name="list_boards", description="List agile boards.")]
    cfg = {"jira": ({"description": description} if description else {})}
    return SimpleNamespace(
        clients={"jira": SimpleNamespace(is_connected=connected, tools=tools)},
        server_configs=cfg, _quarantined_servers=set(quarantined),
    )


def _place(monkeypatch, **states):
    monkeypatch.setattr(bp, "mcp_placements_for_root", lambda r, c: dict(states))


# -- loaded record -------------------------------------------------------------

def test_merge_is_cumulative_and_keeps_load_order():
    m = od.merge_loaded({}, "jira", ["a"])
    m = od.merge_loaded(m, "slack", None)
    m = od.merge_loaded(m, "jira", ["mcp_b", "a"])
    assert m == {"jira": ["a", "b"], "slack": od.ALL}
    assert list(m) == ["jira", "slack"]
    assert od.merge_loaded(m, "jira", None)["jira"] == od.ALL
    assert od.merge_loaded(m, "slack", ["x"])["slack"] == od.ALL   # whole stays whole


def test_sticky_copy_is_per_conversation_and_cumulative():
    od.remember_loaded("c1", "jira", ["a"])
    od.remember_loaded("c1", "jira", ["b"])
    assert od.sticky_loaded("c1") == {"jira": ["a", "b"]}
    assert od.sticky_loaded("c2") == {} and od.sticky_loaded(None) == {}


def test_record_round_trips_through_the_chat_storage(tmp_path):
    """Seam: the record lives on Chat.loadedMcp and survives ChatStorage.update."""
    from app.models.chat import ChatCreate
    from app.models.project import ProjectCreate
    from app.storage.projects import ProjectStorage
    from app.utils.paths import get_ziya_home
    repo = tmp_path / "repo"; repo.mkdir()
    proj = ProjectStorage(get_ziya_home()).create(ProjectCreate(path=str(repo), name="repo"))
    chat = od._chat_storage(proj.id).create(ChatCreate(title="t"))
    assert od.write_record(proj.id, chat.id, "jira", ["a"]) == {"jira": ["a"]}
    assert od.write_record(proj.id, chat.id, "jira", ["b"]) == {"jira": ["a", "b"]}
    assert od.read_record(proj.id, chat.id) == {"jira": ["a", "b"]}
    assert od.read_record(proj.id, "no-such-chat") == {}
    assert od.write_record(proj.id, "no-such-chat", "jira", None) is None
    # record ∪ sticky, record first
    od.remember_loaded(chat.id, "slack")
    assert list(od.loaded_map(proj.id, chat.id)) == ["jira", "slack"]


# -- filter -------------------------------------------------------------------

P = {"jira": "ondemand", "slack": "off", "gh": "always", "bt": "ondemand"}


def test_filter_withholds_shelved_and_unloaded_and_allows_partial():
    f = od.mcp_filter(P, {"bt": ["taskei_get", "mcp_taskei_list"]})
    assert f.withheld == {"jira", "slack"}
    assert f.partial == {"bt": {"taskei_get", "taskei_list"}}
    assert f.order == ["bt"]
    assert f.allows("bt", "mcp_taskei_get") and not f.allows("bt", "mcp_other")
    assert f.allows("gh", "anything") and f.allows(None, "builtin")
    assert not f.allows("slack", "x") and not f.allows("jira", "x")


def test_filter_ignores_loads_for_servers_not_within_reach_and_honours_scope():
    f = od.mcp_filter(P, {"slack": od.ALL, "gh": od.ALL, "jira": od.ALL})
    assert f.withheld == {"slack", "bt"} and f.order == ["jira"]      # slack stays shelved
    f = od.mcp_filter(P, {}, scope_servers={"bt"})
    assert f.withheld == {"jira", "slack"} and f.order == ["bt"] and f.partial == {}


def test_order_is_builtins_then_on_hand_by_name_then_loaded_in_load_order():
    T = lambda n, s: SimpleNamespace(name=n, server=s)  # noqa: E731
    tools = [T("z_srv", "zeta"), T("builtin1", None), T("j1", "jira"), T("a_srv", "alpha"),
             T("builtin2", None), T("b1", "bt"), T("j2", "jira")]
    out = od.order_tools(tools, lambda t: t.server, ["bt", "jira"])
    assert [t.name for t in out] == ["builtin1", "builtin2", "a_srv", "z_srv", "b1", "j1", "j2"]
    # a later load only appends
    out2 = od.order_tools(tools + [T("n1", "new")], lambda t: t.server, ["bt", "jira", "new"])
    assert [t.name for t in out2][:7] == [t.name for t in out]


def test_apply_mcp_filter_on_real_secure_tools():
    from app.mcp.enhanced_tools import SecureMCPTool

    def mk(n, s):
        return SecureMCPTool(name=f"mcp_{n}", description="d", mcp_tool_name=n,
                             input_schema={"type": "object", "properties": {}}, server_name=s)
    tools = [mk("j1", "jira"), mk("s1", "slack"), mk("g1", "gh"), mk("b_keep", "bt"), mk("b_drop", "bt")]
    kept, n = bp.apply_mcp_filter(tools, od.mcp_filter(P, {"bt": ["b_keep"]}))
    assert [t.name for t in kept] == ["mcp_g1", "mcp_b_keep"] and n == 3


def test_filter_for_root_reads_record_sticky_and_scope(monkeypatch):
    _place(monkeypatch, jira="ondemand", slack="off")
    monkeypatch.setattr(od, "read_record", lambda p, c: {"jira": ["a"]})
    f = bp.mcp_filter_for_root("/repo", CID)
    assert f.withheld == {"slack"} and f.partial == {"jira": {"a"}}
    assert bp.withheld_mcp_for_root("/repo", CID) == {"slack"}
    monkeypatch.setattr(od, "read_record", lambda p, c: {})
    assert bp.withheld_mcp_for_root("/repo", CID) == {"jira", "slack"}
    od.remember_loaded(CID, "jira")
    assert bp.withheld_mcp_for_root("/repo", CID) == {"slack"}
    assert bp.withheld_mcp_for_root("/repo", "other") == {"jira", "slack"}


# -- catalog ------------------------------------------------------------------

def test_catalog_is_placement_and_config_only():
    base = od.catalog_section(["jira"], mgr=_mgr())
    assert "## MCP servers within reach" in base
    assert "  • jira — Jira issues and boards" in base
    assert od.SEARCH_TOOL in base and od.LOADER_TOOL in base and "ONE call" in base
    # Connection state, quarantine and tool count never move the cache prefix.
    assert od.catalog_section(["jira"], mgr=_mgr(connected=False)) == base
    assert od.catalog_section(["jira"], mgr=_mgr(quarantined=["jira"])) == base
    assert "3 tool" not in base
    assert "  • absent\n" in od.catalog_section(["jira", "absent"], mgr=_mgr())
    assert od.catalog_section([], mgr=_mgr()) == ""


def test_catalog_is_empty_when_skill_discovery_is_off(monkeypatch):
    import app.mcp.builtin_tools as bt
    monkeypatch.setattr(bt, "is_builtin_category_enabled", lambda c: c != "skills")
    assert od.catalog_section(["jira"], mgr=_mgr()) == ""


def test_catalog_for_root_reads_the_placements(monkeypatch):
    _place(monkeypatch, jira="ondemand", x="off")
    monkeypatch.setattr("app.mcp.manager.get_mcp_manager", lambda: _mgr())
    s = od.catalog_section_for_root("/repo", CID)
    assert "jira" in s and "• x" not in s


def test_prompt_builder_appends_the_catalog_after_the_skill_catalog():
    import inspect
    from app.utils import precision_prompt_system as pps
    src = inspect.getsource(pps)
    assert src.index("get_skill_catalog_section(") < src.index("catalog_section_for_root(")


# -- search -------------------------------------------------------------------

def test_rank_tools_prefers_name_hits_and_drops_non_matches():
    pool = [("bt", "taskei_get_task", "Fetch a task"), ("bt", "pippin_list", "List Pippin docs"),
            ("bt", "mechanic_run", "Run a task on a host")]
    got = od.rank_tools("task", pool)
    assert [r[1] for r in got] == ["taskei_get_task", "mechanic_run"]
    assert od.rank_tools("", pool) == [] and od.rank_tools("zzz", pool) == []


def test_search_ranks_within_reach_tools_flags_loaded_and_loads_nothing(monkeypatch):
    _place(monkeypatch, jira="ondemand", gh="always", slack="off")
    monkeypatch.setattr(od, "read_record", lambda p, c: {"jira": ["search_issues"]})
    res = od.search_tools("search issues", None, "/repo", CID, mgr=_mgr())
    assert "jira/search_issues (loaded)" in res["content"]
    assert "Nothing is loaded by searching" in res["content"] and od.LOADER_TOOL in res["content"]
    assert not od.MARKER_RE.search(res["content"])
    assert od.loaded_in_results([{"tool_name": od.SEARCH_TOOL, "result": res["content"]}]) == set()
    listing = od.search_tools("", "jira", "/repo", CID, mgr=_mgr())
    assert "3 tools on jira" in listing["content"]
    assert "on hand" in od.search_tools("x", "gh", "/repo", CID, mgr=_mgr())["message"]
    assert "shelved" in od.search_tools("x", "slack", "/repo", CID, mgr=_mgr())["message"]
    assert "Nothing loaded" in od.search_tools("zzzz", None, "/repo", CID, mgr=_mgr())["content"]


# -- loader -------------------------------------------------------------------

@pytest.mark.parametrize("state,mgr,expect", [
    ("off", _mgr(), "shelved"),
    ("always", _mgr(), "on hand"),
    (None, _mgr(), "not an MCP server on this bench"),
    ("ondemand", _mgr(connected=False), "not connected"),
    ("ondemand", _mgr(quarantined=["jira"]), "re-authorization"),
])
def test_loader_refusals_carry_no_marker_and_load_nothing(monkeypatch, state, mgr, expect):
    _place(monkeypatch, **({"jira": state} if state else {"other": "ondemand"}))
    res = od.load_server("jira", "/repo", CID, mgr=mgr)
    assert res.get("error") and expect in res["message"]
    assert not od.MARKER_RE.search(res["message"])
    assert od.sticky_loaded(CID) == {}


def test_loader_whole_and_partial_are_cumulative(monkeypatch):
    _place(monkeypatch, jira="ondemand")
    written = []
    monkeypatch.setattr(od, "write_record", lambda p, c, s, t: written.append((p, c, s, t)) or None)
    res = od.load_server("jira", "/repo", CID, mgr=_mgr(), tools=["mcp_search_issues"])
    assert res["content"].startswith(od.marker("jira"))
    assert "1 of 3 tools" in res["content"] and "mcp_search_issues — Search issues by JQL." in res["content"]
    assert "create_issue" not in res["content"] and "Long detail" not in res["content"]
    assert od.sticky_loaded(CID) == {"jira": ["search_issues"]}
    assert written == [(PID, CID, "jira", ["search_issues"])]
    res = od.load_server("jira", "/repo", CID, mgr=_mgr(), tools=["create_issue"])
    assert "2 of 3 tools" in res["content"] and "mcp_search_issues" in res["content"]
    res = od.load_server("jira", "/repo", CID, mgr=_mgr())
    assert "all tools" in res["content"] and od.sticky_loaded(CID) == {"jira": od.ALL}
    from app.utils.capability_usage import last_used_map
    assert "mcp:jira" in last_used_map()


def test_loader_refuses_unknown_tool_names_and_needs_a_conversation(monkeypatch):
    _place(monkeypatch, jira="ondemand")
    res = od.load_server("jira", "/repo", CID, mgr=_mgr(), tools=["nope"])
    assert res.get("error") and "no tool named nope" in res["message"] and od.sticky_loaded(CID) == {}
    assert od.load_server("jira", "/repo", None, mgr=_mgr()).get("error")


# -- ✦ ------------------------------------------------------------------------

def test_capability_event_for_a_load_and_not_for_a_refusal_or_search():
    ok = {"type": "tool_display", "tool_name": "mcp_load_server_tools", "result": od.marker("jira")}
    assert capability_used_event(ok) == {"type": "capability_used", "key": "mcp:jira"}
    no = {"type": "tool_display", "tool_name": "load_server_tools", "result": "'jira' is shelved"}
    assert capability_used_event(no) is None
    srch = {"type": "tool_display", "tool_name": "search_server_tools", "result": "2 matches"}
    assert capability_used_event(srch) is None
    skill = {"type": "tool_display", "tool_name": "get_skill_details", "result": "[Skill: X]\n\nbody"}
    assert capability_used_event(skill) == {"type": "capability_used", "key": "skill:X"}


# -- tool registration ----------------------------------------------------------

@pytest.mark.asyncio
async def test_loader_and_search_tools_are_registered_with_skill_discovery(monkeypatch):
    from app.mcp.builtin_tools import get_skill_tools
    tools = {t.name: t for t in (cls() for cls in get_skill_tools())}
    assert od.LOADER_TOOL in tools and od.SEARCH_TOOL in tools
    _place(monkeypatch, jira="ondemand")
    monkeypatch.setattr("app.mcp.manager.get_mcp_manager", lambda: _mgr())
    monkeypatch.setattr(od, "write_record", lambda *a: None)
    monkeypatch.setattr("app.context.get_conversation_id_or_none", lambda: CID)
    res = await tools[od.SEARCH_TOOL].execute(query="issues")
    assert "jira/search_issues" in res["content"]
    res = await tools[od.LOADER_TOOL].execute(name="jira", tools=["create_issue"])
    assert od.marker("jira") in res["content"] and "1 of 3" in res["content"]


# -- executor seam ------------------------------------------------------------

from app.providers.base import StreamEnd, TextDelta, ToolUseEnd, ToolUseInput, ToolUseStart  # noqa: E402


class _Provider:
    provider_name = "scripted"

    def __init__(self, script):
        self.script, self.tools_sent = list(script), []

    async def stream_response(self, conversation, system_content, tools, config):
        self.tools_sent.append(list(tools or []))
        idx = len(self.tools_sent) - 1
        for ev in (self.script[idx] if idx < len(self.script) else []):
            yield ev

    def build_assistant_message(self, text, tool_uses, thinking_blocks=None):
        content = [{"type": "text", "text": text}] if text else []
        content += [{"type": "tool_use", "id": t["id"], "name": t["name"], "input": t["input"]}
                    for t in tool_uses]
        return {"role": "assistant", "content": content}

    def build_tool_result_message(self, tool_results):
        return {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": r["tool_use_id"], "content": r["content"]}
            for r in tool_results]}

    def supports_feature(self, name):
        return False

    def prepare_cache_control(self, messages, *a, **k):
        return messages


class _Tool:
    def __init__(self, name):
        self.name, self.metadata = name, None


def _load_call(tid, tag="x"):
    # ``tag`` makes each scenario's arguments distinct: a process-wide result
    # cache keyed on tool name + arguments otherwise replays one scenario's
    # load result into the next (observed: the refusal test saw the marker).
    args = {"name": "jira", "tag": tag}
    import json as _json
    return [ToolUseStart(id=tid, name=od.LOADER_TOOL, index=0),
            ToolUseInput(partial_json=_json.dumps(args), index=0),
            ToolUseEnd(id=tid, name=od.LOADER_TOOL, input=args, index=0),
            StreamEnd(stop_reason="tool_use")]


FINAL = [TextDelta(content="Done."), StreamEnd(stop_reason="end_turn")]
LOADER_SCHEMA = {"name": od.LOADER_TOOL}
JIRA_SCHEMA = {"name": "mcp_search_issues"}


def _executor(provider, loads, cid):
    with patch.dict(os.environ, {"ZIYA_ENDPOINT": "bedrock", "ZIYA_MODEL": "sonnet3.7"}):
        with patch("app.streaming_tool_executor.StreamingToolExecutor.__init__", return_value=None):
            from app.streaming_tool_executor import StreamingToolExecutor
            ex = StreamingToolExecutor.__new__(StreamingToolExecutor)
    ex.model_id, ex.bedrock, ex.provider = "test-model", None, provider
    ex.model_config = {"family": "claude", "max_output_tokens": 8192, "supports_assistant_prefill": True}
    ex._block_opening_buffer = ""
    ex._normalize_fence_spacing = lambda text, tracker, **kw: text
    ex._update_code_block_tracker = lambda text, tracker: None
    opt = MagicMock()
    opt.add_content.side_effect = lambda t: [t] if t else []
    opt.flush_remaining.return_value = ""
    ex._content_optimizer = opt
    ex._build_conversation_from_messages = MagicMock(return_value=(
        [{"role": "user", "content": [{"type": "text", "text": "Go"}]}], "system prompt"))
    ex._format_tools_for_api = MagicMock(return_value=[])
    ex._build_provider_config = MagicMock(return_value=MagicMock(max_output_tokens=8192))
    ex._handle_usage_event = MagicMock()
    ex._build_tool_reminder_message = MagicMock(return_value={"role": "user", "content": "reminder"})

    # The real loader writes the loaded record synchronously, so a rebuild
    # simply re-reads it; here the fake tool stands in with the sticky copy.
    async def _tools(*a, conversation_id=None, **k):
        loaded = od.sticky_loaded(conversation_id)
        bedrock = [LOADER_SCHEMA] + ([JIRA_SCHEMA] if "jira" in loaded else [])
        return [_Tool(od.LOADER_TOOL)], bedrock, set(), set(), set()
    ex._load_and_prepare_tools = AsyncMock(side_effect=_tools)

    async def _exec(ctx):
        yield {"type": "tool_start", "tool_name": ctx.actual_tool_name, "tool_id": ctx.tool_id}
        if loads:
            od.remember_loaded(cid, "jira")
        yield {"type": "_tool_result", "tool_id": ctx.tool_id, "tool_name": ctx.tool_name,
               "result": od.marker("jira") + "\n2 tools" if loads else "'jira' is shelved"}
    return ex, _exec


async def _run(loads):
    # Distinct conversation per scenario, and app.tool_batch patched as well
    # as app.tool_execution: tool_batch binds execute_single_tool at import,
    # so the first scenario's fake would otherwise serve the second.
    cid = f"{CID}-{'load' if loads else 'refuse'}"
    provider = _Provider([_load_call(f"t-{cid}", tag=cid), FINAL])
    ex, fake_exec = _executor(provider, loads, cid)
    mgr = MagicMock(); mgr.is_initialized = True
    with patch("app.mcp.manager.get_mcp_manager", return_value=mgr), \
         patch("app.tool_execution.execute_single_tool", fake_exec), \
         patch("app.tool_batch.execute_single_tool", fake_exec, create=True), \
         patch("app.streaming_tool_executor.StreamingToolExecutor._FEEDBACK_GRACE_SECONDS", 0, create=True):
        async for _ev in ex.stream_with_tools(messages=[{"role": "user", "content": "Go"}], tools=[],
                                              conversation_id=cid):
            pass
    return provider, ex


@pytest.mark.asyncio
async def test_a_load_puts_the_servers_tools_in_the_next_model_call():
    provider, ex = await _run(loads=True)
    assert len(provider.tools_sent) == 2, "harness: expected one tool round then final text"
    assert JIRA_SCHEMA not in provider.tools_sent[0]
    assert JIRA_SCHEMA in provider.tools_sent[1]
    assert ex._load_and_prepare_tools.await_count == 2


@pytest.mark.asyncio
async def test_a_refused_load_does_not_rebuild_the_tool_list():
    provider, ex = await _run(loads=False)
    assert len(provider.tools_sent) == 2
    assert ex._load_and_prepare_tools.await_count == 1
    assert all(JIRA_SCHEMA not in t for t in provider.tools_sent)
