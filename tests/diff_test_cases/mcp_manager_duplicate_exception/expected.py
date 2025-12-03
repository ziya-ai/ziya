"""
MCP Manager for handling multiple MCP servers and integrating with Ziya.
"""

import asyncio
import os
import sys
import json
import time
from typing import Dict, List, Optional, Any
from pathlib import Path

from app.mcp.client import MCPClient, MCPResource, MCPTool, MCPPrompt # Assuming MCPClient is in the same directory or sys.path is configured
from app.utils.logging_utils import logger
from app.mcp.dynamic_tools import get_dynamic_loader


class MCPManager:
    """
    Manager for MCP servers and their integration with Ziya.
    
    This class handles:
    - Loading MCP server configurations
    - Managing connections to multiple MCP servers
    - Providing unified access to MCP resources, tools, and prompts
    - Integrating MCP capabilities with Ziya's agent system
    """

    def __init__(self, config_path: Optional[str] = None):
        """
        Initialize the MCP manager.
        
        Args:
            config_path: Path to MCP configuration file
        """
        self.config_path = config_path or self._find_config_file()
        self.clients: Dict[str, MCPClient] = {}
        self.config_search_paths: List[str] = []
        self.builtin_server_definitions = self._get_builtin_server_definitions()
        self.is_initialized = False
        
        # Tool caching to eliminate redundant get_all_tools calls
        self._tools_cache: Optional[List[MCPTool]] = None
        self._tools_cache_timestamp: float = 0
        self._tools_cache_ttl: float = 300  # 5 minutes cache TTL
        self._reconnection_attempts: Dict[str, float] = {}  # Track last reconnection attempt per server
        self._failed_servers: set = set()  # Servers that have failed too many times
        
        # Loop detection for repetitive tool calls
        self._recent_tool_calls: Dict[str, List[tuple]] = {}  # conversation_id -> [(tool_name, arguments, timestamp)]
        self._max_recent_calls = 10
        self._loop_detection_window = 60  # seconds - increased for conversation-aware tracking
    
    def _get_builtin_server_definitions(self) -> Dict[str, Dict[str, Any]]:
        """Defines configurations for built-in MCP servers."""
        builtin_servers = {}
        try:
            # Find the path to the app.mcp_servers package
            import app.mcp_servers
            package_dir = Path(app.mcp_servers.__file__).parent

            builtin_servers["time"] = {
                "command": [sys.executable, "-u", str(package_dir / "time_server.py")],
                "enabled": True,
                "description": "Provides current time functionality",
                "builtin": True
            }
            builtin_servers["shell"] = {
                "command": [sys.executable, "-u", str(package_dir / "shell_server.py")],
                "enabled": True,
                "description": "Provides shell command execution",
                "builtin": True
            }
            logger.info(f"Found built-in MCP server package at: {package_dir}")
        except ImportError:
            logger.error("Built-in MCP server package 'app.mcp_servers' not found. Built-in servers will be unavailable.")
        except Exception as e:
            logger.error(f"Error defining built-in MCP servers: {e}")
        return builtin_servers
