"""
What an active agent enforces during a turn (design/agent-overlay.md, step 2):
its permission rules per tool call, its tool list as a filter on the tools
sent to the model, and its start-up command.

Everything is derived from the chat record's ``activeAgent``; nothing is
stored.  The per-call lookup remembers which agent a conversation has for a
few seconds, so a long tool batch does not re-read the chat record per call;
``forget`` (called by agent_overlay.set_active) drops it at once.
"""
from __future__ import annotations

import fnmatch
import logging
import os
import re
import subprocess
import threading
import time
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

from app.models.agent_definition import AgentDefinition
from app.utils import agent_rules

logger = logging.getLogger(__name__)

ACTIVE_TTL_SECS = 3.0
STARTUP_DEFAULT_TIMEOUT_SECS = 10.0
STARTUP_NOTE_CHARS = 4000

_lock = threading.Lock()
_active: Dict[Tuple[str, str], Tuple[float, Optional[str]]] = {}
_startup: Dict[Tuple[str, str, str], str] = {}
_NON_ALNUM = re.compile(r"[^a-z0-9]+")

Scope = Dict[str, Optional[Set[str]]]


def _norm(name: Any) -> str:
    return _NON_ALNUM.sub("", agent_rules.bare_tool_name(name).lower())


def forget(project_id: str, conversation_id: Optional[str]) -> None:
    """Drop what is remembered about one conversation's agent."""
    with _lock:
        _active.pop((project_id, conversation_id or ""), None)
        for key in [k for k in _startup if k[:2] == (project_id, conversation_id or "")]:
            del _startup[key]


def reset_for_tests() -> None:
    with _lock:
        _active.clear()
        _startup.clear()


def active_definition(project_id: Optional[str], conversation_id: Optional[str]) -> Optional[AgentDefinition]:
    """The usable definition of the agent on in this conversation, or None."""
    if not project_id or not conversation_id:
        return None
    from app.services import agent_overlay as ao
    key = (project_id, conversation_id)
    now = time.monotonic()
    with _lock:
        hit = _active.get(key)
    if hit and now - hit[0] < ACTIVE_TTL_SECS:
        name = hit[1]
    else:
        name = ao.active_agent_name(project_id, conversation_id)
        with _lock:
            _active[key] = (now, name)
    if not name:
        return None
    defn = ao.get_definition(name)
    return None if defn is None or ao.blockers(defn) else defn


# -- permission rules ---------------------------------------------------------

def call_refusal(project_root: Optional[str], conversation_id: Optional[str],
                 tool_name: str, args: Any) -> Optional[str]:
    """Refusal text when the active agent's rules forbid this tool call.

    Not knowing whether an agent is on (lookup failed) allows the call, as a
    conversation without an agent would.  Knowing one is on and failing to
    evaluate its rules refuses it.
    """
    if not project_root or not conversation_id:
        return None
    try:
        from app.utils.bench_prompt import project_id_for_root
        defn = active_definition(project_id_for_root(project_root), conversation_id)
    except Exception as e:  # noqa: BLE001
        logger.warning("agent rules: lookup failed, call not checked: %s", e)
        return None
    if defn is None or not defn.rules.restricts():
        return None
    try:
        return agent_rules.call_refusal(defn.rules, tool_name, args,
                                        project_root=project_root, agent=defn.name)
    except Exception as e:  # noqa: BLE001
        logger.warning("agent rules: %s could not be evaluated: %s", defn.name, e)
        return (f"Refused: the rules of the agent {defn.name} could not be checked "
                f"for this call ({e}). Tell the user; do not retry.")


# -- shell grants --------------------------------------------------------------

# An allow rule may run a package script through one of these.
_INTERPRETERS = frozenset({"python3", "/usr/bin/python3"})
_STARS = re.compile(r"\*{2,}")


def shell_grants_for(defn: AgentDefinition) -> List[str]:
    """The definition's shell allow rules that name a script inside its own
    package, as shell-policy grants (``re:<regex>``, the Task Card grant
    form).

    Only those: a rule for a general command (``ls *``, ``aws ...``) is left
    to the user's shell configuration, so a package cannot widen it.  A
    command containing ``..`` never matches.  A grant cannot bypass the
    always-blocked commands or redirection blocking, and the agent's deny
    rules are checked before the shell policy is asked.
    """
    root = defn.install_root
    if not root:
        return []
    roots = {os.path.normpath(root)}
    try:
        roots.add(os.path.realpath(root))
    except OSError:
        pass
    out: List[str] = []
    for pattern in defn.rules.shell_allow:
        tokens = str(pattern).split()
        if not tokens or ".." in str(pattern):
            continue
        script = tokens[1] if tokens[0] in _INTERPRETERS and len(tokens) > 1 else tokens[0]
        if not any(script.startswith(r + os.sep) for r in roots):
            continue
        grant = r"re:\A(?!.*\.\.)" + fnmatch.translate(_STARS.sub("*", " ".join(tokens)))
        if grant not in out:
            out.append(grant)
    return out


def shell_grants(project_root: Optional[str], conversation_id: Optional[str]) -> List[str]:
    """Grants the active agent brings to a shell call in this conversation;
    none when no agent is on or the lookup fails."""
    if not project_root or not conversation_id:
        return []
    try:
        from app.utils.bench_prompt import project_id_for_root
        defn = active_definition(project_id_for_root(project_root), conversation_id)
        return shell_grants_for(defn) if defn is not None else []
    except Exception as e:  # noqa: BLE001
        logger.warning("agent shell grants unavailable: %s", e)
        return []


# -- write grants --------------------------------------------------------------

def write_grants_for(defn: AgentDefinition) -> List[Dict[str, Any]]:
    """The definition's write list as task-scope write entries
    (``{"path", "is_dir"}``), added to Ziya's own write policy while the
    agent is on.  Nothing Ziya already permits is removed.

    Only absolute paths: ``/dir/**`` (a directory) or a plain file path.
    A pattern with any other wildcard, a ``..`` segment, or one that would
    cover the home directory or the filesystem root is not granted.  The
    install-extension and server-only floors in the write policy still
    apply.
    """
    home = os.path.realpath(os.path.expanduser("~"))
    out: List[Dict[str, Any]] = []
    for pattern in defn.rules.write_allow:
        p = os.path.expanduser(str(pattern).strip())
        is_dir = False
        for tail in ("/**", "/*"):
            if p.endswith(tail):
                p, is_dir = p[:-len(tail)], True
                break
        if not os.path.isabs(p) or any(c in p for c in "*?[") or ".." in p.split(os.sep):
            continue
        norm = os.path.normpath(p)
        for cand in dict.fromkeys((norm, os.path.realpath(norm))):
            if is_dir and (cand == home or home.startswith(cand.rstrip(os.sep) + os.sep)):
                continue
            entry = {"path": cand, "is_dir": is_dir}
            if entry not in out:
                out.append(entry)
    return out


def write_grants(project_root: Optional[str], conversation_id: Optional[str]) -> List[Dict[str, Any]]:
    """Write entries the active agent adds for a ``file_write`` in this
    conversation; none when no agent is on or the lookup fails."""
    if not project_root or not conversation_id:
        return []
    try:
        from app.utils.bench_prompt import project_id_for_root
        defn = active_definition(project_id_for_root(project_root), conversation_id)
        return write_grants_for(defn) if defn is not None else []
    except Exception as e:  # noqa: BLE001
        logger.warning("agent write grants unavailable: %s", e)
        return []


# -- tool list ----------------------------------------------------------------

def tool_scope(tools: Optional[Iterable[str]]) -> Optional[Scope]:
    """``@server`` and ``@server/tool`` entries as ``server -> None (all) |
    {tools}``; None when the definition does not limit tools.  ``@builtin``,
    ``subagent/...`` and another client's built-in tool names carry no server
    and are skipped: Ziya's own built-in tools are always kept."""
    if tools is None:
        return None
    scope: Scope = {}
    for entry in tools:
        e = str(entry).strip()
        if not e.startswith("@"):
            continue
        server, _, tool = e[1:].partition("/")
        key = _norm(server)
        if not key or key == "builtin":
            continue
        if not tool or tool == "*":
            scope[key] = None
        elif key not in scope:
            scope[key] = {_norm(tool)}
        elif scope[key] is not None:
            scope[key].add(_norm(tool))
    return scope


def keep_tool(scope: Optional[Scope], server: Optional[str], tool_name: str,
              configs: Dict[str, Any]) -> bool:
    if scope is None or not server:
        return True                     # no limit, or a Ziya built-in tool
    cfg = configs.get(server)
    cfg = cfg if isinstance(cfg, dict) else {}
    if cfg.get("builtin"):
        return True
    for ident in (_norm(server), _norm(cfg.get("service_id"))):
        if ident and ident in scope:
            allowed = scope[ident]
            return allowed is None or _norm(tool_name) in allowed
    return False


def _configs() -> Dict[str, Any]:
    try:
        from app.mcp.manager import get_mcp_manager
        return dict(get_mcp_manager().server_configs or {})
    except Exception as e:  # noqa: BLE001
        logger.debug("agent enforcement: MCP manager unavailable: %s", e)
        return {}


def turn_tools(project_id: Optional[str], conversation_id: Optional[str]):
    """``(agent name, scope, server configs)`` for this turn; None when no
    agent is on."""
    defn = active_definition(project_id, conversation_id)
    if defn is None:
        return None
    return defn.name, tool_scope(defn.tools), _configs()


def agent_servers(project_id: Optional[str], conversation_id: Optional[str]) -> Set[str]:
    """Configured MCP servers the active agent names (by name or service id)."""
    defn = active_definition(project_id, conversation_id)
    if defn is None or not defn.servers:
        return set()
    wanted = {_norm(s) for s in defn.servers}
    out = set()
    for name, cfg in _configs().items():
        sid = cfg.get("service_id") if isinstance(cfg, dict) else None
        if _norm(name) in wanted or (sid and _norm(sid) in wanted):
            out.add(name)
    return out


# -- start-up command ---------------------------------------------------------

def _project_root(project_id: str) -> Optional[str]:
    from app.storage.projects import ProjectStorage
    from app.utils.paths import get_ziya_home
    p = ProjectStorage(get_ziya_home()).get(project_id)
    return p.path if p else None


def _run_startup(command: str, timeout: float, project_root: str) -> str:
    """Run one start-up command and return its output ('' when it was
    refused, failed, timed out or printed nothing).

    The shell policy is asked with a grant for exactly this command line:
    the user turned the agent on with the command shown in its import
    record.  The grant covers nothing else.
    """
    from app.utils import policy_exec
    allowed, why, shell_env = policy_exec.adjudicate(
        command, project_root=project_root, shell_commands=[command])
    if not allowed:
        logger.warning("agent start-up command refused by the shell policy: %s", why)
        return ""
    env = policy_exec.child_env(shell_env, project_root=project_root, cwd=project_root)
    try:
        cp = policy_exec.run_sh(command, project_root, env, timeout)
    except (subprocess.TimeoutExpired, OSError) as e:
        logger.warning("agent start-up command did not finish: %s", e)
        return ""
    if cp.returncode != 0:
        logger.warning("agent start-up command exited %s", cp.returncode)
        return ""
    return policy_exec.output_tail(cp, STARTUP_NOTE_CHARS)


def startup_notes(project_id: str, conversation_id: Optional[str], defn: AgentDefinition) -> str:
    """Output of the definition's start-up commands, run once per
    conversation and agent in this process (again after ``forget``)."""
    if not defn.startup or not conversation_id:
        return ""
    key = (project_id, conversation_id, defn.name)
    with _lock:
        if key in _startup:
            return _startup[key]
        _startup[key] = ""              # reserve: a concurrent build must not run it twice
    parts = []
    try:
        root = _project_root(project_id)
        for cmd in defn.startup if root else []:
            out = _run_startup(cmd.command, cmd.timeout_secs or STARTUP_DEFAULT_TIMEOUT_SECS, root)
            if out:
                parts.append(f"[Agent start-up output: {cmd.command}]\n{out}")
    except Exception as e:  # noqa: BLE001 -- a start-up command must never fail a turn
        logger.warning("agent start-up for %s failed: %s", defn.name, e)
    notes = "\n\n".join(parts)
    with _lock:
        _startup[key] = notes
    return notes
