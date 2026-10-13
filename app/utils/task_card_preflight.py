"""Task-card preflight: is the environment the one the card was written for?

A card names skills (in ``requires.skills`` and in every scope's ``skills``),
tools (``requires.tools`` and every scope's ``tools``) and MCP servers
(``requires.mcp_servers``).  The executor already notices a missing skill
or tool, but only when it reaches the task that asked for it, and only as
a line in that task's artifact decisions.  For a headless batch run that
is the wrong time and place: the operator finds out an hour in, from a
confused model, that the search server was never configured on this host.

``check_requirements`` is pure over its inputs so it is testable without a
project or an MCP manager; ``run_preflight`` gathers the live inputs.  The
CLI (``cli_card_runner.run_card``) and the launch endpoint both call it
before the first model call.  ``requires.strict`` (or the CLI's
``--strict``) turns the report into a refusal.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Set

from app.utils.logging_utils import logger


@dataclass
class PreflightReport:
    missing_skills: List[str] = field(default_factory=list)
    missing_tools: List[str] = field(default_factory=list)
    missing_mcp_servers: List[str] = field(default_factory=list)
    # Servers configured but not connected (failed to start, quarantined).
    unavailable_mcp_servers: List[str] = field(default_factory=list)
    # Where each requirement came from, for the message: name -> block names.
    requested_by: Dict[str, List[str]] = field(default_factory=dict)
    strict: bool = False

    @property
    def ok(self) -> bool:
        return not (self.missing_skills or self.missing_tools
                    or self.missing_mcp_servers or self.unavailable_mcp_servers)

    @property
    def refuse(self) -> bool:
        return self.strict and not self.ok

    def messages(self) -> List[str]:
        out = []

        def _by(name: str) -> str:
            who = self.requested_by.get(name) or []
            return f" (needed by {', '.join(who)})" if who else ""

        for s in self.missing_skills:
            out.append(f"skill {s!r} not found in this project's stored skills or "
                       f"discovery roots (.ziya/skills, .agents/skills){_by(s)}")
        for t in self.missing_tools:
            out.append(f"tool {t!r} is not provided by any loaded tool or MCP server{_by(t)}")
        for m in self.missing_mcp_servers:
            out.append(f"MCP server {m!r} is not configured on this host{_by(m)}")
        for m in self.unavailable_mcp_servers:
            out.append(f"MCP server {m!r} is configured but not connected{_by(m)}")
        return out


def _iter_scopes(card) -> Iterable[tuple]:
    """``(label, scope)`` for the card scope and every block scope."""
    if getattr(card, "scope", None) is not None:
        yield ("card", card.scope)

    def walk(block):
        sc = getattr(block, "scope", None)
        if sc is not None:
            yield (getattr(block, "name", None) or getattr(block, "id", None) or "block", sc)
        for child in (getattr(block, "body", None) or []):
            yield from walk(child)

    yield from walk(card.root)


def collect_requirements(card) -> Dict[str, Dict[str, List[str]]]:
    """``{"skills": {name: [requesters]}, "tools": {...}, "mcp_servers": {...}}``.

    Union of the card's ``requires`` block and every scope in the tree.
    Scope entries are what the executor will actually try to resolve, so
    they are requirements whether or not the author listed them again.
    """
    out: Dict[str, Dict[str, List[str]]] = {"skills": {}, "tools": {}, "mcp_servers": {}}

    def add(kind: str, name: str, who: str) -> None:
        name = (name or "").strip()
        if not name:
            return
        out[kind].setdefault(name, [])
        if who not in out[kind][name]:
            out[kind][name].append(who)

    req = getattr(card, "requires", None)
    if req is not None:
        for s in req.skills or []:
            add("skills", s, "requires")
        for t in req.tools or []:
            add("tools", t, "requires")
        for m in req.mcp_servers or []:
            add("mcp_servers", m, "requires")
    for label, sc in _iter_scopes(card):
        for s in getattr(sc, "skills", None) or []:
            add("skills", s, label)
        for t in getattr(sc, "tools", None) or []:
            add("tools", t, label)
    return out


def check_requirements(card, *,
                       available_skills: Iterable[str],
                       available_tools: Iterable[str],
                       mcp_status: Dict[str, Dict[str, Any]],
                       strict: Optional[bool] = None) -> PreflightReport:
    """Pure check.  ``available_skills`` holds ids and names (either
    matches, case-insensitively, as the executor resolves them);
    ``available_tools`` holds tool names; ``mcp_status`` is the shape of
    ``MCPManager.get_server_status()``.
    """
    from app.utils.task_tool_floor import (
        always_available_tools, normalize_tool_name, unmatched_scope_tools,
    )

    wanted = collect_requirements(card)
    req = getattr(card, "requires", None)
    report = PreflightReport(
        strict=bool(req.strict) if (strict is None and req is not None) else bool(strict))

    # Same matcher the executor resolves scope.skills with
    # (task_executor._find_skill_by_name): a preflight that said "present"
    # for a name the run then failed to load, or vice versa, is worse than
    # no preflight.
    from app.utils.skill_match import any_skill_matches
    avail = [s for s in available_skills if s]
    for name, who in wanted["skills"].items():
        if not any_skill_matches(name, avail):
            report.missing_skills.append(name)
            report.requested_by[name] = who

    class _T:  # unmatched_scope_tools wants objects with .name
        def __init__(self, n): self.name = n
    tool_objs = [_T(n) for n in available_tools if n]
    # Floor tools (emit_artifact, render_diagram, beads) are always present
    # for a task; a scope naming one is not asking for anything.
    floor = {normalize_tool_name(n) for n in always_available_tools()}
    for name in unmatched_scope_tools(tool_objs, list(wanted["tools"])):
        if normalize_tool_name(name) in floor:
            continue
        report.missing_tools.append(name)
        report.requested_by[name] = wanted["tools"][name]

    for name, who in wanted["mcp_servers"].items():
        st = mcp_status.get(name)
        if st is None:
            report.missing_mcp_servers.append(name)
            report.requested_by[name] = who
        elif not st.get("connected") or st.get("quarantined"):
            report.unavailable_mcp_servers.append(name)
            report.requested_by[name] = who
    return report


def _live_skill_names(project_id: Optional[str], project_root: Optional[str]) -> List[str]:
    """Ids and names of every skill the executor could resolve, via the same
    storage it uses (stored skills + ``workspace_path`` discovery)."""
    if not project_id:
        return []
    try:
        from app.storage.skills import SkillStorage
        from app.services.token_service import TokenService
        from app.utils.paths import get_project_dir
        storage = SkillStorage(get_project_dir(project_id), TokenService(),
                               workspace_path=project_root or None)
        names: List[str] = []
        for s in storage.list():
            names.append(s.id)
            if getattr(s, "name", None):
                names.append(s.name)
        return names
    except Exception as e:  # noqa: BLE001 — a storage fault is reported, not raised
        logger.debug(f"preflight: skill listing failed: {e}")
        return []


def _live_tool_names() -> List[str]:
    try:
        from app.mcp.enhanced_tools import create_secure_mcp_tools
        return [getattr(t, "name", "") for t in create_secure_mcp_tools()]
    except Exception as e:  # noqa: BLE001
        logger.debug(f"preflight: tool listing failed: {e}")
        return []


def _live_mcp_status() -> Dict[str, Dict[str, Any]]:
    try:
        from app.mcp.manager import get_mcp_manager
        return get_mcp_manager().get_server_status() or {}
    except Exception as e:  # noqa: BLE001
        logger.debug(f"preflight: MCP status unavailable: {e}")
        return {}


def run_preflight(card, *, project_id: Optional[str], project_root: Optional[str],
                  strict: Optional[bool] = None) -> PreflightReport:
    """Gather the live environment and check the card against it."""
    wanted = collect_requirements(card)
    if not any(wanted.values()):
        req = getattr(card, "requires", None)
        return PreflightReport(strict=bool(req.strict) if (strict is None and req) else bool(strict))
    return check_requirements(
        card,
        available_skills=_live_skill_names(project_id, project_root) if wanted["skills"] else [],
        available_tools=_live_tool_names() if wanted["tools"] else [],
        mcp_status=_live_mcp_status() if wanted["mcp_servers"] else {},
        strict=strict,
    )
