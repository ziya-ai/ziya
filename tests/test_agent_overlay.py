"""
Agent overlay (design/agent-overlay.md, step 1).

Real SkillStorage and layer stores under a tmp ZIYA_HOME, a chat double for
the conversation record, and a fake definition provider.  The outermost
assertion for the prompt is merge_bench_prompt's result: that string is what
the server hands the turn.
"""
from __future__ import annotations

import json
import os
import time
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import app.plugins as plugins
from app.api import agents as agents_api
from app.models.chat import Chat, ChatUpdate
from app.services import agent_enforcement as enf
from app.services import agent_overlay as ao
from app.services import bench as svc
from app.services.token_service import TokenService
from app.storage.skills import SkillStorage
from app.utils import bench_prompt as bp

PID, CID = "proj1", "chat1"
SKILL = "Concise"
BASE = f"/api/v1/projects/{PID}/agents"
# Captured before the fixture substitutes a blocker for one test agent.
_REAL_BLOCKERS = ao.blockers


class _Chats:
    def __init__(self):
        self.chats = {CID: Chat(id=CID, title="t", createdAt=1, lastActiveAt=1,
                                placements={"skill:x": "off"}, modelPreference="haiku",
                                additionalPrompt="CHAT EXTRA")}

    def get(self, cid):
        return self.chats.get(cid)

    def update(self, cid, data: ChatUpdate):
        for k, v in data.model_dump(exclude_unset=True).items():
            setattr(self.chats[cid], k, v)
        return self.chats[cid]


class _Provider:
    def __init__(self, rows):
        self.rows = rows

    def definitions(self):
        return self.rows


class _Boom:
    def definitions(self):
        raise RuntimeError("boom")


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
    import app.mcp.manager as mgr_mod
    monkeypatch.setattr(mgr_mod, "get_mcp_manager",
                        lambda: SimpleNamespace(server_configs={"jira_mcp": {}}))
    # The fake definitions name the model "opus"; give the endpoint one.
    monkeypatch.setattr(ao, "_endpoint_models",
                        lambda: {"opus": {"model_id": "us.anthropic.claude-opus-9"}})
    monkeypatch.setattr(ao, "_usable", lambda alias: True)
    # Nothing blocks in this build; "blocked" stands in for a definition that
    # could not be honoured, so the refusal paths stay exercised.
    monkeypatch.setattr(ao, "blockers",
                        lambda d: ["held back for the test"] if d.name == "blocked" else [])
    monkeypatch.setattr(enf, "_project_root", lambda pid: str(tmp_path))
    monkeypatch.setattr(enf, "_run_startup", lambda cmd, timeout, root: "")
    enf.reset_for_tests()
    ctx = tmp_path / "routing.md"
    ctx.write_text("ROUTING TABLE\n")
    rows = [
        {"name": "helper", "package": "PkgA", "description": "d", "prompt": "You are the helper.",
         "context_files": [str(ctx), str(tmp_path / "gone.md")], "model": "opus",
         "skills": [SKILL, "not-installed"], "servers": ["jira-mcp", "nova"],
         "tools": ["@builtin"], "subagents": ["worker"], "sops": ["handover", "absent"],
         "notes": [{"part": "Auto-approve list", "says": "3 tools", "outcome": "unused", "does": "not used"}]},
        {"name": "helper", "package": "PkgB", "prompt": "second copy"},
        {"name": "guarded", "package": "PkgA", "prompt": "p", "rules": {"shell_deny": ["rm -rf*"]}},
        {"name": "starter", "package": "PkgA", "prompt": "p",
         "startup": [{"command": "/x/update --hook", "timeout_secs": 3}]},
        {"name": "allow-only", "package": "PkgA", "prompt": "p", "rules": {"shell_allow": ["ls *"]}},
        {"name": "blocked", "package": "PkgA", "prompt": "p"},
        {"description": "no name"},
        "not a dict",
    ]
    saved = list(plugins._agent_definition_providers)
    plugins._agent_definition_providers.clear()
    plugins.register_agent_definition_provider(_Boom())
    plugins.register_agent_definition_provider(_Provider(rows))
    yield storage, chats
    plugins._agent_definition_providers[:] = saved


def _record(chats):
    c = chats.get(CID)
    return (dict(c.placements), c.modelPreference, c.additionalPrompt)


# -- definitions -------------------------------------------------------------

def test_definitions_are_one_per_name_and_bad_rows_or_providers_are_skipped(env):
    defs = {d.name: d for d in ao.list_definitions()}
    assert sorted(defs) == ["allow-only", "blocked", "guarded", "helper", "starter"]
    assert defs["helper"].prompt == "You are the helper."      # the first copy is the one used
    assert defs["helper"].also_in == ["PkgB"]


def test_rules_and_a_startup_command_do_not_block(env):
    assert ao.get_definition("guarded").rules.restricts() and ao.get_definition("starter").startup
    for name in ("helper", "allow-only", "guarded", "starter"):
        assert _REAL_BLOCKERS(ao.get_definition(name)) == []


# -- on and off --------------------------------------------------------------

def test_on_then_off_leaves_the_rest_of_the_record_as_it_was(env):
    _storage, chats = env
    before = _record(chats)
    ao.set_active(PID, CID, "helper")
    assert chats.get(CID).activeAgent == "helper"
    assert _record(chats) == before
    ao.set_active(PID, CID, None)
    assert chats.get(CID).activeAgent is None
    assert _record(chats) == before


def test_a_refused_agent_writes_nothing(env):
    _storage, chats = env
    with pytest.raises(ao.AgentNotRunnable) as e:
        ao.set_active(PID, CID, "blocked")
    assert e.value.blockers
    with pytest.raises(ao.AgentNotFound):
        ao.set_active(PID, CID, "nobody")
    with pytest.raises(svc.ConversationRequired):
        ao.set_active(PID, None, "helper")
    assert chats.get(CID).activeAgent is None


def _real_chats(tmp_path, monkeypatch):
    from app.storage.chats import ChatStorage
    d = tmp_path / "projects" / PID
    d.mkdir(parents=True, exist_ok=True)
    storage = ChatStorage(d)
    monkeypatch.setattr(svc, "_chat_storage", lambda pid: storage)
    return storage


def test_an_unsaved_conversation_can_turn_an_agent_on_and_its_next_turn_has_it(env, tmp_path, monkeypatch):
    # "/agent helper" typed into a brand-new chat: the browser has not pushed
    # the record yet.  This was refused with "not been saved yet" and the
    # next message went out as a plain turn.
    chats = _real_chats(tmp_path, monkeypatch)
    assert chats.get("fresh") is None
    ao.set_active(PID, "fresh", "helper")
    rec = chats.get("fresh")
    assert rec is not None and rec.activeAgent == "helper" and rec.messages == []
    assert ao.active_agent_name(PID, "fresh") == "helper"
    pi = svc.prompt_inputs(PID, "fresh")
    assert pi.active_agent == "helper"
    assert "You are the helper." in pi.agent_addition


def test_unsaved_conversation_refusals_and_off_write_no_record(env, tmp_path, monkeypatch):
    chats = _real_chats(tmp_path, monkeypatch)
    with pytest.raises(ao.AgentNotRunnable):
        ao.set_active(PID, "fresh", "blocked")
    with pytest.raises(ao.AgentNotFound):
        ao.set_active(PID, "fresh", "nobody")
    assert ao.set_active(PID, "fresh", None) is None
    assert chats.get("fresh") is None


def test_a_conversation_stored_in_another_project_is_still_not_found(env, tmp_path, monkeypatch):
    from app.storage.chats import ChatStorage
    chats = _real_chats(tmp_path, monkeypatch)
    other_dir = tmp_path / "projects" / "proj2"
    other_dir.mkdir(parents=True)
    other = ChatStorage(other_dir)
    other._write_json(other._chat_file("g"), {
        "id": "g", "title": "G", "messages": [{"id": "m", "role": "human", "content": "x"}],
        "createdAt": 1, "lastActiveAt": 1})
    with pytest.raises(svc.ChatNotFound):
        ao.set_active(PID, "g", "helper")
    assert chats.get("g") is None


# -- the turn ----------------------------------------------------------------

def test_the_turn_gets_prompt_context_and_skills_and_loses_them_when_off(env):
    plain = svc.prompt_inputs(PID, CID)
    assert plain.agent_addition == "" and plain.active_agent is None

    ao.set_active(PID, CID, "helper")
    pi = svc.prompt_inputs(PID, CID)
    assert pi.active_agent == "helper"
    # The block opens by naming the agent, says how it sits on Ziya, then
    # gives the definition's own prompt.
    block = pi.agent_addition
    assert block.startswith("[Active Agent: helper]\n")
    frame, role = block.index("runs on top of Ziya"), block.index("You are the helper.")
    assert 0 < frame < role < block.index("ROUTING TABLE")
    assert "ROUTING TABLE" in pi.agent_addition                 # context file read
    assert "gone.md" not in pi.agent_addition                   # a missing one is skipped
    assert f"[Active Skill: {SKILL}]" in pi.agent_addition
    assert "not-installed" not in pi.agent_addition
    assert pi.system_prompt_addition == plain.system_prompt_addition   # the conversation's own text is untouched

    ao.set_active(PID, CID, None)
    assert svc.prompt_inputs(PID, CID).agent_addition == ""


def test_the_block_layers_the_agent_on_ziya_and_does_not_replace_it(env):
    """The agent's role applies while it is on, and Ziya's own instructions
    stay in force.  Observed 2026-11-16: with the bare ``[Active Agent]``
    header the model answered "what do you specialize in?" from the base
    prompt, because nothing said how the two relate."""
    ao.set_active(PID, CID, "helper")
    text, *_ = bp.merge_bench_prompt(svc.prompt_inputs(PID, CID), "", {}, [])
    head = text[:text.index("You are the helper.")]
    assert "does not replace" in head                           # Ziya's instructions still apply
    assert "answer as this agent" in head                       # ...and the role is taken
    assert "different name" in head                             # tool names come from another client
    assert head == ao.agent_frame("helper") + "\n\n"            # the seam: the turn carries the frame


def test_the_turn_names_servers_the_agent_expects_and_does_not_have(env, monkeypatch):
    ao.set_active(PID, CID, "helper")                           # names jira-mcp and nova; jira_mcp is configured
    block = svc.prompt_inputs(PID, CID).agent_addition
    line = next(l for l in block.splitlines() if l.startswith("[Agent servers not available here:"))
    assert "nova" in line and "jira" not in line
    assert ao.missing_servers(ao.get_definition("helper")) == ["nova"]

    import app.mcp.manager as mgr_mod
    monkeypatch.setattr(mgr_mod, "get_mcp_manager",
                        lambda: SimpleNamespace(server_configs={"jira_mcp": {}, "x": {"service_id": "nova"}}))
    block = svc.prompt_inputs(PID, CID).agent_addition
    assert "You are the helper." in block                       # the path ran
    assert "not available here" not in block


def test_the_turn_gets_the_procedures_the_agent_names(env, tmp_path, monkeypatch):
    """An agent's SOPs (``*.sop.md``) are loaded in full while it is on.
    Observed 2026-11-16: kuiper-chronicle's prompt said "follow the SOP
    exactly" and the SOP was nowhere in the turn."""
    monkeypatch.delenv("ZIYA_SOP_PATHS", raising=False)
    (tmp_path / "sops").mkdir()                                 # $ZIYA_HOME/sops
    (tmp_path / "sops" / "handover.sop.md").write_text(
        "# Handover\n\n## Steps\n\n- You MUST list open tickets\n")
    ao.set_active(PID, CID, "helper")                           # names handover and absent
    text, *_ = bp.merge_bench_prompt(svc.prompt_inputs(PID, CID), "", {}, [])
    at = text.index("[Agent procedure: handover]")
    assert text.index("You are the helper.") < at < text.index("You MUST list open tickets")
    assert "[Agent procedure: absent]" not in text
    assert "[Agent procedures not found: absent]" in text       # the model is told, not left to invent one

    rows = {r["part"]: r for r in ao.import_rows(PID, ao.get_definition("helper"))}
    assert rows["Procedures"]["outcome"] == "unavailable" and rows["Procedures"]["items"] == ["absent"]
    assert "Procedures" not in {r["part"] for r in ao.import_rows(PID, ao.get_definition("guarded"))}

    ao.set_active(PID, CID, None)
    assert "Agent procedure" not in bp.merge_bench_prompt(svc.prompt_inputs(PID, CID), "", {}, [])[0]


def test_a_skill_already_on_hand_is_not_added_twice(env):
    svc.place(PID, CID, f"skill:{SKILL}", "project", "always")
    ao.set_active(PID, CID, "helper")
    pi = svc.prompt_inputs(PID, CID)
    assert f"[Active Skill: {SKILL}]" in pi.system_prompt_addition
    assert f"[Active Skill: {SKILL}]" not in pi.agent_addition
    assert pi.agent_addition.startswith("[Active Agent: helper]")


def test_the_merged_prompt_leads_with_the_agent_adopted_bench_or_not(env):
    ao.set_active(PID, CID, "helper")
    pi = svc.prompt_inputs(PID, CID)
    assert not pi.authoritative
    text, *_ = bp.merge_bench_prompt(pi, "CLIENT TEXT", {}, [])
    assert text.startswith("[Active Agent: helper]") and text.endswith("CLIENT TEXT")

    svc.place(PID, CID, f"skill:{SKILL}", "project", "always")
    pi = svc.prompt_inputs(PID, CID)
    assert pi.authoritative
    text, *_ = bp.merge_bench_prompt(pi, "CLIENT TEXT", {}, [])
    assert text.startswith("[Active Agent: helper]")
    assert "CLIENT TEXT" not in text and "CHAT EXTRA" in text

    ao.set_active(PID, CID, None)
    text, *_ = bp.merge_bench_prompt(svc.prompt_inputs(PID, CID), "CLIENT TEXT", {}, [])
    assert "Active Agent" not in text


def test_an_agent_that_was_uninstalled_or_became_blocked_is_ignored(env):
    _storage, chats = env
    for name in ("nobody", "blocked"):
        chats.get(CID).activeAgent = name
        assert ao.overlay_for(PID, CID) is None
        assert svc.prompt_inputs(PID, CID).agent_addition == ""
    chats.get(CID).activeAgent = "helper"
    assert ao.overlay_for(PID, CID).name == "helper"            # the path does run


# -- the import record --------------------------------------------------------

def test_import_rows_say_what_is_missing_and_what_is_not_done_yet(env):
    rows = {r["part"]: r for r in ao.import_rows(PID, ao.get_definition("helper"))}
    assert rows["Prompt"]["outcome"] == "unavailable" and rows["Prompt"]["items"][0].endswith("gone.md")
    assert rows["Skills"]["items"] == ["not-installed"]
    assert rows["Servers"]["items"] == ["nova"]                 # jira-mcp matches jira_mcp
    assert rows["Tools"]["outcome"] == "applied"
    assert rows["Specialists"]["outcome"] == "pending"
    assert rows["Model"]["outcome"] == "mapped"
    assert rows["Auto-approve list"]["outcome"] == "unused"
    starter = {r["part"]: r for r in ao.import_rows(PID, ao.get_definition("starter"))}
    assert starter["Start-up command"]["says"] == "/x/update --hook (3 s limit)"
    guarded = {r["part"]: r for r in ao.import_rows(PID, ao.get_definition("guarded"))}
    assert guarded["Rules"]["outcome"] == "applied" and "enforced" in guarded["Rules"]["does"]


# -- the API -----------------------------------------------------------------

@pytest.fixture
def client(env):
    api = FastAPI()
    api.include_router(agents_api.router)
    return TestClient(api)


def test_api_lists_turns_on_and_off_and_refuses(client):
    body = client.get(f"{BASE}?conversation_id={CID}").json()
    by = {a["name"]: a for a in body["agents"]}
    assert body["active"] is None
    assert by["helper"]["runnable"] is True and by["guarded"]["runnable"] is True
    assert by["blocked"]["runnable"] is False

    r = client.put(f"{BASE}/active", json={"conversation_id": CID, "name": "helper"})
    assert r.status_code == 200 and r.json()["active"] == "helper"
    assert client.get(f"{BASE}?conversation_id={CID}").json()["active"] == "helper"

    r = client.put(f"{BASE}/active", json={"conversation_id": CID, "name": "blocked"})
    assert r.status_code == 409 and r.json()["detail"]["blockers"]
    assert client.get(f"{BASE}?conversation_id={CID}").json()["active"] == "helper"   # refusal changed nothing

    assert client.put(f"{BASE}/active", json={"conversation_id": CID, "name": "nobody"}).status_code == 404
    assert client.put(f"{BASE}/active", json={"conversation_id": "zzz", "name": "helper"}).status_code == 404
    assert client.put(f"{BASE}/active", json={"name": "helper"}).status_code == 400

    r = client.put(f"{BASE}/active", json={"conversation_id": CID, "name": None})
    assert r.status_code == 200 and r.json()["active"] is None


def test_api_import_record(client):
    r = client.get(f"{BASE}/import?name=starter")
    assert r.status_code == 200
    assert r.json()["runnable"] is True
    assert [x["part"] for x in r.json()["rows"]] == ["Prompt", "Start-up command"]
    assert client.get(f"{BASE}/import?name=nobody").status_code == 404


# -- sync must not turn the agent off ------------------------------------------

def test_a_sync_push_that_omits_the_field_keeps_the_agent_on(tmp_path):
    home = tmp_path / ".ziya"
    pid = "proj-agent"
    proj = home / "projects" / pid
    (proj / "chats").mkdir(parents=True)
    (proj / "project.json").write_text(json.dumps({
        "id": pid, "name": "P", "path": "/x",
        "settings": {"defaultContextIds": [], "defaultSkillIds": []},
        "createdAt": 1, "lastAccessedAt": 1,
    }))
    with patch.dict(os.environ, {"ZIYA_HOME": str(home)}), \
            patch("app.api.chats.get_ziya_home", return_value=home), \
            patch("app.api.chats.get_project_dir", return_value=proj):
        from app.api.chats import router
        api = FastAPI()
        api.include_router(router)
        tc = TestClient(api)
        v0 = int(time.time() * 1000)
        url = f"/api/v1/projects/{pid}/chats/bulk-sync"
        chat = {"id": "c1", "title": "T", "messages": [], "createdAt": v0, "lastActiveAt": v0, "_version": v0}
        assert tc.post(url, json={"chats": [chat]}).status_code == 200
        path = proj / "chats" / "c1.json"
        raw = json.loads(path.read_text())
        raw["activeAgent"] = "helper"                           # set_active landed after the last pull
        path.write_text(json.dumps(raw))

        push = {**chat, "_version": v0 + 10, "lastActiveAt": v0 + 10,
                "messages": [{"id": "m1", "role": "human", "content": "next", "timestamp": v0 + 10}]}
        out = tc.post(url, json={"chats": [push]})
        assert out.status_code == 200 and out.json()["updated"] == 1
        on_disk = json.loads(path.read_text())
        assert [m["id"] for m in on_disk["messages"]] == ["m1"]  # the push did land
        assert on_disk["activeAgent"] == "helper"


# -- the model ----------------------------------------------------------------

def test_model_names_map_to_exact_models_only():
    from app.agents.models import ModelManager
    bedrock = ModelManager.MODEL_CONFIGS["bedrock"]
    assert ao.match_model_alias("claude-opus-5.5", bedrock) == "opus5.5"
    assert ao.match_model_alias("claude-opus-5", bedrock) == "opus5"          # not opus5.5
    assert ao.match_model_alias("claude-haiku-4.5", bedrock) == "haiku-4.5"
    assert ao.match_model_alias("opus4.5", bedrock) == "opus4.5"
    assert ao.match_model_alias("claude-opus-9.9", bedrock) is None
    assert ao.match_model_alias("gpt-4", bedrock) is None
    assert ao.match_model_alias(None, bedrock) is None
    by_id = {"big": {"model_id": {"us": "us.anthropic.claude-opus-4-5-20251101-v1:0"}}}
    assert ao.match_model_alias("claude-opus-4.5", by_id) == "big"            # dated model id
    assert ao.match_model_alias("claude-opus-4", by_id) is None


def test_the_mapped_model_is_reported_and_an_unusable_one_is_not(env, monkeypatch):
    helper = ao.get_definition("helper")
    assert ao.summary(helper)["model"] == "opus" and ao.summary(helper)["model_alias"] == "opus"
    row = {r["part"]: r for r in ao.import_rows(PID, helper)}["Model"]
    assert row["outcome"] == "mapped" and row["does"].startswith("opus,")
    assert ao.summary(ao.get_definition("guarded"))["model_alias"] is None    # names no model

    monkeypatch.setattr(ao, "_usable", lambda alias: False)
    assert ao.summary(helper)["model_alias"] is None
    row = {r["part"]: r for r in ao.import_rows(PID, helper)}["Model"]
    assert row["outcome"] == "unavailable" and "conversation's model" in row["does"]
