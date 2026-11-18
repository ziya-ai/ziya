"""Tool definitions a request for one project + conversation carries in the
model's context, for the token panel's "tools" figure
(POST /api/context-estimate).

The estimate used to price every tool of every connected server.  A shelved
server sends nothing, a within-reach server sends only what the conversation
has loaded, and on a model with native tool search a within-reach server is
sent deferred (held out of context by the provider), so that figure was the
weight of everything installed.

Placement comes from the same ``McpFilter`` the request path builds
(``bench_prompt.mcp_filter_for_root``) and the same deferral gate
(``mcp_ondemand.native_search_for``).  Tools disabled in permissions are
left out, as the MCP status route does.  Not mirrored: task-scope
allowlists, the agent overlay and session gating, which depend on the turn.
On the native path the tools the model has since found are the provider's
state and are not known here.

Everything fails open: with no project or a bench error, all tools count.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence, Tuple

from app.utils.logging_utils import logger

# (server name or None for a tool with no server, definition)
Row = Tuple[Optional[str], Dict[str, Any]]


def _permitted(permissions: Dict[str, Any], server: str, tool: str) -> bool:
    default = (permissions.get("defaults") or {}).get("tool", "enabled")
    entry = (((permissions.get("servers") or {}).get(server) or {}).get("tools") or {}).get(tool) or {}
    return entry.get("permission", default) == "enabled"


def mcp_rows(mgr=None) -> List[Row]:
    """Enabled tools of connected MCP servers, with the server each belongs to."""
    if mgr is None:
        from app.mcp.manager import get_mcp_manager
        mgr = get_mcp_manager()
    if not getattr(mgr, "is_initialized", False):
        return []
    try:
        from app.mcp.permissions import get_permissions_manager
        permissions = get_permissions_manager().get_permissions() or {}
    except Exception as e:  # noqa: BLE001
        logger.debug(f"request_tool_defs: permissions unavailable: {e}")
        permissions = {}
    rows: List[Row] = []
    for t in mgr.get_all_tools():
        server = getattr(t, "_server_name", None)
        if server and not _permitted(permissions, server, t.name):
            continue
        rows.append((server, {"name": t.name, "description": t.description,
                              "input_schema": t.inputSchema}))
    return rows


def builtin_defs() -> List[Dict[str, Any]]:
    from app.mcp.builtin_tools import get_enabled_builtin_tools
    out: List[Dict[str, Any]] = []
    for t in get_enabled_builtin_tools():
        schema_cls = getattr(t, "InputSchema", None)
        schema = schema_cls.model_json_schema() if hasattr(schema_cls, "model_json_schema") else {}
        out.append({"name": t.name, "description": getattr(t, "description", "") or "",
                    "input_schema": schema})
    return out


def in_context(rows: Sequence[Row], filt: Any, deferral: bool) -> List[Dict[str, Any]]:
    """The definitions of ``rows`` that occupy context.  Pure.

    ``filt`` is an ``McpFilter`` or None (no bench information: keep all).
    With ``deferral`` a within-reach server is sent deferred whatever the
    conversation has loaded, as the executor's native payload does.
    """
    if filt is None:
        return [d for _s, d in rows]
    shelved = set(getattr(filt, "shelved", ()) or ())
    reach = set(getattr(filt, "reach", ()) or ())
    out: List[Dict[str, Any]] = []
    for server, d in rows:
        if server is not None:
            if server in shelved or (deferral and server in reach):
                continue
            if not deferral and not filt.allows(server, d.get("name", "")):
                continue
        out.append(d)
    return out


def deferral_for_model(model: Optional[str] = None) -> bool:
    """Whether ``model`` (an alias; None = the current model) sends
    within-reach tools deferred."""
    from app.utils.mcp_token_breakdown import _as_str, deferral_for_current_model
    if not model:
        return deferral_for_current_model()
    try:
        from app.agents.models import ModelManager
        from app.config.env_registry import ziya_env
        from app.utils.mcp_ondemand import native_search_for
        endpoint = ziya_env("ZIYA_ENDPOINT")
        cfg = ModelManager.get_model_config(endpoint, model) or {}
        return bool(native_search_for(endpoint, cfg, _as_str(cfg.get("model_id"))))
    except Exception as e:  # noqa: BLE001
        logger.debug(f"request_tool_defs: deferral unknown for {model!r}: {e}")
        return deferral_for_current_model()


def request_tool_definitions(
    project_root: Optional[str], conversation_id: Optional[str], *, model: Optional[str] = None,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], List[Dict[str, Any]]]:
    """``(in-context MCP defs, in-context builtin defs, all enabled MCP defs)``.

    The third list lets the caller price a tool that was in context when the
    overhead baseline was measured and is not now.  Never raises.
    """
    rows: List[Row] = []
    builtins: List[Dict[str, Any]] = []
    try:
        rows = mcp_rows()
    except Exception as e:  # noqa: BLE001
        logger.debug(f"context-estimate: MCP tools unavailable: {e}")
    try:
        builtins = builtin_defs()
    except Exception as e:  # noqa: BLE001
        logger.debug(f"context-estimate: builtin tools unavailable: {e}")

    filt, deferral = None, False
    try:
        from app.utils import bench_prompt
        if bench_prompt.project_id_for_root(project_root):
            filt = bench_prompt.mcp_filter_for_root(project_root, conversation_id)
            deferral = bool(deferral_for_model(model))
    except Exception as e:  # noqa: BLE001 -- the gauge degrades to counting everything
        logger.warning("request_tool_defs: bench unavailable, counting all tools: %s", e)
        filt, deferral = None, False

    if deferral and filt is not None and getattr(filt, "reach", None):
        # The provider's search replaces Ziya's loader and search builtins.
        from app.utils.mcp_ondemand import LOADER_TOOL, SEARCH_TOOL
        from app.utils.task_tool_floor import normalize_tool_name
        builtins = [d for d in builtins
                    if normalize_tool_name(d.get("name", "")) not in (LOADER_TOOL, SEARCH_TOOL)]
    return in_context(rows, filt, deferral), builtins, [d for _s, d in rows]
