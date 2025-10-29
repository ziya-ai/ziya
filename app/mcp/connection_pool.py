"""
Connection pool for MCP servers.

This module provides a connection pool for MCP servers to:
1. Manage connections to multiple MCP servers
2. Provide a unified interface for calling tools
3. Handle connection errors and retries
4. Apply rate limiting to prevent throttling
"""

import time
import asyncio
from typing import Dict, Any, Optional

from app.utils.logging_utils import logger

class ConnectionPool:
    """Pool for managing connections to MCP servers."""
    
    def __init__(self):
        """Initialize the connection pool."""
        self.server_configs = {}
        self.last_call_time = {}
        self.min_call_interval = 0.5  # Minimum interval between calls in seconds
    
    def set_server_configs(self, configs: Dict[str, Any]):
        """Set server configurations."""
        self.server_configs = configs
    
    async def call_tool(self, conversation_id: str, tool_name: str, arguments: Dict[str, Any], server_name: Optional[str] = None) -> Any:
        """
        Call a tool via the appropriate MCP server.
        
        Args:
            conversation_id: The conversation ID
            tool_name: The tool name
            arguments: The tool arguments
            server_name: Optional server name to use
            
        Returns:
            Tool execution result
        """
        # Add debug logging
        logger.info(f"🔌 CONNECTION_POOL: Calling tool {tool_name} with arguments {arguments}")
        print(f"🔌 CONNECTION_POOL: Calling tool {tool_name} with arguments {arguments}")
        
        # Special handling for shell commands
        if tool_name == "run_shell_command" or tool_name == "mcp_run_shell_command":
            logger.info(f"🔌 CONNECTION_POOL: Detected shell command: {arguments.get('command', '')}")
            print(f"🔌 CONNECTION_POOL: Detected shell command: {arguments.get('command', '')}")
            # Force server_name to "shell" for shell commands
            server_name = "shell"
            logger.info(f"🔌 CONNECTION_POOL: Forcing server_name to 'shell' for shell command")
            print(f"🔌 CONNECTION_POOL: Forcing server_name to 'shell' for shell command")
        
        # Apply rate limiting
        tool_key = f"{tool_name}:{conversation_id}"
        if tool_key in self.last_call_time:
            elapsed = time.time() - self.last_call_time[tool_key]
            if elapsed < self.min_call_interval:
                await asyncio.sleep(self.min_call_interval - elapsed)
        
        # Update last call time
        self.last_call_time[tool_key] = time.time()
        
        # Import MCP manager
        from app.mcp.manager import get_mcp_manager
        mcp_manager = get_mcp_manager()
        
        if not mcp_manager.is_initialized:
            logger.error("🔌 CONNECTION_POOL: MCP manager not initialized")
        
        # Call the tool via MCP manager
        try:
            logger.info(f"🔌 CONNECTION_POOL: Calling MCP manager with tool_name={tool_name}, server_name={server_name}")
            print(f"🔌 CONNECTION_POOL: Calling MCP manager with tool_name={tool_name}, server_name={server_name}")
            result = await mcp_manager.call_tool(tool_name, arguments, server_name)
            logger.info(f"🔌 CONNECTION_POOL: Call successful, result type: {type(result)}")
            print(f"🔌 CONNECTION_POOL: Call successful, result type: {type(result)}")
            return result
        except Exception as e:
            logger.error(f"🔌 CONNECTION_POOL: Error calling tool {tool_name}: {e}")
            print(f"🔌 CONNECTION_POOL: Error calling tool {tool_name}: {e}")
            raise e

# Global connection pool instance
_connection_pool = None

def get_connection_pool() -> ConnectionPool:
    """Get the global connection pool instance."""
    global _connection_pool
    if _connection_pool is None:
        _connection_pool = ConnectionPool()
    return _connection_pool
