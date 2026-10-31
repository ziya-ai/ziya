"""
``search_server_tools`` and ``load_server_tools``: find and load a within-reach
MCP server's tools mid-turn (design/mcp-on-demand.md, capabilities-hub slice 9).

Thin wrappers: every check and the result text live in
``app.utils.mcp_ondemand``.  Not named ``load_mcp_server``: the
tool-name normalizer strips ``mcp_`` wherever ``_mcp_`` occurs, which would
turn that name into ``load_server`` at dispatch.
"""
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from app.mcp.tools.base import BaseMCPTool


class LoadServerToolsInput(BaseModel):
    name: str = Field(
        ...,
        description="Name of an MCP server listed under 'MCP servers within reach' in your instructions.",
    )
    tools: Optional[List[str]] = Field(
        None,
        description=(
            "Tool names to load from that server (from search_server_tools). Omit to load "
            "the whole server. Loads are cumulative; each call re-sends the tool list, so "
            "name every tool the task needs in one call."
        ),
    )


class SearchServerToolsInput(BaseModel):
    query: str = Field(
        "",
        description="Words describing the tool you need (matched against tool names and descriptions). "
                    "Empty with a server name lists that server's tools.",
    )
    server: Optional[str] = Field(
        None, description="Restrict to one within-reach server. Omit to search all of them.",
    )


class SearchServerToolsTool(BaseMCPTool):
    """Find tools on within-reach MCP servers without loading anything."""

    name: str = "search_server_tools"
    description: str = (
        "Search the tools of the MCP servers listed under 'MCP servers within reach'. "
        "Loads nothing and costs no tool-list change, so call it freely. Then call "
        "load_server_tools once with every tool the task needs."
    )
    InputSchema = SearchServerToolsInput

    async def execute(self, **kwargs) -> Dict[str, Any]:
        from app.context import get_conversation_id_or_none, get_project_root_or_none
        from app.utils.mcp_ondemand import search_tools
        project_root = kwargs.get("_workspace_path") or get_project_root_or_none()
        return search_tools(kwargs.get("query") or "", kwargs.get("server") or None,
                            project_root, get_conversation_id_or_none())


class LoadServerToolsTool(BaseMCPTool):
    """Make a within-reach MCP server's tools callable for this conversation."""

    name: str = "load_server_tools"
    description: str = (
        "Load tools of an MCP server listed under 'MCP servers within reach' in your "
        "instructions: the named tools, or the whole server when tools is omitted. They "
        "become callable from your next step and stay loaded for the rest of the "
        "conversation. Each call re-sends the tool list, so load all you need at once."
    )
    InputSchema = LoadServerToolsInput

    async def execute(self, **kwargs) -> Dict[str, Any]:
        from app.context import get_conversation_id_or_none, get_project_root_or_none
        from app.utils.mcp_ondemand import load_server
        project_root = kwargs.get("_workspace_path") or get_project_root_or_none()
        return load_server(kwargs.get("name") or "", project_root, get_conversation_id_or_none(),
                           tools=kwargs.get("tools") or None)


__all__ = ["LoadServerToolsTool", "SearchServerToolsTool"]
