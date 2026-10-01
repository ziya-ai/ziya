"""
Per-project tool modes (design/git-tool.md sequencing step 5).

A project can pin a consent-capable tool to one of three modes:

    disabled    tool not advertised for this project; a call is refused
    gated       every call the tool declares ``ask`` waits for a human
    autonomous  runs unattended (consent mode ``always``); the tool's own
                structural refusals are unchanged

Absent = inherit (global builtin-category toggle + permissions.json +
the tool's declared defaults).

Stored at ``project.json -> settings.toolModes`` as ``{tool_name: mode}``.
Not signed (decided 2026-09-25).  Known and accepted: the agent can reach the
local API from the shell (curl / urllib to loopback), so it can change this
setting or answer its own consent request.  Gated mode protects against a
model that makes mistakes, not one working against you; see
Docs/ThreatModel.md §6.

A project mode also controls ADVERTISING: ``gated``/``autonomous`` make a
tool available in that project even when its builtin category is off
globally, and ``disabled`` removes it even when the category is on.  Only
tools listed in ``ENABLEABLE`` can be added this way.
"""

from __future__ import annotations

import time
from typing import Any, Callable, Dict, List, Optional, Tuple

from app.utils.logging_utils import logger

DISABLED = "disabled"
GATED = "gated"
AUTONOMOUS = "autonomous"
MODES = (DISABLED, GATED, AUTONOMOUS)

# project mode -> consent_gate mode
CONSENT_MODE = {GATED: "ask", AUTONOMOUS: "always"}

_TTL_SECONDS = 2.0
_cache: Dict[str, Tuple[float, Dict[str, str]]] = {}


def _git_tools() -> List[Any]:
    from app.mcp.builtin_tools import get_git_tools
    return [cls() for cls in get_git_tools()]


# Tools a project may turn on even when the global category is off.
ENABLEABLE: Dict[str, Callable[[], List[Any]]] = {"git": _git_tools}


def _read_modes(project_root: str) -> Dict[str, str]:
    from app.storage.projects import ProjectStorage
    from app.utils.paths import get_ziya_home
    project = ProjectStorage(get_ziya_home()).get_by_path(project_root)
    if project is None:
        return {}
    raw = getattr(project.settings, "toolModes", None) or {}
    return {str(k): v for k, v in dict(raw).items() if v in MODES}


def modes_for(project_root: Optional[str]) -> Dict[str, str]:
    """``{tool: mode}`` for the project at ``project_root``.  Never raises.

    Cached for a couple of seconds: the tool loader and the gate both ask
    on every turn / call, and a settings change taking effect within 2 s is
    fine.
    """
    if not project_root:
        return {}
    now = time.monotonic()
    hit = _cache.get(project_root)
    if hit and now - hit[0] < _TTL_SECONDS:
        return hit[1]
    try:
        modes = _read_modes(project_root)
    except Exception as exc:  # noqa: BLE001 — never fail a turn on settings
        logger.debug(f"project tool modes unreadable for {project_root}: {exc}")
        modes = {}
    _cache[project_root] = (now, modes)
    return modes


def mode_for(tool_name: str, project_root: Optional[str]) -> Optional[str]:
    return modes_for(project_root).get(tool_name)


def invalidate(project_root: Optional[str] = None) -> None:
    if project_root is None:
        _cache.clear()
    else:
        _cache.pop(project_root, None)


def apply_to_tools(
    tools: List[Any], project_root: Optional[str], *, wrap: Callable[[Any], Any],
) -> Tuple[List[Any], List[str], List[str]]:
    """Remove ``disabled`` tools; add ``gated``/``autonomous`` enableable ones.

    ``wrap`` turns a raw BaseMCPTool into whatever the caller's list holds
    (``DirectMCPTool``).  Returns ``(tools, added, removed)``.  Never raises;
    on error the input list is returned unchanged.
    """
    modes = modes_for(project_root)
    if not modes:
        return tools, [], []
    try:
        removed = [t.name for t in tools if modes.get(getattr(t, "name", None)) == DISABLED]
        out = [t for t in tools if modes.get(getattr(t, "name", None)) != DISABLED]
        present = {getattr(t, "name", None) for t in out}
        added: List[str] = []
        for name, mode in modes.items():
            if mode in (GATED, AUTONOMOUS) and name not in present and name in ENABLEABLE:
                for inst in ENABLEABLE[name]():
                    if inst.name == name:
                        out.append(wrap(inst))
                        added.append(name)
        return out, added, removed
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"project tool modes not applied: {exc}")
        return tools, [], []
