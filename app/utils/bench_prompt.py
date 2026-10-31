"""
Bench -> prompt seam (design/capabilities-hub.md, slice 3).

The chat request identifies its project by ``project_root`` (a path), not an
id; ``_inject_task_results`` in app/server.py resolves it the same way.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from app.utils.logging_utils import logger


def project_id_for_root(project_root: Optional[str]) -> Optional[str]:
    if not project_root:
        return None
    try:
        from app.storage.projects import ProjectStorage
        from app.utils.paths import get_ziya_home
        project = ProjectStorage(get_ziya_home()).get_by_path(project_root)
        return project.id if project else None
    except Exception as e:  # noqa: BLE001 -- prompt assembly must never fail a turn
        logger.debug("bench_prompt: project lookup failed for %s: %s", project_root, e)
        return None


def resolve_bench_prompt(project_root: Optional[str], conversation_id: Optional[str]):
    """PromptInputs for this request, or None when no project resolves."""
    pid = project_id_for_root(project_root)
    if not pid:
        return None
    try:
        from app.services.bench import prompt_inputs
        return prompt_inputs(pid, conversation_id)
    except Exception as e:  # noqa: BLE001
        logger.warning("bench_prompt: resolution failed, using client prompt: %s", e)
        return None


def merge_bench_prompt(
    bench, client_addition: str, client_overrides: Dict[str, Any], client_tool_ids: List[str],
) -> Tuple[str, Dict[str, Any], List[str], Optional[set]]:
    """Combine server-resolved and client-sent skill effects.

    Returns (system_prompt_addition, model_overrides, preferred_tool_ids,
    catalog_names).  Rules, one release only (removed with slice 4's
    migration):

      * no project / bench unresolvable -> client values, catalog unfiltered;
      * bench has NO skill placement at any layer -> not adopted yet; client
        prompt text kept so today's localStorage activations keep working,
        overrides/tool ids taken from the bench only when the client sent
        none (it never does today);
      * bench authoritative -> server prompt text wins; the catalog is
        filtered to the bench's ondemand set, so a skill placed ``off`` is
        offered nowhere.
    """
    if bench is None:
        return client_addition, client_overrides, client_tool_ids, None
    overrides = client_overrides if client_overrides else dict(bench.model_overrides)
    tool_ids = client_tool_ids if client_tool_ids else list(bench.preferred_tool_ids)
    if not bench.authoritative:
        return client_addition, overrides, tool_ids, None
    if client_addition and client_addition.strip() != bench.system_prompt_addition.strip():
        logger.debug("bench_prompt: client systemPromptAddition superseded by bench")
    return bench.system_prompt_addition, overrides, tool_ids, set(bench.catalog_names)


def mcp_placements_for_root(project_root: Optional[str], conversation_id: Optional[str]) -> Dict[str, str]:
    """``{server: always|ondemand|off}`` for placeable MCP servers; ``{}`` when unknown.

    Fails open: a bench error must never strip tools from, or fail, a turn.
    """
    pid = project_id_for_root(project_root)
    if not pid:
        return {}
    try:
        from app.services.bench import mcp_effective
        return mcp_effective(pid, conversation_id)
    except Exception as e:  # noqa: BLE001
        logger.warning("bench_prompt: MCP placement lookup failed, tools unfiltered: %s", e)
        return {}


def servers_named_by_scope(tools: Optional[List[Any]], tool_allowlist) -> set:
    """Servers a Task Card's ``scope.tools`` names a tool of.

    Such a server counts as loaded for that run: otherwise a within-reach
    server's tool named in the scope would be withheld before the scope
    filter ever saw it (design/mcp-on-demand.md, open question 1).
    """
    if not tools or not tool_allowlist:
        return set()
    from app.utils.task_tool_floor import normalize_tool_name
    wanted = {normalize_tool_name(n) for n in tool_allowlist}
    return {s for t in tools
            if (s := _tool_server(t)) and normalize_tool_name(getattr(t, "name", "")) in wanted}


def mcp_filter_for_root(project_root: Optional[str], conversation_id: Optional[str], *,
                        tools=None, tool_allowlist=None):
    """The request's MCP tool filter (app.utils.mcp_ondemand.McpFilter).

    Shelved servers are withheld; within-reach servers are withheld except
    for what the conversation has LOADED (the chat record's ``loadedMcp``
    plus this process's sticky copy) or what the task scope names.  An empty
    filter when unknown, so a bench error never strips tools.
    """
    from app.utils.mcp_ondemand import McpFilter, loaded_map, mcp_filter
    placements = mcp_placements_for_root(project_root, conversation_id)
    if not placements:
        return McpFilter()
    loaded = loaded_map(project_id_for_root(project_root), conversation_id)
    return mcp_filter(placements, loaded, servers_named_by_scope(tools, tool_allowlist))


def withheld_mcp_for_root(project_root: Optional[str], conversation_id: Optional[str], *,
                          tools=None, tool_allowlist=None) -> set:
    """Servers with NO tools in this request (whole-server view of the filter)."""
    return mcp_filter_for_root(project_root, conversation_id,
                               tools=tools, tool_allowlist=tool_allowlist).withheld


def apply_mcp_filter(tools: List[Any], filt) -> Tuple[List[Any], int]:
    """Drop what the filter withholds, then order: builtins, on-hand servers
    by name, loaded within-reach servers in load order (a load appends).
    Returns the new list and how many tools were dropped."""
    from app.utils.mcp_ondemand import order_tools
    kept = [t for t in tools if filt.allows(_tool_server(t), getattr(t, "name", ""))]
    return order_tools(kept, _tool_server, filt.order), len(tools) - len(kept)


def _tool_server(tool) -> Optional[str]:
    md = getattr(tool, "metadata", None)
    if isinstance(md, dict) and md.get("server_name"):
        return md["server_name"]
    return getattr(tool, "server_name", None)


def drop_withheld_tools(tools: List[Any], withheld: set) -> Tuple[List[Any], int]:
    """``tools`` minus those from a withheld server, and how many were dropped."""
    if not withheld:
        return list(tools), 0
    kept = [t for t in tools if _tool_server(t) not in withheld]
    return kept, len(tools) - len(kept)
