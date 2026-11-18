"""
Agent overlay (design/agent-overlay.md, step 1).

An agent is an overlay on a conversation: the chat record's ``activeAgent``
names it, and each turn is assembled with the agent's prompt, context files
and skills on top of what the conversation already has.  Turning it off
clears that one field; nothing else was written.

This module applies prompt, context files and skills.  The definition's
rules, tool list and start-up command are enforced and run by
app/services/agent_enforcement.py.
The model name is matched to a model here (``model_alias``); the client
applies it per conversation.  Dispatching specialists is reported in the
import record but not available yet.
"""
from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from app.models.agent_definition import AgentDefinition, ImportRow
from app.services import bench as svc

logger = logging.getLogger(__name__)


class AgentNotFound(LookupError):
    pass


class AgentNotRunnable(ValueError):
    def __init__(self, name: str, blockers: List[str]):
        self.name = name
        self.blockers = blockers
        super().__init__(f"{name} cannot be turned on: {'; '.join(blockers)}")


@dataclass
class Overlay:
    name: str
    package: str
    model: Optional[str]
    prompt_block: str
    skill_names: List[str] = field(default_factory=list)


def list_definitions() -> List[AgentDefinition]:
    """Installed definitions, one per name.  The first provider row for a
    name is used; other packages shipping the same name are recorded in
    ``also_in``.  An invalid row is skipped."""
    try:
        from app.plugins import agent_definition_rows
        rows = agent_definition_rows()
    except Exception as e:  # noqa: BLE001
        logger.debug("agent overlay: definitions unavailable: %s", e)
        return []
    out: Dict[str, AgentDefinition] = {}
    for row in rows:
        try:
            d = AgentDefinition.model_validate(row)
        except Exception as e:  # noqa: BLE001 -- pydantic ValidationError
            logger.debug("agent overlay: definition skipped: %s", e)
            continue
        if not d.name:
            continue
        first = out.get(d.name)
        if first is None:
            out[d.name] = d
        elif d.package and d.package != first.package and d.package not in first.also_in:
            first.also_in.append(d.package)
    return list(out.values())


def get_definition(name: str) -> Optional[AgentDefinition]:
    return next((d for d in list_definitions() if d.name == name), None)


def blockers(defn: AgentDefinition) -> List[str]:
    """Why this definition cannot be turned on in this build; empty = it can.
    Nothing blocks at present; the API and the interface carry the field so
    a part that cannot be honoured is refused rather than ignored."""
    return []


_SQUASH = re.compile(r"[^a-z0-9]+")


def match_model_alias(name: Optional[str], configs: Dict[str, Any]) -> Optional[str]:
    """The alias in ``configs`` (``alias -> model config``) that a definition's
    model name refers to, or None.

    Exact matches only: the alias itself, with or without a ``claude``
    prefix and ignoring punctuation (``claude-opus-5.5`` -> ``opus5.5``), or
    a model id that ends with the name, optionally followed by a date or
    version suffix.  ``claude-opus-5`` does not match ``...claude-opus-5-5``.
    """
    low = str(name or "").lower()
    want = _SQUASH.sub("", low)
    if not want:
        return None
    bare = want[len("claude"):] if want.startswith("claude") else want
    for alias in configs:
        if _SQUASH.sub("", str(alias).lower()) in (want, bare):
            return str(alias)
    hyphen = _SQUASH.sub("-", low).strip("-")
    pat = re.compile(r"(?:^|[^a-z0-9])" + re.escape(hyphen) + r"(?:$|[:@]|-v\d|-\d{8})")
    for alias, cfg in configs.items():
        mid = cfg.get("model_id") if isinstance(cfg, dict) else None
        ids = list(mid.values()) if isinstance(mid, dict) else [mid]
        if any(isinstance(i, str) and pat.search(i.lower()) for i in ids):
            return str(alias)
    return None


def _endpoint_models() -> Dict[str, Any]:
    """``alias -> config`` for the running endpoint; empty when unknown."""
    try:
        from app.agents.models import ModelManager
        return dict(ModelManager.MODEL_CONFIGS.get(os.environ.get("ZIYA_ENDPOINT", "bedrock")) or {})
    except Exception as e:  # noqa: BLE001
        logger.debug("agent overlay: model list unavailable: %s", e)
        return {}


def _usable(alias: str) -> bool:
    """Whether a chat request may name this model (the model-pin gate)."""
    try:
        from app.utils.model_override import validate_model_override
        return not validate_model_override(alias, os.environ.get("ZIYA_ENDPOINT", "bedrock"))
    except Exception as e:  # noqa: BLE001
        logger.debug("agent overlay: model check unavailable: %s", e)
        return False


def model_alias(name: Optional[str]) -> Optional[str]:
    """The model here that a definition's model name refers to, or None when
    there is no exact match or it may not be used."""
    alias = match_model_alias(name, _endpoint_models())
    return alias if alias and _usable(alias) else None


def summary(defn: AgentDefinition) -> Dict[str, Any]:
    why = blockers(defn)
    return {
        "name": defn.name, "description": defn.description,
        "package": defn.package, "also_in": list(defn.also_in),
        "model": defn.model, "model_alias": model_alias(defn.model),
        "skills": len(defn.skills), "servers": len(defn.servers),
        "subagents": len(defn.subagents),
        "runnable": not why, "blockers": why,
    }


def _plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


def missing_servers(defn: AgentDefinition) -> List[str]:
    """Servers the definition names that are not configured here, matched
    by name or service id.  Empty when the MCP manager cannot be asked."""
    if not defn.servers:
        return []
    try:
        from app.mcp.manager import get_mcp_manager
        configs = get_mcp_manager().server_configs or {}
    except Exception as e:  # noqa: BLE001
        logger.debug("agent overlay: MCP manager unavailable: %s", e)
        return []
    have = set()
    for name, cfg in configs.items():
        have.add(svc._norm_name(name))
        if isinstance(cfg, dict) and cfg.get("service_id"):
            have.add(svc._norm_name(cfg["service_id"]))
    return [s for s in defn.servers if svc._norm_name(s) not in have]


def _sops(project_id: str, defn: AgentDefinition) -> Tuple[List[Tuple[str, str]], List[str]]:
    """``(name, text)`` of each Agent SOP the definition names that was
    found, and the names that were not (app/utils/agent_sops.py)."""
    if not defn.sops:
        return [], []
    from app.utils.agent_sops import read_sop
    try:
        from app.services.agent_enforcement import _project_root
        root = _project_root(project_id)
    except Exception as e:  # noqa: BLE001 -- the user-level roots are still read
        logger.debug("agent overlay: project root unavailable: %s", e)
        root = None
    found: List[Tuple[str, str]] = []
    absent: List[str] = []
    for name in defn.sops:
        got = read_sop(name, project_root=root, prefer_under=defn.install_root)
        if got is None:
            absent.append(name)
        else:
            found.append((name, got[1]))
    return found, absent


def agent_frame(name: str) -> str:
    """The opening of an agent's prompt block: how the agent relates to the
    rest of the system prompt (design/agent-overlay.md, "How the prompt
    block is framed").  The agent is layered on Ziya; it does not replace it."""
    return (
        f"[Active Agent: {name}]\n"
        "The user turned this agent on for this conversation. It runs on top of Ziya "
        "and does not replace it: everything stated earlier in this prompt still "
        "applies, including the tools, the write policy, diagrams and memory.\n"
        "While it is on, take the role described below and follow its instructions "
        "and procedures for the work it covers. Asked who you are or what you can "
        "do, answer as this agent running in Ziya. Where its instructions conflict "
        "with Ziya's rules on safety, permissions or how files are changed, Ziya's "
        "rules apply; on what the work is and how to carry it out, the agent's "
        "instructions apply.\n"
        "Its instructions were written for another client. A tool they name may be "
        "listed here under a different name, for example with an mcp_ prefix; use "
        "the tool available here that does the same thing."
    )


def import_rows(project_id: str, defn: AgentDefinition) -> List[Dict[str, Any]]:
    """The import view: per part of the definition, what the source said and
    what Ziya does with it.  Built from the same definition the turn uses."""
    rows: List[ImportRow] = []
    missing_ctx = [p for p in defn.context_files if not Path(p).is_file()]
    rows.append(ImportRow(
        part="Prompt",
        says=f"system prompt + {_plural(len(defn.context_files), 'context file')}",
        does="applied as written" if not missing_ctx else "applied; some context files are missing",
        outcome="applied" if not missing_ctx else "unavailable", items=missing_ctx))
    if defn.sops:
        found, absent = _sops(project_id, defn)
        rows.append(ImportRow(
            part="Procedures", says=f"{len(defn.sops)} named",
            does=(f"{len(found)} loaded in full while the agent is on" if not absent
                  else f"{len(found)} loaded, {len(absent)} not found"),
            outcome="applied" if not absent else "unavailable", items=absent))
    if defn.model:
        alias = model_alias(defn.model)
        rows.append(ImportRow(
            part="Model", says=defn.model,
            outcome="mapped" if alias else "unavailable",
            does=(f"{alias}, unless this conversation has its own model pin" if alias
                  else "no matching model here; the conversation's model is used")))
    if defn.skills:
        try:
            have = {s.name for s in svc._skill_storage(project_id).list()}
        except Exception as e:  # noqa: BLE001
            logger.debug("agent overlay: skills unavailable: %s", e)
            have = set(defn.skills)
        missing = [s for s in defn.skills if s not in have]
        rows.append(ImportRow(
            part="Skills", says=f"{len(defn.skills)} named",
            does=(f"{len(defn.skills)} found" if not missing
                  else f"{len(defn.skills) - len(missing)} found, {len(missing)} not installed here"),
            outcome="applied" if not missing else "unavailable", items=missing))
    if defn.servers:
        missing = missing_servers(defn)
        rows.append(ImportRow(
            part="Servers", says=f"{len(defn.servers)} named",
            does=(f"{len(defn.servers)} available" if not missing
                  else f"{len(defn.servers) - len(missing)} available, {len(missing)} not installed here"),
            outcome="applied" if not missing else "unavailable", items=missing))
    if defn.tools is not None:
        from app.services.agent_enforcement import tool_scope
        named = len(tool_scope(defn.tools) or {})
        rows.append(ImportRow(
            part="Tools", says=_plural(len(defn.tools), "entry").replace("entrys", "entries"),
            outcome="applied",
            does=f"limited to Ziya's built-in tools and {_plural(named, 'server')} it names",
            items=list(defn.tools)))
    if defn.rules.any():
        r = defn.rules
        from app.services.agent_enforcement import shell_grants_for, write_grants_for
        granted = len(shell_grants_for(defn))
        grants = (f"{_plural(granted, 'shell allow rule')} for scripts inside the package "
                  f"granted to the conversation" if granted else "no package scripts granted")
        if write_grants_for(defn):
            grants += "; writing to the places it lists is added"
        rows.append(ImportRow(
            part="Rules",
            says=(f"shell {len(r.shell_deny)} deny / {len(r.shell_allow)} allow · "
                  f"file read {len(r.read_deny)} deny · file write {len(r.write_allow)} allow"),
            outcome="applied",
            does=(f"deny rules enforced on top of Ziya's own policy; its allow lists "
                  f"add to that policy and remove nothing; {grants}" if r.restricts()
                  else f"nothing denied; its allow lists add to Ziya's own policy; {grants}")))
    if defn.subagents:
        rows.append(ImportRow(part="Specialists", says=f"{len(defn.subagents)} it may dispatch",
                              outcome="pending", does="dispatch is not available yet", items=list(defn.subagents)))
    for cmd in defn.startup:
        limit = f" ({cmd.timeout_secs:g} s limit)" if cmd.timeout_secs else ""
        rows.append(ImportRow(part="Start-up command", says=f"{cmd.command}{limit}", outcome="startup",
                              does="runs when the agent is turned on; its output goes into the agent's context"))
    rows.extend(defn.notes)
    return [r.model_dump() for r in rows]


def active_agent_name(project_id: str, conversation_id: Optional[str]) -> Optional[str]:
    if not conversation_id:
        return None
    chat = svc._chat_storage(project_id).get(conversation_id)
    return (getattr(chat, "activeAgent", None) or None) if chat else None


def set_active(project_id: str, conversation_id: Optional[str],
               name: Optional[str]) -> Optional[AgentDefinition]:
    """Turn an agent on (``name``) or off (None) for one conversation.

    Writes only ``activeAgent``.  Raises AgentNotFound, AgentNotRunnable,
    bench.ConversationRequired, bench.ChatNotFound; a refusal writes nothing.
    """
    if not conversation_id:
        raise svc.ConversationRequired("an agent is turned on for a conversation")
    defn: Optional[AgentDefinition] = None
    if name:
        defn = get_definition(name)
        if defn is None:
            raise AgentNotFound(name)
        why = blockers(defn)
        if why:
            raise AgentNotRunnable(name, why)
    storage = svc._chat_storage(project_id)
    with svc._project_store(project_id).locked():
        if storage.get(conversation_id) is None:
            raise svc.ChatNotFound(conversation_id)
        from app.models.chat import ChatUpdate
        storage.update(conversation_id, ChatUpdate(activeAgent=name or None))
    # Rules and tool list follow the record at once; a start-up command runs
    # now, so its output is ready for the first turn.
    from app.services import agent_enforcement as enf
    enf.forget(project_id, conversation_id)
    if defn is not None:
        enf.startup_notes(project_id, conversation_id, defn)
    return defn


def overlay_for(project_id: str, conversation_id: Optional[str]) -> Optional[Overlay]:
    """What the active agent adds to this conversation's next turn; None
    when no agent is active, or the named one is no longer installed or can
    no longer be turned on."""
    name = active_agent_name(project_id, conversation_id)
    if not name:
        return None
    defn = get_definition(name)
    if defn is None or blockers(defn):
        logger.info("agent overlay: %s is named by the conversation but is not usable; ignored", name)
        return None
    parts = ["\n\n".join(p for p in (agent_frame(defn.name), defn.prompt.rstrip()) if p)]
    for p in defn.context_files:
        try:
            text = Path(p).read_text(encoding="utf-8")
        except OSError:
            continue
        parts.append(f"[Agent context: {p}]\n{text.rstrip()}")
    procedures, lost = _sops(project_id, defn)
    for sop, text in procedures:
        parts.append(f"[Agent procedure: {sop}]\n{text.rstrip()}")
    if lost:
        parts.append(
            f"[Agent procedures not found: {', '.join(lost)}]\n"
            "The agent's instructions refer to these procedures and the files are not "
            "installed here. Tell the user; do not reconstruct a procedure from its name.")
    absent = missing_servers(defn)
    if absent:
        parts.append(
            f"[Agent servers not available here: {', '.join(absent)}]\n"
            "The agent's instructions expect tools from these servers and they are "
            "not connected. Do the rest of the work, do not guess at what they would "
            "have returned, and tell the user which steps were skipped for this reason.")
    from app.services.agent_enforcement import startup_notes
    notes = startup_notes(project_id, conversation_id, defn)
    if notes:
        parts.append(notes)
    return Overlay(name=defn.name, package=defn.package, model=defn.model,
                   prompt_block="\n\n".join(parts), skill_names=list(defn.skills))
