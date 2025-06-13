"""
MCP Manager for handling multiple MCP servers and integrating with Ziya.
"""

import asyncio
import os
import sys
import json
from typing import Dict, List, Optional, Any
from pathlib import Path

from app.mcp.client import MCPClient, MCPResource, MCPTool, MCPPrompt # Assuming MCPClient is in the same directory or sys.path is configured
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
                "env": {
                    "ALLOW_COMMANDS": "ls,cat,pwd,grep,wc,touch,find,date,ps,curl,ping,cut,sort"
                },
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

    def _find_config_file(self) -> Optional[str]:
        """Find the MCP configuration file."""
        self.config_search_paths: List[str] = []
        
        # Check current working directory
        cwd_config = Path.cwd() / "mcp_config.json"
        self.config_search_paths.append(str(cwd_config))
        if cwd_config.exists(): 
            logger.info(f"Found MCP config file at: {cwd_config}")
            return str(cwd_config)
            
        # Check project root (assuming this script is in app/mcp/)
        project_root_config = Path(__file__).resolve().parents[2] / "mcp_config.json"
        self.config_search_paths.append(str(project_root_config))
        if project_root_config.exists(): 
            logger.info(f"Found MCP config file at: {project_root_config}")
            return str(project_root_config)
            
        # Default to user's Ziya directory
        user_config = Path.home() / ".ziya" / "mcp_config.json"
        self.config_search_paths.append(str(user_config))
        if user_config.exists():
            logger.info(f"Found MCP config file at: {user_config}")
            return str(user_config)
            
        logger.info(f"No MCP config file found. Searched paths: {self.config_search_paths}")
        return None
        
    def get_config_search_info(self) -> Dict[str, Any]:
        """Get information about config file search and status."""
        return {
            "config_path": self.config_path,
            "config_exists": self.config_path and Path(self.config_path).exists() if self.config_path else False,
            "search_paths": getattr(self, 'config_search_paths', [])
        }

    def refresh_config_path(self):
        """Re-search for config files and update the config path."""
        old_path = self.config_path
        self.config_path = self._find_config_file()
        if old_path != self.config_path:
            logger.info(f"Config path changed from {old_path} to {self.config_path}")
        else:
            logger.info(f"Config path unchanged: {self.config_path}")

    async def initialize(self) -> bool:
        """
        Initialize the MCP manager and connect to configured servers.
        
        Returns:
            bool: True if initialization successful
        """
        # Check if MCP is enabled
        if not os.environ.get("ZIYA_ENABLE_MCP", "false").lower() in ("true", "1", "yes"):
            logger.info("MCP is disabled. Use --mcp flag to enable MCP integration.")
            self.is_initialized = False
            return False
        
        # Re-search for config files in case new ones were added
        self.refresh_config_path()
        
        try:
        # Load configuration
            server_configs = self.builtin_server_definitions.copy()
            logger.info(f"Initialized with {len(server_configs)} built-in server definitions.")

            if self.config_path and os.path.exists(self.config_path):
                logger.info(f"Loading user MCP configuration from: {self.config_path}")
                try:
                    with open(self.config_path, 'r') as f:
                        user_config_data = json.load(f)
                    user_servers = user_config_data.get("mcpServers", {})
                    
                    for name, user_cfg in user_servers.items():
                        if name in server_configs and server_configs[name].get("builtin"):
                            logger.info(f"User configuration for '{name}' overrides built-in server.")
                            updated_config = server_configs[name].copy()
                            updated_config.update(user_cfg)
                            updated_config["builtin"] = True 
                            server_configs[name] = updated_config
                        else:
                            logger.info(f"Loaded user-defined server: '{name}'")
                            server_configs[name] = {**user_cfg, "builtin": False}
                    
                    logger.info(f"Loaded {len(user_servers)} user server configurations from {self.config_path}. Total servers: {len(server_configs)}")
                except Exception as e:
                    logger.error(f"Error loading user MCP config from {self.config_path}: {e}")
            else:
                if self.config_path:
                    logger.info(f"No MCP config file found at {self.config_path}. Using built-in server defaults.")
                else:
                    logger.info(f"No MCP configuration file found. Searched: {getattr(self, 'config_search_paths', [])}. Using built-in server defaults.")
            self.server_configs = server_configs # Store the final merged configs
        
            # Connect to each configured server
            connection_tasks = []
        
            for server_name, server_config in self.server_configs.items():
                if not server_config.get("enabled", True):
                    logger.info(f"MCP server {server_name} is disabled, skipping")
                    continue
                
                # Set environment variables for the server process
                server_env = os.environ.copy()
                if "env" in server_config:
                    server_env.update(server_config["env"])
                
                # Verify server command exists
                command = server_config.get("command", [])
                if command:
                    # For built-in servers, the command path is already absolute.
                    # For user-defined relative paths, MCPClient will resolve them.
                    if not server_config.get("builtin", False): # For non-builtin, check if script exists if relative
                        script_path_part = command[-1] if command else ""
                        if script_path_part.endswith('.py') and not os.path.isabs(script_path_part):
                            # Attempt to resolve relative to project root for user-defined scripts
                            # This matches MCPClient's behavior for resolving relative paths
                            # Note: MCPClient tries multiple roots, here we simplify for the check
                            proj_root_for_check = Path(__file__).resolve().parents[2] # Assuming app/mcp/manager.py
                            potential_user_script_path = proj_root_for_check / script_path_part
                            if not potential_user_script_path.exists():
                                logger.error(f"User-defined MCP server script not found: {script_path_part} (checked relative to {proj_root_for_check})")
                                continue
                    elif server_config.get("builtin", False):
                        # For built-in, command[2] is the absolute path to the script
                        builtin_script_path = command[2] if len(command) > 2 else ""
                        if not Path(builtin_script_path).exists():
                            logger.error(f"Built-in MCP server script not found at resolved path: {builtin_script_path}")
                            continue
            
                # Pass the environment to the client
                enhanced_config = server_config.copy()
                enhanced_config["env"] = server_env
                client = MCPClient(enhanced_config)
                self.clients[server_name] = client
                connection_tasks.append(self._connect_server(server_name, client))
            
            # Wait for all connections to complete
            if connection_tasks:
                results = await asyncio.gather(*connection_tasks, return_exceptions=True)
            
            # Log connection results
            successful_connections = sum(1 for result in results if result is True)
            builtin_count = sum(1 for cfg in self.server_configs.values() if cfg.get("builtin", False))
            user_count = len(self.server_configs) - builtin_count
            logger.info(f"MCP Manager initialized: {successful_connections}/{len(connection_tasks)} servers connected")
            
            # Debug server status
            for server_name, client in self.clients.items():
                    if client.is_connected:
                        logger.info(f"✅ {server_name}: {len(client.tools)} tools, {len(client.resources)} resources")
                        for tool in client.tools:
                            logger.info(f"   - Tool: {tool.name}")
                    else:
                        logger.warning(f"❌ {server_name}: Connection failed")
                
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
                # Reload all configs to get the specific server's config
                await self._load_server_configs() 
                server_config = self.server_configs.get(server_name)
                if not server_config:
                    logger.error(f"No configuration found for server '{server_name}' during restart.")
                    return False
            
                # Create and connect new client
                client = MCPClient(server_config)
                self.clients[server_name] = client
                success = await self._connect_server(server_name, client)
                
                logger.info(f"Server {server_name} restart {'successful' if success else 'failed'}")
                return success
            
        except Exception as e:
            logger.error(f"Error restarting server {server_name}: {str(e)}")
            return False

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
        logger.info(f"MCP_MANAGER.get_all_tools: Starting tool collection. {len(self.clients)} clients total.")
        for server_name, client in self.clients.items():
            if client.is_connected:
                client_tools = client.tools
                logger.info(f"MCP_MANAGER.get_all_tools: Server '{server_name}' has {len(client_tools)} tools: {[t.name for t in client_tools]}")
                for tool_data in client_tools:
                    # Create MCPTool without server parameter
                    mcp_tool = MCPTool(
                        name=tool_data.name,
                        description=tool_data.description,
                        inputSchema=tool_data.inputSchema
                    )
                    # Store server name as an attribute for reference
                    mcp_tool._server_name = server_name # type: ignore
                    logger.info(f"MCP_MANAGER.get_all_tools: Adding tool '{tool_data.name}' from server '{server_name}' to collection.")
                    tools.append(mcp_tool) 
            else:
                logger.warning(f"MCP_MANAGER.get_all_tools: Server '{server_name}' is not connected. Skipping its tools.")
        logger.info(f"MCP_MANAGER.get_all_tools: Total tools collected: {len(tools)} from {len([c for c in self.clients.values() if c.is_connected])} connected servers. Tool names: {[t.name for t in tools]}")
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
        """
        # Remove mcp_ prefix if present for internal tool lookup
        internal_tool_name = tool_name
        if tool_name.startswith("mcp_"):
            internal_tool_name = tool_name[4:]
        
        logger.info(f"🔍 MCP_MANAGER: Looking for tool '{internal_tool_name}' (original: '{tool_name}')")
        logger.info(f"🔍 MCP_MANAGER: Available tools: {[tool.name for client in self.clients.values() if client.is_connected for tool in client.tools]}")
        
        if server_name:
            client = self.clients.get(server_name)
            if client and client.is_connected:
                return await client.call_tool(tool_name, arguments)
        else:
            # Try all connected servers
            for client in self.clients.values():
                if client.is_connected:
                    # Check if this server has the tool (try both original and internal names)
                    tool_names_to_try = [tool_name, internal_tool_name]
                    for name_to_try in tool_names_to_try:
                        if any(tool.name == name_to_try for tool in client.tools):
                            logger.info(f"🔍 MCP_MANAGER: Found tool '{name_to_try}' in server, executing...")
                            result = await client.call_tool(name_to_try, arguments)
                            logger.info(f"🔍 MCP_MANAGER: Tool execution result: {result}")
                            return result
            
            logger.warning(f"🔍 MCP_MANAGER: Tool '{internal_tool_name}' not found in any connected server")
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
            is_builtin = self.server_configs.get(server_name, {}).get("builtin", False)
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
