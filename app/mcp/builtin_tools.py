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
    }
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


def get_builtin_tools_for_category(category: str) -> List[Type[BaseMCPTool]]:
    """Get builtin tools for a specific category."""
    if category == "pcap_analysis":
        return get_pcap_analysis_tools()
    elif category == "architecture_shapes":
        return get_architecture_shapes_tools()
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
        logger.info(f"Loaded {len(enabled_tools)} enabled builtin tools")
    
    return enabled_tools
