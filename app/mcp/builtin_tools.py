"""
Registry for builtin MCP tools that run directly without external servers.

This module provides a centralized registry for optional builtin tools
that can be enabled/disabled by users without requiring external MCP servers.
"""

import os
from typing import Dict, List, Type, Optional
from app.utils.logging_utils import logger
from app.mcp.tools.base import BaseMCPTool


# Registry of available builtin tool categories
BUILTIN_TOOL_CATEGORIES: Dict[str, Dict[str, any]] = {
    "pcap_analysis": {
        "name": "PCAP Analysis",
        "description": "Network packet capture analysis and protocol correlation tools",
        "enabled_by_default": False,
        "requires_dependencies": ["scapy", "dpkt"],
        "tools": [],  # Will be populated dynamically
        "hidden": True  # Hidden for release - not ready yet
    },
    "architecture_shapes": {
        "name": "Architecture Shapes",
        "description": "Architecture diagram shape catalog for DrawIO, Mermaid, and Graphviz",
        "enabled_by_default": True,
        "requires_dependencies": [],
        "tools": [],
    },
    "fileio": {
        "name": "File I/O",
        "description": "Read, write, and list files for agentic state tracking and design doc maintenance",
        "enabled_by_default": True,
        "requires_dependencies": [],
        "tools": [],
    },
    "nova_grounding": {
        "name": "Nova Web Search",
        "description": "Web search via Amazon Nova grounding — no external MCP server needed",
        "enabled_by_default": True,
        "requires_dependencies": [],
        "tools": [],
    },
}


def check_pcap_dependencies() -> bool:
    """Check if PCAP analysis dependencies are available."""
    try:
        import scapy.all
        return True
    except ImportError:
        logger.debug("PCAP analysis dependencies not available (scapy not installed)")
        return False


def get_pcap_analysis_tools() -> List[Type[BaseMCPTool]]:
    """Get PCAP analysis tools if dependencies are available."""
    if not check_pcap_dependencies():
        return []
    
    try:
        from app.mcp.tools.pcap_analysis import PCAPAnalysisTool, ListPCAPFilesTool
        return [PCAPAnalysisTool, ListPCAPFilesTool]
    except ImportError as e:
        logger.warning(f"Could not import PCAP analysis tools: {e}")
        return []


def get_architecture_shapes_tools() -> List[Type[BaseMCPTool]]:
    """Get architecture shapes catalog tools."""
    try:
        from app.mcp.tools.architecture_shapes.tools import (
            ListShapeCategoriesTool, SearchShapesTool, GetDiagramTemplateTool
        )
        return [ListShapeCategoriesTool, SearchShapesTool, GetDiagramTemplateTool]
    except ImportError as e:
        logger.warning(f"Could not import architecture shapes tools: {e}")
        return []


def get_fileio_tools() -> List[Type[BaseMCPTool]]:
    """Get file I/O tools for agentic state tracking."""
    try:
        from app.mcp.tools.fileio import (
            FileReadTool, FileWriteTool, FileListTool
        )
        return [FileReadTool, FileWriteTool, FileListTool]
    except ImportError as e:
        logger.warning(f"Could not import fileio tools: {e}")
        return []


def get_nova_grounding_tools() -> List[Type[BaseMCPTool]]:
    """Get Nova Web Search grounding tools."""
    try:
        from app.mcp.tools.nova_grounding import NovaWebSearchTool
        return [NovaWebSearchTool]
    except ImportError as e:
        logger.warning(f"Could not import Nova grounding tools: {e}")
        return []


def get_builtin_tools_for_category(category: str) -> List[Type[BaseMCPTool]]:
    """Get builtin tools for a specific category."""
    tool_getters = {
        "pcap_analysis": get_pcap_analysis_tools,
        "architecture_shapes": get_architecture_shapes_tools,
        "fileio": get_fileio_tools,
        "nova_grounding": get_nova_grounding_tools,
    }

    getter = tool_getters.get(category)
    if getter:
        return getter()
    return []


def is_builtin_category_enabled(category: str) -> bool:
    """Check if a builtin tool category is enabled."""
    if category not in BUILTIN_TOOL_CATEGORIES:
        return False
    
    # Check environment variable first
    env_var = f"ZIYA_ENABLE_{category.upper()}"
    env_value = os.environ.get(env_var)
    if env_value is not None:
        return env_value.lower() in ("true", "1", "yes")
    
    # Check if a service model plugin has enabled this category
    try:
        from app.plugins import get_enabled_service_tool_categories
        plugin_enabled = get_enabled_service_tool_categories()
        if category in plugin_enabled:
            return True
    except Exception:
        pass

    # Fall back to default setting
    return BUILTIN_TOOL_CATEGORIES[category]["enabled_by_default"]


def get_enabled_builtin_tools() -> List[BaseMCPTool]:
    """Get all enabled builtin tools as instances."""
    enabled_tools = []
    
    for category, config in BUILTIN_TOOL_CATEGORIES.items():
        if is_builtin_category_enabled(category):
            tool_classes = get_builtin_tools_for_category(category)
            for tool_class in tool_classes:
                try:
                    enabled_tools.append(tool_class())
                    logger.debug(f"Enabled builtin tool: {tool_class().name}")
                except Exception as e:
                    logger.warning(f"Failed to initialize builtin tool {tool_class}: {e}")
    
    if enabled_tools:
        logger.debug(f"Loaded {len(enabled_tools)} enabled builtin tools")
    
    return enabled_tools
