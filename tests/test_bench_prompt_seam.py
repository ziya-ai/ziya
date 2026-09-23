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
