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
                "command": sys.executable,
                "args": ["-u", str(package_dir / "time_server.py")],
                "enabled": True,
                "description": "Provides current time functionality",
                "builtin": True
            }
            builtin_servers["shell"] = {
                "command": sys.executable,
                "args": ["-u", str(package_dir / "shell_server.py")],
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
            
        logger.debug(f"No MCP config file found. Searched paths: {self.config_search_paths}")
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
        # Only log if path actually changes to reduce noise
        old_path = self.config_path
        self.config_path = self._find_config_file()
        if old_path != self.config_path:
            logger.info(f"Config path changed from {old_path} to {self.config_path}")
        elif old_path is None and self.config_path is None:
            logger.debug("No MCP config file found in standard locations")
        else:
            logger.debug(f"Config path unchanged: {self.config_path}")

    async def initialize(self) -> bool:
        """
        Initialize the MCP manager and connect to configured servers.
        
        Returns:
            bool: True if initialization successful
        """
        # Check if MCP is enabled
        if not os.environ.get("ZIYA_ENABLE_MCP", "true").lower() in ("true", "1", "yes"):
            logger.info("MCP is disabled. Use --mcp flag to enable MCP integration.")
            self.is_initialized = False
            return False
        
        # Re-search for config files in case new ones were added
        self.refresh_config_path()
        
        # Load configuration
        server_configs = self.builtin_server_definitions.copy()
        logger.info(f"Initialized with {len(server_configs)} built-in server definitions.")

        if self.config_path and os.path.exists(self.config_path):
            logger.info(f"Loading user MCP configuration from: {self.config_path}")
            try:
                with open(self.config_path, 'r') as f:
                    user_config_data = json.load(f)
            except json.JSONDecodeError as e:
                logger.error(f"Invalid JSON in MCP config file {self.config_path}: {e}")
                logger.error(f"Line {e.lineno}, Column {e.colno}: {e.msg}")
                logger.warning("Skipping malformed config file, using built-in defaults only")
                user_config_data = {}
            except Exception as e:
                logger.error(f"Error reading MCP config from {self.config_path}: {e}")
                logger.warning("Skipping unreadable config file, using built-in defaults only")
                user_config_data = {}
            
            try:
                user_servers = user_config_data.get("mcpServers", {})
                
                for name, user_cfg in user_servers.items():
                    # RESILIENCE: Normalize command format
                    # MCP protocol expects: command = string, args = array
                    # But some configs incorrectly have: command = array
                    if "command" in user_cfg:
                        command = user_cfg["command"]
                        if isinstance(command, list):
                            logger.warning(f"Server '{name}' has command as array (incorrect format), normalizing...")
                            if len(command) > 0:
                                # Split into command (first element) and args (rest)
                                user_cfg["command"] = command[0]
                                if len(command) > 1:
                                    # Merge with existing args if present
                                    existing_args = user_cfg.get("args", [])
                                    user_cfg["args"] = command[1:] + existing_args
                                logger.info(f"Normalized '{name}': command='{command[0]}', args={user_cfg.get('args', [])}")
                            else:
                                logger.error(f"Server '{name}' has empty command array, skipping")
                                continue
                        elif not isinstance(command, str):
                            logger.error(f"Server '{name}' has invalid command type {type(command)}, skipping")
                            continue
                    
                    # RESILIENCE: Ensure args is an array if present
                    if "args" in user_cfg and not isinstance(user_cfg["args"], list):
                        logger.warning(f"Server '{name}' has non-array args, converting to list")
                        user_cfg["args"] = [str(user_cfg["args"])]
                    
                    if name in server_configs and server_configs[name].get("builtin"):
                        logger.info(f"User configuration for '{name}' overrides built-in server.")
                        updated_config = server_configs[name].copy()
                        updated_config.update(user_cfg)
                        
                        # CRITICAL: For builtin servers, ensure script path is absolute
                        # User configs may have relative paths that won't resolve correctly
                        if "args" in updated_config and len(updated_config["args"]) > 0:
                            script_arg = updated_config["args"][-1]  # Last arg is typically the script
                            if script_arg.endswith('.py') and not os.path.isabs(script_arg):
                                # Replace with absolute path from builtin definition
                                builtin_args = server_configs[name].get("args", [])
                                if builtin_args:
                                    updated_config["args"] = builtin_args
                                    logger.info(f"Preserved absolute script path for builtin server '{name}': {builtin_args[-1]}")
                        
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
                logger.debug(f"No MCP config file found at {self.config_path}. Using built-in server defaults.")
            else:
                logger.debug(f"No MCP configuration file found. Searched: {getattr(self, 'config_search_paths', [])}. Using built-in server defaults.")
        self.server_configs = server_configs # Store the final merged configs
    
        try:
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
                command = server_config.get("command")
                args = server_config.get("args", [])
                
                if command:
                    # For built-in servers, the command path is already absolute.
                    # For user-defined relative paths, MCPClient will resolve them.
                    if not server_config.get("builtin", False): # For non-builtin, check if script exists if relative
                        # Check if script is in args (new format) or command (old format)
                        script_path_part = args[-1] if args and args[-1].endswith('.py') else ""
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
                        # For built-in, args[-1] should be the absolute path to the script
                        builtin_script_path = args[-1] if args else ""
                        if not Path(builtin_script_path).exists():
                            logger.error(f"Built-in MCP server script not found at resolved path: {builtin_script_path}")
                            continue
                
                # Pass the environment to the client
                enhanced_config = server_config.copy()
                enhanced_config["env"] = server_env
                enhanced_config["name"] = server_name  # Add server name to config
                
                # Add external server specific configuration
                # Handle both string command and array args for keyword detection
                command_str = command if isinstance(command, str) else (' '.join(command) if isinstance(command, list) else '')
                args_str = ' '.join(args) if isinstance(args, list) else ''
                full_command = f"{command_str} {args_str}"
                
                if any(keyword in full_command.lower() for keyword in ['fetch', 'uvx', 'npx']):
                    enhanced_config["external_server"] = True
                    enhanced_config["max_retries"] = 5
                    enhanced_config["timeout"] = 60
                    enhanced_config["enable_response_cleaning"] = True
                    logger.info(f"Configured {server_name} as external server with enhanced settings")
                
                client = MCPClient(enhanced_config)
                self.clients[server_name] = client
                connection_tasks.append(self._connect_server(server_name, client))
            
            # Wait for all connections to complete
            results = []
            if connection_tasks:
                results = await asyncio.gather(*connection_tasks, return_exceptions=True)
                # Invalidate cache after initial connections are established
                self.invalidate_tools_cache()
            
            # Log connection results
            successful_connections = sum(1 for result in results if result is True)
            builtin_count = sum(1 for cfg in self.server_configs.values() if cfg.get("builtin", False))
            user_count = len(self.server_configs) - builtin_count
            logger.info(f"MCP Manager initialized: {successful_connections}/{len(connection_tasks)} servers connected")
            
            # Debug server status
            for server_name, client in self.clients.items():
                if client.is_connected:
                    logger.info(f"✅ {server_name}: {len(client.tools)} tools, {len(client.resources)} resources")
                    logger.debug(f"   Tools: {', '.join(tool.name for tool in client.tools)}")
                else:
                    logger.warning(f"❌ {server_name}: Connection failed")
            
            logger.info(f"Server breakdown: {builtin_count} built-in, {user_count} user-configured")
            
            self.is_initialized = True
            return True
        except Exception as e:
            logger.error(f"Error initializing MCP manager: {str(e)}")
            return False
    
    async def _cleanup_stuck_external_servers(self):
        """Cleanup external servers that may be stuck or unresponsive."""
        for server_name, client in self.clients.items():
            server_config = self.server_configs.get(server_name, {})
            
            # Identify external servers by command patterns
            command = server_config.get("command", [])
            is_external = any(keyword in ' '.join(command).lower() 
                            for keyword in ['fetch', 'uvx', 'npx', 'node'])
            
            if is_external and hasattr(client, '_consecutive_failures'):
                if client._consecutive_failures >= 5:
                    logger.warning(f"Restarting stuck external server: {server_name}")
                    
                    try:
                        await client.disconnect()
                        # Give external process time to cleanup
                        await asyncio.sleep(2.0)
                        
                        success = await client.connect()
                        if success:
                            logger.info(f"Successfully restarted external server: {server_name}")
                            client._consecutive_failures = 0
                    except Exception as e:
                        logger.error(f"Failed to restart external server {server_name}: {e}")
                        self._failed_servers.add(server_name)
    
    async def _ensure_client_healthy(self, client: 'MCPClient') -> bool:
        """Ensure client is healthy, reconnect if necessary."""
        server_name = getattr(client, 'server_name', client.server_config.get('name', 'unknown'))
        
        # Skip servers that have failed too many times
        if server_name in self._failed_servers:
            logger.debug(f"Server {server_name} is in failed state, skipping health check")
            return False
        
        # Prevent rapid reconnection attempts (minimum 30 seconds between attempts)
        last_attempt = self._reconnection_attempts.get(server_name, 0)
        if time.time() - last_attempt < 30:
            logger.debug(f"Skipping reconnection attempt for {server_name} - too recent ({time.time() - last_attempt:.1f}s ago)")
            return False
            
        if not client.is_connected or (hasattr(client, '_is_process_healthy') and not client._is_process_healthy()):
            logger.warning(f"Client {server_name} unhealthy, attempting reconnection")
            
            # Record this reconnection attempt
            self._reconnection_attempts[server_name] = time.time()
            
            try:
                await client.disconnect()
                success = await client.connect()
                if success:
                    logger.info(f"Client {server_name} reconnection successful")
                    # Invalidate tools cache to reload capabilities
                    self.invalidate_tools_cache()
                    # Reset failure count on successful reconnection
                    if hasattr(self, '_reconnection_failures'):
                        self._reconnection_failures.pop(server_name, None)
                    return True
                else:
                    logger.error(f"Client {server_name} reconnection failed")
                    
                    # Track failed attempts - if too many, disable this server
                    if not hasattr(self, '_reconnection_failures'):
                        self._reconnection_failures = {}
                    self._reconnection_failures[server_name] = self._reconnection_failures.get(server_name, 0) + 1
                    
                    if self._reconnection_failures[server_name] >= 3:
                        logger.error(f"Server {server_name} failed {self._reconnection_failures[server_name]} times, disabling")
                        self._failed_servers.add(server_name)
                    return False
            except Exception as e:
                logger.error(f"Error during client reconnection for {server_name}: {e}")
                return False
        return True
    
    async def shutdown(self):
        """Shutdown all MCP connections."""
        disconnect_tasks = []
        for client in self.clients.values():
            disconnect_tasks.append(client.disconnect())
        
        if disconnect_tasks:
            await asyncio.gather(*disconnect_tasks, return_exceptions=True)
        
        self.clients.clear()
        self.invalidate_tools_cache()  # Invalidate cache when clients change
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
                # Get the specific server's config from current configs
                server_config = self.server_configs.get(server_name)
                if not server_config:
                    # If not found, try to get from builtin definitions
                    server_config = self.builtin_server_definitions.get(server_name)
                    if not server_config:
                        logger.error(f"No configuration found for server '{server_name}' during restart.")
                        return False
                    else:
                        # Add the builtin config to server_configs
                        self.server_configs[server_name] = server_config.copy()
            
            # Create and connect new client
            client = MCPClient(server_config)
            self.clients[server_name] = client
            success = await self._connect_server(server_name, client)
            
            # Invalidate cache when client configuration changes
            self.invalidate_tools_cache()
            
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
        """Get all tools from all connected MCP servers with caching."""
        current_time = time.time()
        
        # Check if cache is valid
        if (self._tools_cache is not None and 
            current_time - self._tools_cache_timestamp < self._tools_cache_ttl):
            logger.info(f"MCP_MANAGER.get_all_tools: Using cached tools ({len(self._tools_cache)} tools)")
            return self._tools_cache
        
        # Cache miss or expired - fetch fresh tools
        tools = []
        logger.info(f"MCP_MANAGER.get_all_tools: Starting tool collection. {len(self.clients)} clients total.")
        for server_name, client in self.clients.items():
            # Check both connection status AND enabled status
            server_config = self.server_configs.get(server_name, {})
            is_enabled = server_config.get("enabled", True)
            
            logger.debug(f"MCP_MANAGER.get_all_tools: Server '{server_name}' - connected: {client.is_connected}, enabled: {is_enabled}")
            
            if client.is_connected and is_enabled:
                client_tools = client.tools
                logger.debug(f"MCP_MANAGER.get_all_tools: Server '{server_name}' has {len(client_tools)} tools: {[t.name for t in client_tools]}")
                for tool_data in client_tools:
                    mcp_tool = MCPTool(
                        name=tool_data.name,
                        description=tool_data.description,
                        inputSchema=tool_data.inputSchema
                    )
                    # Store server name as an attribute for reference
                    mcp_tool._server_name = server_name # type: ignore
                    logger.debug(f"MCP_MANAGER.get_all_tools: Adding tool '{tool_data.name}' from server '{server_name}' to collection.")
                    tools.append(mcp_tool) 
            elif not is_enabled:
                logger.debug(f"MCP_MANAGER.get_all_tools: Server '{server_name}' is disabled, skipping tools")
            else:
                logger.warning(f"MCP_MANAGER.get_all_tools: Server '{server_name}' is not connected. Skipping its tools.")
        
        logger.debug(f"MCP_MANAGER.get_all_tools: Total tools collected: {len(tools)} from {len([c for c in self.clients.values() if c.is_connected])} connected servers. Tool names: {[t.name for t in tools]}")
        
        # Update cache
        self._tools_cache = tools
        self._tools_cache_timestamp = current_time
        logger.debug(f"MCP_MANAGER.get_all_tools: Cached {len(tools)} tools for {self._tools_cache_ttl}s")
        
        # Add dynamically loaded tools
        dynamic_loader = get_dynamic_loader()
        dynamic_tools = dynamic_loader.get_active_tools()
        
        if dynamic_tools:
            logger.info(f"Adding {len(dynamic_tools)} dynamic tools to tool list")
            for tool_name, tool_instance in dynamic_tools.items():
                # Convert dynamic tool to MCPTool format
                mcp_tool = MCPTool(
                    name=tool_instance.name,
                    description=tool_instance.description,
                    inputSchema=tool_instance.InputSchema.schema()
                )
                mcp_tool._server_name = "dynamic"  # type: ignore
                mcp_tool._is_dynamic = True  # type: ignore
                tools.append(mcp_tool)
                logger.debug(f"Added dynamic tool: {tool_name}")
        
        return tools
    
    def invalidate_tools_cache(self):
        """Invalidate the tools cache to force refresh on next get_all_tools call."""
        self._tools_cache = None
        self._tools_cache_timestamp = 0
        logger.info("MCP_MANAGER: Tools cache invalidated")
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
    
    def _is_repetitive_call(self, tool_name: str, arguments: Dict[str, Any], conversation_id: Optional[str] = None) -> bool:
        """
        Check if this tool call is repetitive within the detection window for this conversation.
        
        Args:
            tool_name: Name of the tool being called
            arguments: Tool arguments
            conversation_id: Optional conversation ID for conversation-specific tracking
            
        Returns:
            True if the call should be blocked as repetitive
        """
        # Use a default conversation ID if none provided
        conv_id = conversation_id or 'default'
        
        current_time = time.time()
        call_signature = (tool_name, json.dumps(arguments, sort_keys=True))
        
        # Get or create the call list for this conversation
        if conv_id not in self._recent_tool_calls:
            self._recent_tool_calls[conv_id] = []
        
        conv_calls = self._recent_tool_calls[conv_id]
        
        # Clean old calls outside the window
        self._recent_tool_calls[conv_id] = [
            (name, args, timestamp) for name, args, timestamp in conv_calls
            if current_time - timestamp <= self._loop_detection_window
        ]
        
        # Count identical calls in the window
        identical_calls = sum(1 for name, args, timestamp in self._recent_tool_calls[conv_id]
                             if (name, args) == call_signature)
        
        # Allow retries with different parameters or after reasonable delay
        if identical_calls > 0:
            last_call_time = max(timestamp for name, args, timestamp in self._recent_tool_calls[conv_id]
                                if (name, args) == call_signature)
            if current_time - last_call_time > 10:  # Allow retry after 10 seconds
                identical_calls = 0
        
        # Only add call if we're NOT blocking it
        if identical_calls < 5:
            self._recent_tool_calls[conv_id].append((tool_name, json.dumps(arguments, sort_keys=True), current_time))
            
            # Keep only recent calls per conversation
            if len(self._recent_tool_calls[conv_id]) > self._max_recent_calls:
                self._recent_tool_calls[conv_id] = self._recent_tool_calls[conv_id][-self._max_recent_calls:]
        
        return identical_calls >= 5  # Allow max 5 identical calls before blocking
    
    def _coerce_argument_types(self, tool_name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        """Coerce argument types based on tool schema to fix string-to-number issues."""
        if not arguments:
            return arguments
            
        # Find the tool schema
        tool_schema = None
        for client in self.clients.values():
            if client.is_connected:
                for tool in client.tools:
                    if tool.name == tool_name:
                        tool_schema = tool.inputSchema
                        break
                if tool_schema:
                    break
        
        if not tool_schema or 'properties' not in tool_schema:
            return arguments
        
        # Coerce types based on schema
        coerced = {}
        for key, value in arguments.items():
            if key in tool_schema['properties']:
                expected_type = tool_schema['properties'][key].get('type')
                if expected_type == 'number' and isinstance(value, str):
                    try:
                        coerced[key] = int(value) if '.' not in value else float(value)
                    except ValueError:
                        coerced[key] = value
                elif expected_type == 'integer' and isinstance(value, str):
                    try:
                        coerced[key] = int(value)
                    except ValueError:
                        coerced[key] = value
                elif expected_type == 'boolean' and isinstance(value, str):
                    coerced[key] = value.lower() in ('true', '1', 'yes')
                else:
                    coerced[key] = value
            else:
                coerced[key] = value
        
        return coerced

    def _normalize_tool_parameters(self, tool_name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        """
        Normalize tool parameters to handle tool_input wrapper inconsistency.
        
        This allows models to call tools with or without the tool_input wrapper,
        and automatically converts to the format the tool expects.
        
        Args:
            tool_name: Name of the tool being called
            arguments: Parameters passed by the model
            
        Returns:
            Normalized parameters matching the tool's expected schema
        """
        if not isinstance(arguments, dict):
            return arguments
        
        # CRITICAL: Handle JSON string arguments at the top level
        # Models sometimes pass the entire arguments as a JSON string
        # This can happen with native function calling when serialization is involved
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments)
                logger.info(f"Parsed top-level JSON string arguments for {tool_name}")
            except (json.JSONDecodeError, TypeError) as e:
                logger.error(f"Failed to parse arguments as JSON string: {e}")
                return arguments
        
        # Handle tool_input as JSON string BEFORE schema normalization
        # This is critical because we need dict operations later
        if isinstance(arguments, dict) and 'tool_input' in arguments:
            tool_input = arguments['tool_input']
            if isinstance(tool_input, str):
                try:
                    arguments['tool_input'] = json.loads(tool_input)
                    logger.info(f"Parsed tool_input JSON string for {tool_name}")
                except (json.JSONDecodeError, TypeError) as e:
                    logger.error(f"Failed to parse tool_input as JSON string: {e}")
                    # Return error immediately rather than letting it fail downstream
                    return {"__validation_error__": True, "message": f"Invalid JSON in tool_input: {str(e)}"}
        
        try:
            # Find the tool schema
            tool_schema = None
            for client in self.clients.values():
                if client.is_connected:
                    for tool in client.tools:
                        if tool.name == tool_name:
                            tool_schema = tool.inputSchema
                            break
                    if tool_schema:
                        break
            
            if not tool_schema or 'properties' not in tool_schema:
                return arguments
            
            properties = tool_schema['properties']
            
            # Check if schema expects tool_input wrapper
            schema_uses_wrapper = (
                len(properties) == 1 and 
                "tool_input" in properties and
                isinstance(properties["tool_input"], dict) and
                "properties" in properties["tool_input"]
            )
            
            # Check if parameters are wrapped
            params_are_wrapped = "tool_input" in arguments and len(arguments) == 1
            
            # Case 1: Schema expects wrapper, params are NOT wrapped -> wrap them
            if schema_uses_wrapper and not params_are_wrapped:
                logger.info(f"Auto-wrapping parameters for {tool_name} with tool_input")
                return {"tool_input": arguments}
            
            # Case 2: Schema does NOT expect wrapper, params ARE wrapped -> unwrap them
            if not schema_uses_wrapper and params_are_wrapped:
                logger.info(f"Auto-unwrapping tool_input for {tool_name}")
                return arguments["tool_input"]
            
            # Case 3: Both match - return as-is
            return arguments
            
        except Exception as e:
            logger.warning(f"Error normalizing parameters for {tool_name}: {e}")
            return arguments

    async def call_tool(self, tool_name: str, arguments: Dict[str, Any], server_name: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """
        Call an MCP tool.
        
        Args:
            tool_name: Name of the tool to call
            arguments: Tool arguments
            server_name: Specific server to use (if None, tries all servers)
            
        Keyword Args:
            conversation_id: Optional conversation ID for tracking repetitive calls
            
        Returns:
            Tool execution result or None if not found
        """

        # Check if this is a dynamic tool first
        dynamic_loader = get_dynamic_loader()
        internal_tool_name = tool_name[4:] if tool_name.startswith("mcp_") else tool_name
        dynamic_tool = dynamic_loader.get_tool(internal_tool_name)

        if dynamic_tool:
            logger.info(f"Executing dynamic tool: {internal_tool_name}")
            try:
                result = await dynamic_tool.execute(**arguments)
                return {"content": [{"type": "text", "text": str(result)}]}
            except Exception as e:
                logger.error(f"Dynamic tool execution failed: {e}", exc_info=True)
                return {"error": True, "message": str(e)}
        
        # Check tool permissions before execution
        from app.mcp.permissions import get_permissions_manager
        permissions_manager = get_permissions_manager()
        permissions = permissions_manager.get_permissions()
        
        # Remove mcp_ prefix for internal lookup
        internal_tool_name = tool_name[4:] if tool_name.startswith("mcp_") else tool_name
        
        # Find which server this tool belongs to
        tool_server = None
        for srv_name, client in self.clients.items():
            if client.is_connected and any(tool.name == internal_tool_name for tool in client.tools):
                tool_server = srv_name
                break
        
        # Check permissions if we found the server
        if tool_server:
            server_perms = permissions.get('servers', {}).get(tool_server, {})
            tool_perms = server_perms.get('tools', {}).get(internal_tool_name, {})
            tool_permission = tool_perms.get('permission', permissions.get('defaults', {}).get('tool', 'enabled'))
            
            if tool_permission == 'disabled':
                logger.info(f"Tool {internal_tool_name} is disabled")
                return {
                    "error": True,
                    "message": f"Tool '{internal_tool_name}' is currently disabled. You can enable it in MCP Server Settings.",
                    "code": -32001
                }
        
        # Check for repetitive calls (conversation-aware)
        conversation_id = arguments.get('conversation_id') if isinstance(arguments, dict) else None
        if self._is_repetitive_call(tool_name, arguments, conversation_id):
            logger.warning(f"🔍 MCP_MANAGER: Blocking repetitive tool call: {tool_name} with {arguments}")
            return {
                "error": True,
                "message": f"Tool call blocked: {tool_name} has been called repeatedly with similar arguments. Please try a different approach or check if the previous results contain what you need.",
                "code": -32001
            }
            
        # Periodic cleanup of stuck external servers
        if time.time() % 300 < 1:  # Every 5 minutes
            asyncio.create_task(self._cleanup_stuck_external_servers())
            # Don't return here - this is just a background cleanup task
        
        # Remove mcp_ prefix if present for internal tool lookup
        internal_tool_name = tool_name
        if tool_name.startswith("mcp_"):
            internal_tool_name = tool_name[4:]
        
        # Normalize parameters to handle tool_input wrapper inconsistency
        # This must happen AFTER internal tool name resolution but BEFORE type coercion
        arguments = self._normalize_tool_parameters(internal_tool_name, arguments)
        
        # Handle JSON string tool_input (kept for backward compatibility)
        if isinstance(arguments, dict) and 'tool_input' in arguments and isinstance(arguments['tool_input'], str):
            try:
                arguments['tool_input'] = json.loads(arguments['tool_input'])
            except json.JSONDecodeError:
                pass  # Let it fail naturally downstream
        
        # Coerce argument types based on tool schema
        arguments = self._coerce_argument_types(internal_tool_name, arguments)

        # Final safety check: If normalization/coercion produced a validation error, return it immediately
        if isinstance(arguments, dict) and arguments.get("__validation_error__"):
            return {
                "error": True,
                "message": arguments.get("message", "Invalid arguments"),
                "code": -32602
            }
        
        if server_name:
            client = self.clients.get(server_name)
            if client:
                if not client.is_connected:
                    return {"error": True, "message": f"Server '{server_name}' is not connected", "code": -32002}
                    
                # Ensure client is healthy before making the call
                if hasattr(client, '_is_process_healthy') and not await self._ensure_client_healthy(client):
                    logger.error(f"Client {server_name} is unhealthy, cannot execute tool")
                    return {"error": True, "message": f"Server '{server_name}' is unhealthy", "code": -32002}
                    
                return await client.call_tool(tool_name, arguments)
                    
            # If tool call fails with validation error, don't try other servers
            result = await client.call_tool(tool_name, arguments)
            
            # Return validation errors immediately so the model can see them
            if isinstance(result, dict) and result.get("error"):
                error_msg = str(result.get("message", ""))
                if "validation" in error_msg.lower() or "required field" in error_msg.lower():
                    logger.info(f"Returning validation error to model: {error_msg}")
                return result
            
        else:
            # Try all connected servers
            for client in self.clients.values():
                if client.is_connected:
                    # Check if this server has the tool (try both original and internal names)
                    tool_names_to_try = [tool_name, internal_tool_name]
                    for name_to_try in tool_names_to_try:
                        if any(tool.name == name_to_try for tool in client.tools):
                            # Ensure client is healthy before making the call
                            if hasattr(client, '_is_process_healthy') and not await self._ensure_client_healthy(client):
                                logger.warning(f"Client unhealthy, skipping tool execution")
                                continue
                                
                            logger.debug(f"🔍 MCP_MANAGER: Found tool '{name_to_try}' in server, executing...")
                            logger.debug(f"🔍 MCP_MANAGER: About to call client.call_tool with name='{name_to_try}', arguments={arguments}")
                            print(f"🔍 MCP_MANAGER: About to call client.call_tool with name='{name_to_try}', arguments={arguments}")
                            result = await client.call_tool(name_to_try, arguments)
                            logger.debug(f"🔍 MCP_MANAGER: Tool execution result: {result}")
                            return result
            
            # Tool not found - provide helpful error message
            logger.warning(f"🔍 MCP_MANAGER: Tool '{internal_tool_name}' not found in any connected server")
            
            # Check if tool exists in a disconnected server
            disconnected_server = None
            for srv_name, config in self.server_configs.items():
                if config.get("builtin") and srv_name == "shell" and internal_tool_name == "run_shell_command":
                    disconnected_server = srv_name
                    break
            
            if disconnected_server:
                return {"error": True, "message": f"Tool '{internal_tool_name}' is available in the '{disconnected_server}' server, but that server is currently disconnected. Please check MCP Server Settings to reconnect it.", "code": -32002}
            
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
        
        # Iterate over all configured servers, not just connected clients
        # This ensures we show status for servers that failed to connect
        for server_name, server_config in self.server_configs.items():
            client = self.clients.get(server_name)
            is_builtin = server_config.get("builtin", False)
            
            # If client exists, get actual connection status
            if client:
                status[server_name] = {
                    "connected": client.is_connected,
                    "resources": len(client.resources),
                    "tools": len(client.tools),
                    "prompts": len(client.prompts),
                    "capabilities": client.capabilities,
                    "builtin": is_builtin
                }
            else:
                # Server is configured but not in clients (failed to start or never started)
                status[server_name] = {
                    "connected": False,
                    "resources": 0,
                    "tools": 0,
                    "prompts": 0,
                    "capabilities": {},
                    "builtin": is_builtin
                }
        return status

# Global MCP manager instance
_mcp_manager: Optional[MCPManager] = None
def get_mcp_manager():
    """Get the global MCP manager instance."""
    import os
    
    # Check if MCP is enabled before creating manager
    if not os.environ.get("ZIYA_ENABLE_MCP", "true").lower() in ("true", "1", "yes"):
        # Return a dummy manager that's never initialized when MCP is disabled
        class DisabledMCPManager:
            def __init__(self):
                self.is_initialized = False
                self.clients = {}
                self.server_configs = {}
            
            async def initialize(self):
                pass
            
            def get_all_tools(self):
                return []
            
            def get_server_status(self):
                return {}
                
        return DisabledMCPManager()  # type: ignore
    
    global _mcp_manager
    if _mcp_manager is None:
        _mcp_manager = MCPManager()
    return _mcp_manager
