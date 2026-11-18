"""
Agent overlay, step 2 (design/agent-overlay.md): permission rules, the tool
list as a filter, and the start-up command.

The rule checks run through the real chat-record lookup
(agent_enforcement.call_refusal), which is what tool_execution calls.  Every
"refused" is paired with a call that passes on the same path.
"""
from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

import pytest

import app.plugins as plugins
from app.models.agent_definition import AgentRules
from app.models.chat import Chat, ChatUpdate
from app.services import agent_enforcement as enf
from app.services import agent_overlay as ao
from app.services import bench as svc
from app.services.token_service import TokenService
from app.storage.skills import SkillStorage
from app.utils import agent_rules as ar
from app.utils import bench_prompt as bp

PID, CID, ROOT = "proj1", "chat1", "/srv/project"
ROWS = [
    {"name": "noc", "package": "Pkg", "prompt": "You are noc.",
     "install_root": "/opt/pkg",
     "servers": ["jira-mcp", "shelved"],
     "tools": ["@builtin", "@jira-mcp", "@other/only_this", "subagent/x", "fs_read"],
     "rules": {"shell_deny": ["sudo *", "aws * delete*", "*.ssh/*"],
               "shell_allow": ["ls *", "/opt/pkg/bin/*", "python3 /opt/pkg/skills/*",
                               "/elsewhere/tool *", "/opt/pkgother/run", "/opt/pkg/../x"],
               "read_deny": ["**/.ssh/**"], "write_allow": ["/tmp/**", "/private/tmp/**"]},
     "startup": [{"command": "/x/update --hook", "timeout_secs": 3}]},
    {"name": "open", "package": "Pkg", "prompt": "p"},
]
CONFIGS = {"jira_mcp": {"service_id": "jira-mcp"}, "other": {}, "third": {},
           "shelved": {}, "shell": {"builtin": True}}


class _Chats:
    def __init__(self):
        self.chats = {c: Chat(id=c, title="t", createdAt=1, lastActiveAt=1) for c in (CID, "chat2")}

    def get(self, cid):
        return self.chats.get(cid)

    def update(self, cid, data: ChatUpdate):
        for k, v in data.model_dump(exclude_unset=True).items():
            setattr(self.chats[cid], k, v)
        return self.chats[cid]


class _Provider:
    def definitions(self):
        return ROWS


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
    monkeypatch.setattr(mgr_mod, "get_mcp_manager", lambda: SimpleNamespace(server_configs=CONFIGS))
    monkeypatch.setattr(bp, "project_id_for_root", lambda root: PID if root == ROOT else None)
    monkeypatch.setattr(enf, "_project_root", lambda pid: ROOT)
    ran = []
    monkeypatch.setattr(enf, "_run_startup",
                        lambda cmd, timeout, root: ran.append((cmd, timeout, root)) or "noc-update: ready")
    enf.reset_for_tests()
    saved = list(plugins._agent_definition_providers)
    plugins._agent_definition_providers.clear()
    plugins.register_agent_definition_provider(_Provider())
    yield chats, ran
    plugins._agent_definition_providers[:] = saved
    enf.reset_for_tests()


def _shell(command, cid=CID, tool="mcp_run_shell_command"):
    return enf.call_refusal(ROOT, cid, tool, {"command": command})


# -- the rules themselves ------------------------------------------------------

RULES = AgentRules(**ROWS[0]["rules"])


def _check(tool, **args):
    return ar.call_refusal(RULES, tool, args, project_root=ROOT, agent="noc")


def test_shell_deny_matches_the_command_and_each_segment():
    assert _check("run_shell_command", command="ls /tmp") is None
    assert _check("run_shell_command", command="aws s3 ls bucket") is None
    assert 'rule "sudo *"' in _check("run_shell_command", command="sudo ls")
    assert 'rule "sudo *"' in _check("mcp_run_shell_command", command="ls /tmp;  sudo   rm x")
    assert "aws * delete*" in _check("job_start", command="date && aws ec2 delete-volume --id v | cat")
    assert "*.ssh/*" in _check("run_shell_command", command="cat ~/.ssh/id_rsa")
    assert "noc" in _check("run_shell_command", command="sudo ls")


def test_read_deny_matches_absolute_relative_and_home_paths():
    assert _check("file_read", path="src/main.py") is None
    assert _check("file_list") is None                         # the project root
    assert _check("file_read", path=".ssh/known_hosts")
    assert _check("mcp_file_read", path="~/.ssh/id_rsa")
    assert _check("file_list", path=os.path.expanduser("~/.ssh"))
    assert _check("pdf_read_pages", path="/home/x/.ssh/doc.pdf", start_page=1)


def test_the_write_list_never_refuses_a_write():
    # Ziya's own write policy decides; the agent's list only adds to it.
    assert _check("file_write", path="/tmp/report.md", content="x") is None
    assert _check("file_write", path="notes.md", content="x") is None
    assert _check("file_write", path=".ziya/state.md", content="x") is None
    deny_only = AgentRules(shell_deny=["sudo *"])
    assert ar.call_refusal(deny_only, "file_write", {"path": "/tmp/x"}, project_root=ROOT, agent="a") is None
    assert _check("file_read", path=".ssh/known_hosts")                           # the rules are being evaluated
    assert _check("mcp_post_message", channel="c", text="t") is None              # MCP tools are not limited here


# -- through the chat record ---------------------------------------------------

def test_rules_apply_only_while_the_agent_is_on_in_that_conversation(env):
    assert _shell("sudo ls") is None                           # no agent
    ao.set_active(PID, CID, "noc")
    assert "agent noc" in _shell("sudo ls")
    assert _shell("ls /tmp") is None                           # the path runs and allows
    assert _shell("sudo ls", cid="chat2") is None              # another conversation
    assert enf.call_refusal("/elsewhere", CID, "run_shell_command", {"command": "sudo ls"}) is None
    assert enf.call_refusal(ROOT, CID, "file_write", {"path": "notes.md"}) is None
    ao.set_active(PID, CID, None)
    assert _shell("sudo ls") is None                           # off takes effect at once


def test_an_agent_without_restrictions_refuses_nothing(env):
    ao.set_active(PID, CID, "open")
    assert _shell("sudo ls") is None
    assert enf.call_refusal(ROOT, CID, "file_write", {"path": "notes.md"}) is None


def test_a_rule_that_cannot_be_evaluated_refuses(env, monkeypatch):
    ao.set_active(PID, CID, "noc")
    monkeypatch.setattr(ar, "call_refusal", lambda *a, **k: 1 / 0)
    assert "could not be checked" in _shell("ls /tmp")


# -- the baseline is Ziya's policy: deny subtracts, allow adds -------------------

def test_a_command_outside_the_allow_list_is_left_to_ziyas_policy(env):
    ao.set_active(PID, CID, "noc")
    assert "agent noc" in _shell("sudo ls")                     # the rules are on
    assert _shell("curl http://example.com") is None
    assert _shell("grep -E 'a|b' file") is None
    assert _shell("jq '.a | .b' out.json") is None
    assert _shell("ls /tmp; curl x", tool="job_start") is None
    assert enf.call_refusal(ROOT, CID, "shadow_send", {"session": "s", "line": "curl x"}) is None
    assert 'rule "sudo *"' in enf.call_refusal(ROOT, CID, "shadow_send", {"session": "s", "line": "sudo ls"})


def test_only_deny_lists_restrict():
    deny_only = AgentRules(shell_deny=["sudo *"])
    assert deny_only.restricts()
    assert ar.call_refusal(deny_only, "run_shell_command", {"command": "ls /tmp"},
                           project_root=ROOT, agent="a") is None
    assert not AgentRules().restricts()
    assert not AgentRules(mcp_allow=["x/*"], shell_allow=["ls *"], write_allow=["/tmp/**"]).restricts()
    assert AgentRules(read_deny=["**/.ssh/**"]).restricts()


# -- shell grants --------------------------------------------------------------

def test_only_allow_rules_for_scripts_inside_the_package_become_grants(env):
    import re
    noc = ao.get_definition("noc")
    grants = enf.shell_grants_for(noc)
    assert len(grants) == 2 and all(g.startswith("re:") for g in grants)

    def granted(cmd):
        return any(re.search(g[3:], cmd) for g in grants)
    assert granted("/opt/pkg/bin/preflight --fast")
    assert granted("python3 /opt/pkg/skills/triage/triage.py --to now")
    assert not granted("/opt/pkg/bin/../../etc/evil")
    assert not granted("ls /tmp") and not granted("/elsewhere/tool x") and not granted("/opt/pkgother/run")
    assert not granted("echo /opt/pkg/bin/preflight")          # anchored at the start
    assert enf.shell_grants_for(ao.get_definition("open")) == []
    assert enf.shell_grants_for(noc.model_copy(update={"install_root": None})) == []


def test_grants_follow_the_agent_being_on_and_the_import_row_counts_them(env):
    assert enf.shell_grants(ROOT, CID) == []
    ao.set_active(PID, CID, "noc")
    assert len(enf.shell_grants(ROOT, CID)) == 2
    assert enf.shell_grants(ROOT, "chat2") == []
    rules = {r["part"]: r for r in ao.import_rows(PID, ao.get_definition("noc"))}["Rules"]
    assert "2 shell allow rules for scripts inside the package" in rules["does"]
    ao.set_active(PID, CID, None)
    assert enf.shell_grants(ROOT, CID) == []


# -- write grants --------------------------------------------------------------

def test_the_write_list_becomes_grants_for_absolute_places_only(tmp_path):
    from app.models.agent_definition import AgentDefinition
    reports = tmp_path / "reports"
    one = tmp_path / "one.md"
    home = os.path.expanduser("~")
    defn = AgentDefinition(name="a", rules={"write_allow": [
        f"{reports}/**", str(one), "relative/**", f"{tmp_path}/*/deep/**", f"{tmp_path}/../x/**",
        "/**", f"{home}/**", f"{os.path.dirname(home)}/**"]})
    got = enf.write_grants_for(defn)
    assert {"path": str(reports), "is_dir": True} in got
    assert {"path": str(one), "is_dir": False} in got
    allowed = {os.path.realpath(str(reports)), str(reports), os.path.realpath(str(one)), str(one)}
    assert {e["path"] for e in got} <= allowed                  # nothing else was granted
    assert enf.write_grants_for(AgentDefinition(name="b")) == []


def test_write_grants_follow_the_agent_and_reach_file_write(env, tmp_path):
    from app.context import reset_task_writable_paths, set_task_writable_paths
    from app.mcp.tools import fileio
    assert enf.write_grants(ROOT, CID) == []
    ao.set_active(PID, CID, "noc")
    grants = enf.write_grants(ROOT, CID)
    assert {"path": "/tmp", "is_dir": True} in grants
    assert enf.write_grants(ROOT, "chat2") == []
    assert "writing to the places it lists is added" in \
        {r["part"]: r for r in ao.import_rows(PID, ao.get_definition("noc"))}["Rules"]["does"]

    outside = tmp_path / "outside"
    project = tmp_path / "project"
    outside.mkdir(); project.mkdir()
    target = str(outside / "report.md")
    assert fileio._check_task_scope_write(target, str(project)) is False      # no grant: not allowed by scope
    with pytest.raises(ValueError):
        fileio._resolve_and_validate(target, str(project), fileio._get_task_writable_prefixes())
    token = set_task_writable_paths([{"path": str(outside), "is_dir": True}])
    try:
        assert fileio._check_task_scope_write(target, str(project)) is True
        assert fileio._resolve_and_validate(target, str(project), fileio._get_task_writable_prefixes())
    finally:
        reset_task_writable_paths(token)
    ao.set_active(PID, CID, None)
    assert enf.write_grants(ROOT, CID) == []


def test_the_shell_policy_honours_the_grant_and_nothing_wider(env, tmp_path):
    from app.utils.policy_exec import adjudicate
    grants = [g for g in enf.shell_grants_for(ao.get_definition("noc"))]
    root = str(tmp_path)
    assert adjudicate("/opt/pkg/bin/preflight --fast", project_root=root)[0] is False   # refused without it
    assert adjudicate("/opt/pkg/bin/preflight --fast", project_root=root, shell_commands=grants)[0] is True
    assert adjudicate("/elsewhere/tool x", project_root=root, shell_commands=grants)[0] is False
    assert adjudicate("/opt/pkg/bin/../../evil", project_root=root, shell_commands=grants)[0] is False


# -- the tool list -------------------------------------------------------------

def _tool(name, server=None):
    return SimpleNamespace(name=name, metadata={"server_name": server} if server else {})


TOOLS = [_tool("file_read"), _tool("mcp_run_shell_command", "shell"),
         _tool("mcp_get_jira_issue", "jira_mcp"), _tool("mcp_only_this", "other"),
         _tool("mcp_something_else", "other"), _tool("mcp_fetch", "third")]


def _names(tools):
    return [t.name for t in tools]


def test_tool_scope_reads_server_entries_only():
    assert enf.tool_scope(None) is None
    assert enf.tool_scope(ROWS[0]["tools"]) == {"jiramcp": None, "other": {"onlythis"}}
    assert enf.tool_scope(["@a/x", "@a", "@a/y"]) == {"a": None}


def test_the_tool_list_limits_servers_and_keeps_builtins(env):
    kept, dropped = bp.apply_agent_tool_scope(TOOLS, bp.agent_tool_scope_for_root(ROOT, CID))
    assert (_names(kept), dropped) == (_names(TOOLS), 0)       # no agent: untouched

    ao.set_active(PID, CID, "noc")
    kept, dropped = bp.apply_agent_tool_scope(TOOLS, bp.agent_tool_scope_for_root(ROOT, CID))
    assert _names(kept) == ["file_read", "mcp_run_shell_command", "mcp_get_jira_issue", "mcp_only_this"]
    assert dropped == 2

    ao.set_active(PID, CID, "open")                            # names no tool list
    kept, dropped = bp.apply_agent_tool_scope(TOOLS, bp.agent_tool_scope_for_root(ROOT, CID))
    assert dropped == 0 and len(kept) == len(TOOLS)


def test_the_agents_servers_count_as_loaded_but_a_shelved_one_stays_shelved(env, monkeypatch):
    import app.utils.mcp_ondemand as od
    monkeypatch.setattr(od, "loaded_map", lambda *a, **k: {})
    monkeypatch.setattr(bp, "mcp_placements_for_root",
                        lambda root, cid: {"jira_mcp": "ondemand", "other": "ondemand", "shelved": "off"})
    assert bp.mcp_filter_for_root(ROOT, CID).withheld == {"jira_mcp", "other", "shelved"}
    ao.set_active(PID, CID, "noc")
    assert enf.agent_servers(PID, CID) == {"jira_mcp", "shelved"}
    assert bp.mcp_filter_for_root(ROOT, CID).withheld == {"other", "shelved"}


# -- the start-up command ------------------------------------------------------

def test_startup_runs_once_when_turned_on_and_its_output_reaches_the_turn(env):
    _chats, ran = env
    ao.set_active(PID, CID, "noc")
    assert ran == [("/x/update --hook", 3, ROOT)]
    for _ in range(2):
        pi = svc.prompt_inputs(PID, CID)
        assert "[Agent start-up output: /x/update --hook]\nnoc-update: ready" in pi.agent_addition
    assert len(ran) == 1                                       # not once per turn
    ao.set_active(PID, CID, None)
    ao.set_active(PID, CID, "open")
    assert len(ran) == 1                                       # an agent without one runs nothing
    ao.set_active(PID, CID, "noc")
    assert len(ran) == 2                                       # turned on again: runs again


def test_the_real_runner_returns_output_and_nothing_on_failure(tmp_path):
    assert enf._run_startup("echo noc-update: ready", 5, str(tmp_path)) == "noc-update: ready"
    assert enf._run_startup("false", 5, str(tmp_path)) == ""


# -- nothing blocks, and the import record says what is done -------------------

def test_the_restricted_agent_can_be_turned_on_and_its_import_rows_say_so(env):
    noc = ao.get_definition("noc")
    assert noc.rules.restricts() and noc.startup and ao.summary(noc)["runnable"] is True
    rows = {r["part"]: r for r in ao.import_rows(PID, noc)}
    assert rows["Rules"]["outcome"] == "applied" and "enforced" in rows["Rules"]["does"]
    assert rows["Tools"]["outcome"] == "applied" and "2 servers" in rows["Tools"]["does"]
    assert rows["Start-up command"]["does"].startswith("runs when the agent is turned on")


# -- wiring --------------------------------------------------------------------

def _src(rel):
    return (Path(__file__).resolve().parents[1] / rel).read_text(encoding="utf-8")


def test_tool_calls_are_checked_after_hooks_and_before_the_consent_gate():
    src = _src("app/tool_execution.py")
    at = src.index("_agent_call_refusal, ctx.project_root, ctx.conversation_id")
    assert src.index("_hooks_before_tool(") < at < src.index("# --- Consent gate")


def test_shell_calls_carry_the_agents_grants_into_the_task_scope_envelope():
    src = _src("app/tool_execution.py")
    at = src.index("_agent_shell_grants(ctx.project_root, ctx.conversation_id)")
    assert src.index("_tsc = get_task_shell_commands()") < at < src.index("task_scope_payload: Optional[Dict[str, Any]] = None")
    assert "_tsc = list(_tsc or []) + _agent_grants" in src


def test_file_write_carries_the_agents_write_grants_for_that_call_only():
    src = _src("app/tool_execution.py")
    at = src.index("_agent_write_grants(ctx.project_root, ctx.conversation_id)")
    assert src.index("_wg_token = _write_grants.activate(_write_grant)") < at
    assert at < src.index("reset_task_writable_paths(_agent_wp_token)")
    assert "_get_safe_write_paths() + _get_task_writable_prefixes()" in _src("app/mcp/tools/fileio.py")


def test_the_executor_filters_after_the_bench_and_before_the_task_allowlist():
    src = _src("app/streaming_tool_executor.py")
    at = src.index("apply_agent_tool_scope(all_tools, _agent_turn)")
    assert src.index("apply_mcp_filter(all_tools, _mcp_filter)") < at
    assert at < src.index("filter_tools_by_scope(all_tools, tool_allowlist)")
