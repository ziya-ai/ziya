"""
Bench -> prompt seam (design/capabilities-hub.md, slice 3 open item).

Real SkillStorage under a tmp ZIYA_HOME (seeds the shipped builtins, so the
prompt bodies asserted below are the real ones); real JsonStateStore for the
project/user layers; a chat double for the conversation layer.  MCP items
are stubbed out -- they are not what this seam is about.

The outermost assertion is the system message build_messages_for_streaming
produces: the pinned skill's prompt is in it, the off skill's catalog row is
not.  The remaining tests pin each hop that assertion crosses.
"""
from __future__ import annotations

import pytest

from app.models.chat import Chat, ChatUpdate
from app.models.skill import ModelOverrides, SkillCreate
from app.services import bench as svc
from app.services.token_service import TokenService
from app.storage.skills import SkillStorage
from app.utils import bench_prompt as bp
from app.utils.skill_catalog_prompt import get_skill_catalog_section

PID, CID = "proj1", "chat1"
# Captured before any fixture monkeypatches svc._mcp_items away.
_REAL_MCP_ITEMS = svc._mcp_items
PINNED, OFF, LEFT = "Concise", "Document Authoring", "Code Review"


class _Chats:
    def __init__(self):
        self.chats = {CID: Chat(id=CID, title="t", createdAt=1, lastActiveAt=1)}
    def get(self, cid):
        return self.chats.get(cid)
    def update(self, cid, data: ChatUpdate):
        for k, v in data.model_dump(exclude_unset=True).items():
            setattr(self.chats[cid], k, v)
        return self.chats[cid]


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("ZIYA_HOME", str(tmp_path))
    storage = SkillStorage(tmp_path / "projects" / PID, TokenService())
    chats = _Chats()
    monkeypatch.setattr(svc, "_skill_storage", lambda pid: storage)
    monkeypatch.setattr(svc, "_chat_storage", lambda pid: chats)
    monkeypatch.setattr(svc, "_mcp_items", lambda: [])
    import app.mcp.builtin_tools as bt
    monkeypatch.setattr(bt, "is_builtin_category_enabled", lambda c: True)
    return storage, chats


def _pin(key, layer, state):
    svc.place(PID, CID, key, layer, state)


def test_no_placements_is_not_authoritative(env):
    pi = svc.prompt_inputs(PID, CID)
    assert pi.authoritative is False
    assert pi.always_names == [] and pi.system_prompt_addition == ""
    # every discoverable builtin is still in the catalog
    assert OFF in pi.catalog_names and LEFT in pi.catalog_names


def test_prompt_inputs_follow_placements_and_equal_get_always_set(env):
    storage, _ = env
    _pin(f"skill:{PINNED}", "project", "always")
    _pin(f"skill:{OFF}", "conversation", "off")
    pi = svc.prompt_inputs(PID, CID)

    assert pi.authoritative is True
    assert pi.always_names == [PINNED]
    concise = next(s for s in storage.list() if s.name == PINNED)
    assert f"[Active Skill: {PINNED}]\n{concise.prompt}" in pi.system_prompt_addition
    assert OFF not in pi.system_prompt_addition
    assert OFF not in pi.catalog_names and LEFT in pi.catalog_names

    via_get = sorted(k.partition(":")[2] for k in svc.resolve_bench(PID, CID).always_set()
                     if k.startswith("skill:"))
    assert via_get == pi.always_names == svc.always_skill_names(PID, CID)


def test_overrides_and_tool_ids_come_from_the_same_records(env):
    storage, _ = env
    custom = storage.create(SkillCreate(
        name="Hot", description="d", prompt="be hot",
        toolIds=["mcp_run_shell_command", "file_read"],
        modelOverrides=ModelOverrides(temperature=0.9, maxOutputTokens=1234)))
    _pin(f"skill:{custom.name}", "user", "always")
    pi = svc.prompt_inputs(PID, None)
    assert "[Active Skill: Hot]\nbe hot" in pi.system_prompt_addition
    assert pi.model_overrides == {"temperature": 0.9, "maxOutputTokens": 1234}
    assert pi.preferred_tool_ids == ["mcp_run_shell_command", "file_read"]


def test_chat_additional_prompt_is_kept(env):
    _, chats = env
    chats.chats[CID].additionalPrompt = "Answer in French."
    _pin(f"skill:{PINNED}", "project", "always")
    pi = svc.prompt_inputs(PID, CID)
    assert pi.system_prompt_addition.endswith("Answer in French.")


def test_catalog_section_filters_by_name(env):
    full = get_skill_catalog_section(None)
    assert "document_authoring" in full and "code_review" in full
    only = get_skill_catalog_section({LEFT})
    assert "code_review" in only and "document_authoring" not in only
    assert get_skill_catalog_section(set()) == ""


def test_merge_rules(env):
    client = "[Active Skill: Legacy]\nfrom localStorage"
    # no project -> client untouched, catalog unfiltered
    assert bp.merge_bench_prompt(None, client, {}, []) == (client, {}, [], None)
    # bench not adopted yet -> client prompt kept, bench fills empty extras
    pi = svc.prompt_inputs(PID, CID)
    add, ov, ids, cat = bp.merge_bench_prompt(pi, client, {}, [])
    assert add == client and cat is None
    # authoritative -> server text wins, catalog filtered, client extras win when sent
    _pin(f"skill:{PINNED}", "project", "always")
    pi = svc.prompt_inputs(PID, CID)
    add, ov, ids, cat = bp.merge_bench_prompt(pi, client, {"temperature": 0.1}, ["x"])
    assert add == pi.system_prompt_addition and "Legacy" not in add
    assert ov == {"temperature": 0.1} and ids == ["x"]
    assert cat == set(pi.catalog_names)


def test_resolve_bench_prompt_maps_project_root_to_id(env, monkeypatch):
    _pin(f"skill:{PINNED}", "project", "always")
    monkeypatch.setattr(bp, "project_id_for_root", lambda root: PID if root == "/w" else None)
    assert bp.resolve_bench_prompt("/w", CID).always_names == [PINNED]
    assert bp.resolve_bench_prompt("/elsewhere", CID) is None
    assert bp.resolve_bench_prompt(None, CID) is None


def test_system_message_end_to_end(env):
    """Outermost surface: the assembled system message."""
    from app.server import build_messages_for_streaming
    _pin(f"skill:{PINNED}", "project", "always")
    _pin(f"skill:{OFF}", "conversation", "off")
    pi = svc.prompt_inputs(PID, CID)
    add, _, _, cat = bp.merge_bench_prompt(pi, "", {}, [])

    msgs = build_messages_for_streaming("q", [], [], CID,
                                        system_prompt_addition=add, skill_catalog_names=cat)
    system = msgs[0]["content"]
    assert f"[Active Skill: {PINNED}]" in system
    assert "code_review" in system, "an ondemand skill is still offered in the catalog"
    assert "document_authoring" not in system, "an off skill is offered nowhere"

    # Positive control on the catalog hop: unfiltered, the off skill IS listed.
    system_unfiltered = build_messages_for_streaming("q", [], [], CID,
                                                     system_prompt_addition=add)[0]["content"]
    assert "document_authoring" in system_unfiltered


def test_stream_chunks_wires_the_seam():
    """The three assignments and the forward exist in stream_chunks (source guard)."""
    import inspect
    from app import server
    src = inspect.getsource(server.stream_chunks)
    assert "resolve_bench_prompt" in src and "merge_bench_prompt" in src
    assert "system_prompt_addition, model_overrides, preferred_tool_ids, _bench_catalog_names" in src
    assert "skill_catalog_names=_bench_catalog_names" in src
    # the merge lands after the body reads and before build_messages consumes
    assert src.index("merge_bench_prompt(") > src.index('preferred_tool_ids = body.get')
    assert src.index("merge_bench_prompt(") < src.index("build_messages_for_streaming, question")


# -- MCP off at project/conversation withholds tools (Model: MCP placements) --

_CFGS = {"jira": {}, "slack": {}, "shell": {"builtin": True}}


@pytest.fixture
def mcp_env(env, monkeypatch):
    specs = [svc._mcp_spec(n, c) for n, c in sorted(_CFGS.items())]
    monkeypatch.setattr(svc, "_mcp_specs", lambda: specs)
    monkeypatch.setattr(svc, "_mcp_items", lambda: [
        svc.BenchItem(spec=s, name=s.key.partition(":")[2], provenance="user") for s in specs])
    return env


def test_mcp_off_at_project_is_withheld_always_is_not(mcp_env):
    assert svc.withheld_mcp_servers(PID, CID) == set()
    _pin("mcp:jira", "project", "off")
    _pin("mcp:slack", "project", "always")
    assert svc.withheld_mcp_servers(PID, CID) == {"jira"}
    # the hub reports the same thing the request path acts on
    eff = {r.key: r.effective for r in svc.resolve_bench(PID, CID).items}
    assert eff["mcp:jira"] == "off" and eff["mcp:slack"] == "always"


def test_conversation_off_is_scoped_to_that_conversation(mcp_env):
    _pin("mcp:slack", "conversation", "off")
    assert svc.withheld_mcp_servers(PID, CID) == {"slack"}
    assert svc.withheld_mcp_servers(PID, None) == set()


def test_conversation_always_overrides_project_off(mcp_env):
    _pin("mcp:jira", "project", "off")
    _pin("mcp:jira", "conversation", "always")
    assert svc.withheld_mcp_servers(PID, CID) == set()
    assert svc.withheld_mcp_servers(PID, None) == {"jira"}


def test_environment_server_is_never_withheld_even_if_stored(mcp_env):
    # A hand-edited file; place() would have refused this with 409.
    svc._project_store(PID).write({"placements": {"mcp:shell": "off", "mcp:jira": "off"}})
    assert svc.withheld_mcp_servers(PID, CID) == {"jira"}


def test_drop_withheld_tools_on_real_secure_tools():
    from app.mcp.enhanced_tools import SecureMCPTool
    def mk(n, s):
        return SecureMCPTool(name=f"mcp_{n}", description="d", mcp_tool_name=n,
                             input_schema={"type": "object", "properties": {}}, server_name=s)
    tools = [mk("a", "jira"), mk("b", "slack"), mk("c", "jira")]
    kept, n = bp.drop_withheld_tools(tools, {"jira"})
    assert [t.name for t in kept] == ["mcp_b"] and n == 2
    kept, n = bp.drop_withheld_tools(tools, set())
    assert len(kept) == 3 and n == 0


def test_withheld_lookup_fails_open(monkeypatch):
    monkeypatch.setattr(bp, "project_id_for_root", lambda root: PID)
    def boom(*_a):
        raise RuntimeError("storage exploded")
    monkeypatch.setattr(svc, "withheld_mcp_servers", boom)
    assert bp.withheld_mcp_for_root("/repo", CID) == set()


def test_executor_withholds_after_project_modes_and_before_task_allowlist():
    import inspect
    from app.streaming_tool_executor import StreamingToolExecutor
    src = inspect.getsource(StreamingToolExecutor._load_and_prepare_tools)
    call = src.index("withheld_mcp_for_root(project_root, conversation_id)")
    assert src.index("_apply_project_modes(") < call
    assert call < src.index("drop_withheld_tools(") < src.index("filter_tools_by_scope(")


# -- placement provenance: who wrote a pin, so a lens "+n" is explainable ----

def test_place_records_via_and_clear_removes_it(env):
    svc.place(PID, CID, f"skill:{PINNED}", "project", "always", via="migration")
    svc.place(PID, CID, f"skill:{LEFT}", "user", "off")                 # default via
    meta = svc.placement_meta(PID)
    assert meta["project"][f"skill:{PINNED}"]["via"] == "migration"
    assert meta["project"][f"skill:{PINNED}"]["at"] > 0
    assert meta["user"][f"skill:{LEFT}"]["via"] == "hub"
    # the resolver's input is untouched by the sibling meta document
    assert svc.load_layers(PID, CID)["project"] == {f"skill:{PINNED}": "always"}
    svc.place(PID, CID, f"skill:{PINNED}", "project", None)
    assert f"skill:{PINNED}" not in svc.placement_meta(PID)["project"]


def test_unknown_via_is_recorded_as_api(env):
    svc.place(PID, CID, f"skill:{PINNED}", "project", "always", via="curl-from-somewhere")
    assert svc.placement_meta(PID)["project"][f"skill:{PINNED}"]["via"] == "api"


def test_view_model_carries_meta_only_for_layers_holding_a_placement(env):
    from app.api.bench import _view_model
    svc.place(PID, CID, f"skill:{PINNED}", "project", "always", via="migration")
    svc.place(PID, CID, f"skill:{PINNED}", "conversation", "off")
    items = svc.collect_items(PID)
    vm = _view_model(items, svc.resolve_bench(PID, CID, items), CID, svc.placement_meta(PID))
    item = next(i for i in vm["items"] if i["key"] == f"skill:{PINNED}")
    assert item["meta"]["project"]["via"] == "migration"
    assert item["meta"]["conversation"] is None      # chat record keeps no meta
    assert "user" not in item["meta"]
    assert vm["deltas"]["project"] == 1


# -- card back: detail loaded on demand, never in GET /bench -----------------

def test_item_detail_loads_the_skill_body_the_list_leaves_unloaded(env):
    storage, _ = env
    d = svc.item_detail(PID, f"skill:{PINNED}")
    assert d["kind"] == "skill" and d["name"] == PINNED and d["source"] == "builtin"
    full = storage.get(next(s.id for s in storage.list() if s.name == PINNED))
    assert d["prompt"] == full.prompt and d["prompt"]
    assert d["description"] == full.description
    # GET /bench items never carry the body
    from app.api.bench import _view_model
    items = svc.collect_items(PID)
    vm = _view_model(items, svc.resolve_bench(PID, CID, items), CID)
    assert all("prompt" not in i for i in vm["items"])
    for bad in (f"skill:Nope", "mcp:nope", "garbage", "skill:"):
        with pytest.raises(svc.ItemNotFound):
            svc.item_detail(PID, bad)


def test_detail_route_returns_the_detail_and_404s_unknown_keys(env):
    import asyncio
    from fastapi import HTTPException
    from app.api.bench import get_detail
    assert asyncio.run(get_detail(PID, f"skill:{PINNED}"))["name"] == PINNED
    with pytest.raises(HTTPException) as ei:
        asyncio.run(get_detail(PID, "skill:Nope"))
    assert ei.value.status_code == 404


# -- conversation-layer writes are serialised like the file-backed layers -----

def test_concurrent_conversation_placements_lose_nothing(env, monkeypatch):
    import threading
    import time as _t
    _, chats = env
    real_get = chats.get

    def slow_get(cid):          # widen the read-modify-write window
        c = real_get(cid)
        _t.sleep(0.05)
        return c
    monkeypatch.setattr(chats, "get", slow_get)

    keys = [f"skill:{PINNED}", f"skill:{LEFT}", f"skill:{OFF}"]
    threads = [threading.Thread(target=svc.place, args=(PID, CID, k, "conversation", "off")) for k in keys]
    for t in threads: t.start()
    for t in threads: t.join(10)
    assert set(chats.chats[CID].placements) == set(keys)
    # and the resolver sees all three
    assert set(svc.load_layers(PID, CID)["conversation"]) == set(keys)


# -- per-tool switch on an MCP card back -------------------------------------

def test_tool_toggle_lands_in_permissions_and_shows_on_detail(env, monkeypatch):
    import asyncio
    from types import SimpleNamespace
    from fastapi import HTTPException
    import app.mcp.permissions as perms_mod
    from app.api.bench import ToolRequest, put_tool

    store = {"defaults": {"tool": "enabled"}, "servers": {}}
    class _PM:
        def get_permissions(self): return store
        def update_tool_permission(self, s, t, p):
            store.setdefault("servers", {}).setdefault(s, {}).setdefault("tools", {})[t] = {"permission": p}
    monkeypatch.setattr(perms_mod, "get_permissions_manager", lambda: _PM())
    tools = [SimpleNamespace(name="mcp_a", _server_name="jira", description="Alpha tool",
                             inputSchema={"type": "object", "properties": {"q": {"type": "string"}}}),
             SimpleNamespace(name="mcp_b", _server_name="jira", description="", inputSchema={})]
    mgr = SimpleNamespace(server_configs={"jira": {}}, is_initialized=True, get_all_tools=lambda: tools)
    import app.mcp.manager as mm
    monkeypatch.setattr(mm, "get_mcp_manager", lambda: mgr)
    # ``env`` stubs _mcp_items out for the skill tests; this test is about
    # MCP costing, so run the real assembler over the fake manager.
    monkeypatch.setattr(svc, "_mcp_items", _REAL_MCP_ITEMS)

    assert svc.item_detail(PID, "mcp:jira")["tool_permissions"] == {"mcp_a": True, "mcp_b": True}
    before = next(i for i in svc._mcp_items() if i.name == "jira")
    assert before.health["tool_count"] == 2 and before.health["enabled_count"] == 2
    assert before.tokens > 0 and before.health["withheld_tokens"] == 0

    r = asyncio.run(put_tool(PID, ToolRequest(server="jira", tool="mcp_b", enabled=False), None))
    d = r["detail"]
    assert d["tool_permissions"] == {"mcp_a": True, "mcp_b": False}
    rows = {r["name"]: r for r in d["tool_details"]}
    assert rows["mcp_a"]["description"] == "Alpha tool" and rows["mcp_a"]["tokens"] > 0
    assert rows["mcp_a"]["enabled"] is True and rows["mcp_b"]["enabled"] is False
    assert store["servers"]["jira"]["tools"]["mcp_b"]["permission"] == "disabled"
    # The re-costed view rides along: the card's tokens drop and the counts say why.
    card = next(i for i in r["view"]["items"] if i["key"] == "mcp:jira")
    assert card["tokens"] < before.tokens
    assert card["health"]["enabled_count"] == 1 and card["health"]["tool_count"] == 2
    assert card["health"]["withheld_tokens"] == before.tokens - card["tokens"] > 0
    assert r["view"]["weight"]["always"] == card["tokens"]

    r = asyncio.run(put_tool(PID, ToolRequest(server="jira", tool="mcp_b", enabled=True), None))
    assert r["detail"]["tool_permissions"]["mcp_b"] is True
    assert next(i for i in r["view"]["items"] if i["key"] == "mcp:jira")["tokens"] == before.tokens
    with pytest.raises(HTTPException) as ei:
        asyncio.run(put_tool(PID, ToolRequest(server="nope", tool="x", enabled=False), None))
    assert ei.value.status_code == 404
    assert "nope" not in store["servers"]                  # 404 before any write
