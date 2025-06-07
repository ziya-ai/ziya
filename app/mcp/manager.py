"""
MCP Manager for handling multiple MCP servers and integrating with Ziya.
"""

import asyncio
import json
import os
from typing import Dict, List, Optional, Any
from pathlib import Path

from app.mcp.client import MCPClient, MCPResource, MCPTool, MCPPrompt
from app.utils.logging_utils import logger


class MCPManager:
    """
    Manager for MCP servers and their integration with Ziya.
    
    This class handles:
    - Loading MCP server configurations
    - Managing connections to multiple MCP servers
    - Providing unified access to MCP resources, tools, and prompts
    - Integrating MCP capabilities with Ziya's agent system
    """
    
    # Built-in MCP servers that are always available
    BUILTIN_SERVERS = {
        "time-server": {
            "command": ["python", "-u", "mcp_servers/time_server.py"],
            "enabled": True,
            "description": "Provides current time functionality",
            "builtin": True
        },
        "shell": {
            "command": ["python", "-u", "mcp_servers/shell_server.py"],
            "env": {
                "ALLOW_COMMANDS": "ls,cat,pwd,grep,wc,touch,find,date"
            },
            "enabled": True,
            "description": "Provides shell command execution functionality",
            "builtin": True
        }
    }
    
    def __init__(self, config_path: Optional[str] = None):
        """
        Initialize the MCP manager.
        
        Args:
            config_path: Path to MCP configuration file
        """
        self.config_path = config_path or os.path.join(
            os.path.expanduser("~"), ".ziya", "mcp_config.json"
        )
        
        # Check for config in multiple locations
        if not config_path and not os.path.exists(self.config_path):
            # Check current working directory
            cwd_config = os.path.join(os.getcwd(), "mcp_config.json")
            # Check relative to the app directory (parent of app/)
            app_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            app_config = os.path.join(app_dir, "mcp_config.json")
            # Check project root (parent of app directory)
            project_root = os.path.dirname(app_dir)
            root_config = os.path.join(project_root, "mcp_config.json")
            
            if os.path.exists(cwd_config):
                self.config_path = cwd_config
            elif os.path.exists(root_config):
                self.config_path = root_config
            elif os.path.exists(app_config):
                self.config_path = app_config
        
        self.clients: Dict[str, MCPClient] = {}
        self.is_initialized = False
        
    async def initialize(self) -> bool:
        """
        Initialize the MCP manager and connect to configured servers.
        
        Returns:
            bool: True if initialization successful
        """
        try:
            # Load configuration
            config = self._load_config()
            user_servers = config.get("mcpServers", {}) if config else {}
            logger.info(f"Loaded user MCP config from: {self.config_path}")
            
            # Log which paths were checked
            cwd_config = os.path.join(os.getcwd(), "mcp_config.json")
            app_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            app_config = os.path.join(app_dir, "mcp_config.json")
            project_root = os.path.dirname(app_dir)
            root_config = os.path.join(project_root, "mcp_config.json")
            logger.debug(f"Checked config paths: {[self.config_path, cwd_config, root_config, app_config]}")
            
            # Merge built-in servers with user configuration
            # User config can override built-in servers
            merged_servers = self.BUILTIN_SERVERS.copy()
            for server_name, server_config in user_servers.items():
                if server_name in merged_servers:
                    # User is overriding a built-in server
                    merged_config = merged_servers[server_name].copy()
                    merged_config.update(server_config)
                    merged_config["builtin"] = True  # Keep builtin flag
                    merged_servers[server_name] = merged_config
                else:
                    # User is adding a new server
                    server_config["builtin"] = False
                    merged_servers[server_name] = server_config
            
            # Connect to each configured server
            connection_tasks = []
            
            for server_name, server_config in merged_servers.items():
                if not server_config.get("enabled", True):
                    logger.info(f"MCP server {server_name} is disabled, skipping")
                    continue
                    
                client = MCPClient(server_config)
                self.clients[server_name] = client
                connection_tasks.append(self._connect_server(server_name, client))
            
            # Wait for all connections to complete
            if connection_tasks:
                results = await asyncio.gather(*connection_tasks, return_exceptions=True)
                
                # Log connection results
                successful_connections = sum(1 for result in results if result is True)
                builtin_count = sum(1 for config in merged_servers.values() if config.get("builtin", False))
                user_count = len(merged_servers) - builtin_count
                logger.info(f"MCP Manager initialized: {successful_connections}/{len(connection_tasks)} servers connected")
                logger.info(f"Server breakdown: {builtin_count} built-in, {user_count} user-configured")
            
            self.is_initialized = True
            return True
            
        except Exception as e:
            logger.error(f"Error initializing MCP manager: {str(e)}")
            return False
    
    async def shutdown(self):
        """Shutdown all MCP connections."""
        disconnect_tasks = []
        for client in self.clients.values():
            disconnect_tasks.append(client.disconnect())
        
        if disconnect_tasks:
            await asyncio.gather(*disconnect_tasks, return_exceptions=True)
        
        self.clients.clear()
        self.is_initialized = False
        logger.info("MCP Manager shutdown complete")
    
    async def restart_server(self, server_name: str, new_config: Optional[Dict[str, Any]] = None) -> bool:
        """
        Restart a specific MCP server with optional new configuration.
        
        Args:
            server_name: Name of the server to restart
            new_config: Optional new configuration to apply
            
        Returns:
            bool: True if restart successful
        """
        try:
            # Disconnect existing server if it exists
            if server_name in self.clients:
                await self.clients[server_name].disconnect()
                del self.clients[server_name]
            
            # Load current config or use provided config
            if new_config:
                server_config = new_config
            else:
                config = self._load_config()
                if not config or server_name not in config.get("mcpServers", {}):
                    logger.error(f"No configuration found for server: {server_name}")
                    return False
                server_config = config["mcpServers"][server_name]
            
            # Create and connect new client
            client = MCPClient(server_config)
            self.clients[server_name] = client
            success = await self._connect_server(server_name, client)
            
            logger.info(f"Server {server_name} restart {'successful' if success else 'failed'}")
            return success
            
        except Exception as e:
            logger.error(f"Error restarting server {server_name}: {str(e)}")
            return False
    
    def _load_config(self) -> Optional[Dict[str, Any]]:
        """Load MCP configuration from file."""
        try:
            config_file = Path(self.config_path)
            if not config_file.exists():
                # Create default config directory
                config_file.parent.mkdir(parents=True, exist_ok=True)
                return None
                
            with open(config_file, 'r') as f:
                return json.load(f)
                
        except Exception as e:
            logger.error(f"Error loading MCP config: {str(e)}")
            return None
    
    async def _connect_server(self, server_name: str, client: MCPClient) -> bool:
        """Connect to a single MCP server."""
        try:
            success = await client.connect()
            if success:
                logger.info(f"Connected to MCP server: {server_name}")
            else:
                logger.error(f"Failed to connect to MCP server: {server_name}")
            return success
        except Exception as e:
            logger.error(f"Error connecting to MCP server {server_name}: {str(e)}")
            return False
    
    def get_all_resources(self) -> List[MCPResource]:
        """Get all resources from all connected MCP servers."""
        resources = []
        for server_name, client in self.clients.items():
            if client.is_connected:
                for resource in client.resources:
                    # Add server name to resource for identification
                    resource_dict = {
                        **resource.__dict__,
                        "server": server_name
                    }
                    resources.append(MCPResource(**resource_dict))
        return resources
    
    def get_all_tools(self) -> List[MCPTool]:
        """Get all tools from all connected MCP servers."""
        tools = []
        for server_name, client in self.clients.items():
            if client.is_connected:
                for tool in client.tools:
                    # Add server name to tool for identification
                    tool_dict = {
                        **tool.__dict__,
                        "server": server_name
                    }
                    tools.append(MCPTool(**tool_dict))
        return tools
    
    def get_all_prompts(self) -> List[MCPPrompt]:
        """Get all prompts from all connected MCP servers."""
        prompts = []
        for server_name, client in self.clients.items():
            if client.is_connected:
                for prompt in client.prompts:
                    # Add server name to prompt for identification
                    prompt_dict = {
                        **prompt.__dict__,
                        "server": server_name
                    }
                    prompts.append(MCPPrompt(**prompt_dict))
        return prompts
    
    async def get_resource_content(self, uri: str, server_name: Optional[str] = None) -> Optional[str]:
        """
        Get content from an MCP resource.
        
        Args:
            uri: Resource URI
            server_name: Specific server to query (if None, tries all servers)
            
        Returns:
            Resource content or None if not found
        """
        if server_name:
            client = self.clients.get(server_name)
            if client and client.is_connected:
                return await client.get_resource(uri)
        else:
            # Try all connected servers
            for client in self.clients.values():
                if client.is_connected:
                    content = await client.get_resource(uri)
                    if content:
                        return content
        return None
    
    async def call_tool(self, tool_name: str, arguments: Dict[str, Any], server_name: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """
        Call an MCP tool.
        
        Args:
            tool_name: Name of the tool to call
            arguments: Tool arguments
            server_name: Specific server to use (if None, tries all servers)
            
        Returns:
            Tool result or None if tool not found
        """
        if server_name:
            client = self.clients.get(server_name)
            if client and client.is_connected:
                return await client.call_tool(tool_name, arguments)
        else:
            # Try all connected servers
            for client in self.clients.values():
                if client.is_connected:
                    # Check if this server has the tool
                    if any(tool.name == tool_name for tool in client.tools):
                        return await client.call_tool(tool_name, arguments)
        return None
    
    async def get_prompt_content(self, prompt_name: str, arguments: Optional[Dict[str, Any]] = None, server_name: Optional[str] = None) -> Optional[str]:
        """
        Get content from an MCP prompt.
        
        Args:
            prompt_name: Name of the prompt
            arguments: Prompt arguments
            server_name: Specific server to query (if None, tries all servers)
            
        Returns:
            Prompt content or None if not found
        """
        if server_name:
            client = self.clients.get(server_name)
            if client and client.is_connected:
                return await client.get_prompt(prompt_name, arguments)
        else:
            # Try all connected servers
            for client in self.clients.values():
                if client.is_connected:
                    # Check if this server has the prompt
                    if any(prompt.name == prompt_name for prompt in client.prompts):
                        return await client.get_prompt(prompt_name, arguments)
        return None
    
    def get_server_status(self) -> Dict[str, Dict[str, Any]]:
        """Get status of all MCP servers."""
        status = {}
        for server_name, client in self.clients.items():
            # Determine if this is a built-in server
            is_builtin = server_name in self.BUILTIN_SERVERS
            
            status[server_name] = {
                "connected": client.is_connected,
                "resources": len(client.resources),
                "tools": len(client.tools),
                "prompts": len(client.prompts),
                "capabilities": client.capabilities,
                "builtin": is_builtin
            }
        return status
# Global MCP manager instance
_mcp_manager: Optional[MCPManager] = None
def get_mcp_manager() -> MCPManager:
    """Get the global MCP manager instance."""
    global _mcp_manager
    if _mcp_manager is None:
        _mcp_manager = MCPManager()
    return _mcp_manager
