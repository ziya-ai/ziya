"""
API routes for managing builtin MCP tools.
"""

import os
from typing import Dict, Any
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.utils.logging_utils import logger
from app.mcp.builtin_tools import (
    BUILTIN_TOOL_CATEGORIES,
    is_builtin_category_enabled,
    get_builtin_tools_for_category,
    check_pcap_dependencies
)

router = APIRouter(prefix="/builtin-tools", tags=["builtin-tools"])


class BuiltinToolToggleRequest(BaseModel):
    model_config = {"extra": "allow"}
    """Request model for toggling builtin tools."""
    category: str
    enabled: bool


@router.get("/status")
async def get_builtin_tools_status():
    """Get status of all builtin tool categories."""
    try:
        categories = {}
        
        for category, config in BUILTIN_TOOL_CATEGORIES.items():
            # Skip hidden categories
            if config.get("hidden", False):
                continue
                
            # Check if dependencies are available
            dependencies_available = True
            if category == "pcap_analysis":
                dependencies_available = check_pcap_dependencies()
            
            # Get available tools for this category
            available_tool_classes = get_builtin_tools_for_category(category)
            
            categories[category] = {
                "name": config["name"],
                "description": config["description"],
                "enabled": is_builtin_category_enabled(category),
                "enabled_by_default": config["enabled_by_default"],
                "dependencies_available": dependencies_available,
                "requires_dependencies": config.get("requires_dependencies", []),
                "available_tools": [tool_class().name for tool_class in available_tool_classes],
                "tool_count": len(available_tool_classes)
            }
        
        return {
            "success": True,
            "categories": categories
        }
        
    except Exception as e:
        logger.error(f"Error getting builtin tools status: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/toggle")
async def toggle_builtin_tool_category(request: BuiltinToolToggleRequest):
    """Enable or disable a builtin tool category."""
    try:
        if request.category not in BUILTIN_TOOL_CATEGORIES:
            raise HTTPException(status_code=400, detail=f"Unknown builtin tool category: {request.category}")
        
        # Set environment variable to persist the setting
        env_var = f"ZIYA_ENABLE_{request.category.upper()}"
        os.environ[env_var] = "true" if request.enabled else "false"
        
        # TODO: Optionally persist to config file for permanent storage
        
        # Clear the MCP tools cache to force reload
        try:
            from app.mcp.enhanced_tools import _secure_tool_cache, _tool_cache_timestamp
            # Clear cache by setting global variables (not ideal but works)
            import app.mcp.enhanced_tools
            app.mcp.enhanced_tools._secure_tool_cache = None
            app.mcp.enhanced_tools._tool_cache_timestamp = 0
        except ImportError:
            pass
        
        action = "enabled" if request.enabled else "disabled"
        logger.info(f"Builtin tool category '{request.category}' {action}")
        
        return {
            "success": True,
            "message": f"Builtin tool category '{request.category}' {action}",
            "category": request.category,
            "enabled": request.enabled
        }
        
    except Exception as e:
        logger.error(f"Error toggling builtin tool category: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/dependencies/{category}")
async def check_category_dependencies(category: str):
    """Check if dependencies for a builtin tool category are satisfied."""
    try:
        if category not in BUILTIN_TOOL_CATEGORIES:
            raise HTTPException(status_code=400, detail=f"Unknown builtin tool category: {category}")
        
        dependencies_met = True
        missing_dependencies = []
        
        if category == "pcap_analysis":
            dependencies_met = check_pcap_dependencies()
            if not dependencies_met:
                missing_dependencies = ["scapy", "dpkt", "netaddr"]
        
        return {
            "success": True,
            "category": category,
            "dependencies_met": dependencies_met,
            "missing_dependencies": missing_dependencies,
            "install_command": f"pip install {' '.join(missing_dependencies)}" if missing_dependencies else None
        }
        
    except Exception as e:
        logger.error(f"Error checking dependencies: {e}")
        raise HTTPException(status_code=500, detail=str(e))
