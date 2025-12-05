"""
API routes for MCP (Model Context Protocol) management.
"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
import os
import json
import asyncio
from typing import Dict, List, Any, Optional, Literal
from dataclasses import asdict

from app.mcp.manager import get_mcp_manager
from app.utils.logging_utils import logger
from app.config.shell_config import DEFAULT_SHELL_CONFIG, get_default_shell_config
from app.mcp.permissions import get_permissions_manager
from app.agents.agent import estimate_token_count
from app.config.models_config import MODEL_CONFIGS
from app.mcp.registry_manager import get_registry_manager

router = APIRouter(prefix="/api/mcp", tags=["mcp"])



class MCPServerConfig(BaseModel):
    model_config = {"extra": "allow"}
    name: str
    command: List[str]
    args: Optional[List[str]] = None
    env: Optional[Dict[str, str]] = None
    enabled: bool = True

class ShellConfig(BaseModel):
    model_config = {"extra": "allow"}
    enabled: bool = DEFAULT_SHELL_CONFIG["enabled"]
    allowedCommands: List[str] = DEFAULT_SHELL_CONFIG["allowedCommands"]
    gitOperationsEnabled: bool = DEFAULT_SHELL_CONFIG["gitOperationsEnabled"]
    safeGitOperations: List[str] = DEFAULT_SHELL_CONFIG["safeGitOperations"]
    timeout: int = DEFAULT_SHELL_CONFIG["timeout"]
    persist: bool = False  # New field to indicate whether to save to config file

class ServerToggleRequest(BaseModel):
    model_config = {"extra": "allow"}
    server_name: str
    enabled: bool

PermissionLevel = Literal["enabled", "disabled", "ask"]

class ServerPermissionUpdateRequest(BaseModel):
    model_config = {"extra": "allow"}
    server_name: str
    permission: PermissionLevel

class ToolPermissionUpdateRequest(BaseModel):
    model_config = {"extra": "allow"}
    server_name: str
    tool_name: str
    permission: PermissionLevel

class PermissionsData(BaseModel):
    model_config = {"extra": "allow"}
    defaults: Dict[str, Any]
    servers: Dict[str, Any]


def count_tool_tokens(tool_schema: Dict[str, Any]) -> int:
    """
    Count tokens used by a tool schema using existing token counter.
    
    Args:
        tool_schema: The tool schema dictionary (inputSchema)
        
    Returns:
        Estimated token count for this tool
    """
    if not tool_schema:
        return 0
    
    try:
        # Serialize the schema to JSON (as it would appear in the context)
        schema_json = json.dumps(tool_schema, separators=(',', ':'))
        return estimate_token_count(schema_json)
    except Exception:
        # Fallback to string representation if JSON serialization fails
        return estimate_token_count(str(tool_schema))


def count_server_tool_tokens(tools: List[Dict[str, Any]]) -> int:
    """
    Count total tokens used by all tools from a server.
    
    Args:
        tools: List of tool dictionaries with 'name', 'description', 'inputSchema'
        
    Returns:
        Total estimated token count for all tools
    """
    total = 0
    
    for tool in tools:
        # Count tokens in tool name
        total += estimate_token_count(tool.get('name', ''))
        
        # Count tokens in tool description
        total += estimate_token_count(tool.get('description', ''))
        
        # Count tokens in input schema
        input_schema = tool.get('inputSchema', {})
        total += count_tool_tokens(input_schema)
    
    return total


def format_token_count(tokens: int) -> str:
    """Format token count for display."""
    if tokens >= 1000000:
        return f"{tokens / 1000000:.1f}M"
    if tokens >= 1000:
        return f"{tokens / 1000:.1f}K"
    return str(tokens)


def calculate_model_instruction_tokens() -> Dict[str, Any]:
    """
    Calculate token counts for instructions across all enabled models.
    
    Returns:
        Dictionary with total tokens and per-model breakdown
    """
    import os
    
    # Get the current endpoint and model
    endpoint = os.environ.get("ZIYA_ENDPOINT", "bedrock")
    current_model = os.environ.get("ZIYA_MODEL", "sonnet4.5")
    
    # Get all available models for the endpoint
    available_models = MODEL_CONFIGS.get(endpoint, {})
    
    # For now, we'll estimate based on typical instruction sizes
    # In a future enhancement, we could read actual instruction files
    # and calculate their exact token counts using estimate_token_count()
    
    # Typical instruction sizes (approximate)
    INSTRUCTION_ESTIMATES = {
        # Base system instructions
        "base_instructions": 5000,
        # Tool use instructions
        "tool_instructions": 3000,
        # Code formatting instructions
        "code_instructions": 2000,
        # Context handling instructions
        "context_instructions": 1500,
    }
    
    # Calculate total instruction tokens
    total_instructions = sum(INSTRUCTION_ESTIMATES.values())
    
    # Count how many models are enabled (for now, just the current one)
    # In a multi-model scenario, you'd track which models are actually enabled
    enabled_models = [current_model]
    
    return {
        "total_instruction_tokens": total_instructions,
        "enabled_models": len(enabled_models),
        "total_models": len(available_models),
        "per_model_cost": total_instructions,
        "breakdown": INSTRUCTION_ESTIMATES,
        "models": enabled_models
    }


@router.get("/status")
async def get_mcp_status():
    """
    Get the status of all MCP servers.
    
    Returns:
        Dictionary with MCP server status information
    """
    try:
        # Check if MCP is enabled
        if not os.environ.get("ZIYA_ENABLE_MCP", "true").lower() in ("true", "1", "yes"):
            return {
                "initialized": False,
                "disabled": True,
                "message": "MCP is disabled. Use --mcp flag to enable MCP integration.",
                "servers": {},
                "config_path": None
            }
            
        mcp_manager = get_mcp_manager()
        
        if not mcp_manager.is_initialized:
            return {
                "initialized": False,
                "servers": {},
                "config_path": mcp_manager.config_path
            }
        
        status = mcp_manager.get_server_status()
        config_info = mcp_manager.get_config_search_info()
        server_configs = mcp_manager.server_configs
        
        # Add dynamic tools as a virtual server - only when tools are actually active
        from app.mcp.dynamic_tools import get_dynamic_loader
        dynamic_loader = get_dynamic_loader()
        active_dynamic_tools = dynamic_loader.get_active_tools()
        tool_triggers = dynamic_loader.get_all_triggers()
        available_tools_info = dynamic_loader.get_available_tools_info()
        
        # Only show on-demand tools section when tools are actually loaded
        if active_dynamic_tools and len(active_dynamic_tools) > 0:
            # Build detailed descriptions with trigger information
            tool_details = []
            for tool_name, tool_instance in active_dynamic_tools.items():
                # Get tool display name and trigger info
                triggers = tool_triggers.get(tool_name, [])
                trigger_desc = ", ".join(triggers) if triggers else "unknown trigger"
                
                # Get a friendly name for the tool
                if tool_name == "analyze_pcap":
                    friendly_name = "PCAP Network Analyzer"
                else:
                    # Default: capitalize and clean up underscores
                    friendly_name = tool_name.replace('_', ' ').title()
                
                tool_details.append({
                    "name": friendly_name,
                    "tool_name": tool_name,
                    "triggers": triggers,
                    "trigger_desc": trigger_desc
                })
            
            # Add on-demand tools as a virtual server entry
            status["ondemand"] = {
                "connected": True,
                "resources": 0,
                "tools": len(active_dynamic_tools),
                "prompts": 0,
                "capabilities": {},
                "builtin": True,
                "is_ondemand": True,  # Special flag to identify this as on-demand
                "tool_details": tool_details  # Include detailed trigger information
            }
            
            # Add to server_configs
            server_configs["ondemand"] = {
                "enabled": True,
                "description": "On-demand Tools",
                "builtin": True,
                "is_ondemand": True,
                "tool_details": tool_details,
                "available_tools": available_tools_info
            }
        
        # Calculate token costs for each server (including disabled ones)
        from app.mcp.permissions import get_permissions_manager
        permissions_manager = get_permissions_manager()
        permissions = permissions_manager.get_permissions()
        
        server_token_costs = {}
        total_tool_tokens = 0
        enabled_tool_tokens = 0
        
        for server_name, client in mcp_manager.clients.items():
            is_enabled = server_configs.get(server_name, {}).get("enabled", True)
            if client.is_connected:
                # Convert tools to dict format for token counting
                tools_dict = [
                    {
                        'name': tool.name,
                        'description': tool.description or '',
                        'inputSchema': tool.inputSchema
                    }
                    for tool in client.tools
                ]
                token_count = count_server_tool_tokens(tools_dict)
                server_token_costs[server_name] = token_count
                total_tool_tokens += token_count
                
                # Calculate enabled tool tokens by filtering out disabled tools
                if is_enabled:
                    server_perms = permissions.get('servers', {}).get(server_name, {})
                    enabled_tools_dict = []
                    
                    for tool in client.tools:
                        tool_perms = server_perms.get('tools', {}).get(tool.name, {})
                        tool_permission = tool_perms.get('permission', permissions.get('defaults', {}).get('tool', 'enabled'))
                        
                        # Only count enabled tools
                        if tool_permission != 'disabled':
                            enabled_tools_dict.append({
                                'name': tool.name,
                                'description': tool.description or '',
                                'inputSchema': tool.inputSchema
                            })
                    
                    # Count tokens only for enabled tools
                    enabled_token_count = count_server_tool_tokens(enabled_tools_dict)
                    enabled_tool_tokens += enabled_token_count
                    
                    logger.debug(f"Server {server_name}: {len(enabled_tools_dict)}/{len(tools_dict)} tools enabled, {enabled_token_count}/{token_count} tokens")
        
        # Add dynamic tools to token calculation
        if active_dynamic_tools:
            # Calculate token cost for active dynamic tools
            dynamic_tools_dict = [
                {
                    'name': tool_instance.name,
                    'description': tool_instance.description,
                    'inputSchema': tool_instance.InputSchema.schema()
                }
                for tool_instance in active_dynamic_tools.values()
            ]
            
            dynamic_token_count = count_server_tool_tokens(dynamic_tools_dict)
            server_token_costs["ondemand"] = dynamic_token_count
            total_tool_tokens += dynamic_token_count
            enabled_tool_tokens += dynamic_token_count
            
            logger.debug(f"Dynamic tools: {len(active_dynamic_tools)} tools, {dynamic_token_count} tokens")
        
        # Calculate instruction token costs
        instruction_costs = calculate_model_instruction_tokens()
        
        return {
            "initialized": True,
            "servers": status,
            "total_servers": len(status),
            "connected_servers": sum(1 for s in status.values() if s["connected"]),
            "config_path": config_info["config_path"],
            "config_exists": config_info["config_exists"],
            "config_search_paths": config_info["search_paths"],
            "server_configs": server_configs,
            "token_costs": {
                "servers": server_token_costs,
                "total_tool_tokens": total_tool_tokens,
                "enabled_tool_tokens": enabled_tool_tokens,
                "instructions": instruction_costs
            }
        }
        
    except Exception as e:
        logger.error(f"Error getting MCP status: {e}")
        raise HTTPException(status_code=500, detail=f"Error getting MCP status: {str(e)}")


@router.get("/resources")
async def get_mcp_resources():
    """
    Get all available MCP resources.
    
    Returns:
        List of MCP resources from all connected servers
    """
    try:
        # Check if MCP is enabled
        if not os.environ.get("ZIYA_ENABLE_MCP", "true").lower() in ("true", "1", "yes"):
            return {"resources": [], "disabled": True}
            
        mcp_manager = get_mcp_manager()
        
        if not mcp_manager.is_initialized:
            return {"resources": []}
        
        resources = mcp_manager.get_all_resources()
        
        return {
            "resources": [
                {
                    "uri": resource.uri,
                    "name": resource.name,
                    "description": resource.description,
                    "mimeType": resource.mimeType,
                    "server": getattr(resource, 'server', 'unknown')
                }
                for resource in resources
            ]
        }
        
    except Exception as e:
        logger.error(f"Error getting MCP resources: {e}")
        raise HTTPException(status_code=500, detail=f"Error getting MCP resources: {str(e)}")


@router.get("/tools")
async def get_mcp_tools():
    """
    Get all available MCP tools.
    
    Returns:
        List of MCP tools from all connected servers
    """
    try:
        # Check if MCP is enabled
        if not os.environ.get("ZIYA_ENABLE_MCP", "true").lower() in ("true", "1", "yes"):
            return {"tools": [], "disabled": True}
        
        # Add detailed status information for debugging
        mcp_manager = get_mcp_manager()
        status = mcp_manager.get_server_status()
        connected_servers = [name for name, info in status.items() if info["connected"]]
        logger.info(f"MCP tools request - Connected servers: {connected_servers}")
        logger.info(f"MCP tools request - Server details: {status}")
            
        mcp_manager = get_mcp_manager()
        
        if not mcp_manager.is_initialized:
            logger.warning("MCP manager not initialized when tools requested")
            return {"tools": []}
        
        tools = mcp_manager.get_all_tools()
        logger.info(f"Retrieved {len(tools)} tools for frontend")
        
        return {
            "tools": [
                {
                    "name": tool.name,
                    "description": tool.description,
                    "inputSchema": tool.inputSchema,
                    "server": getattr(tool, '_server_name', 'unknown')
                }
                for tool in tools
            ],
            "debug_info": {
                "total_tools": len(tools),
                "connected_servers": len([c for c in mcp_manager.clients.values() if c.is_connected]),
                "server_status": status
            }
        }
        
    except Exception as e:
        logger.error(f"Error getting MCP tools: {e}")
        raise HTTPException(status_code=500, detail=f"Error getting MCP tools: {str(e)}")


@router.get("/prompts")
async def get_mcp_prompts():
    """
    Get all available MCP prompts.
    
    Returns:
        List of MCP prompts from all connected servers
    """
    try:
        # Check if MCP is enabled
        if not os.environ.get("ZIYA_ENABLE_MCP", "true").lower() in ("true", "1", "yes"):
            return {"prompts": [], "disabled": True}
            
        mcp_manager = get_mcp_manager()
        
        if not mcp_manager.is_initialized:
            return {"prompts": []}
        
        prompts = mcp_manager.get_all_prompts()
        
        return {
            "prompts": [
                {
                    "name": prompt.name,
                    "description": prompt.description,
                    "arguments": prompt.arguments,
                    "server": getattr(prompt, 'server', 'unknown')
                }
                for prompt in prompts
            ]
        }
        
    except Exception as e:
        logger.error(f"Error getting MCP prompts: {e}")
        raise HTTPException(status_code=500, detail=f"Error getting MCP prompts: {str(e)}")


@router.post("/initialize")
async def initialize_mcp():
    """
    Initialize or reinitialize the MCP manager.
    
    Returns:
        Success status and connection results
    """
    try:
        # Check if MCP is enabled
        if not os.environ.get("ZIYA_ENABLE_MCP", "true").lower() in ("true", "1", "yes"):
            return {
                "success": False,
                "message": "MCP is disabled. Use --mcp flag to enable MCP integration."
            }
            
        mcp_manager = get_mcp_manager()
        logger.info("MCP reinitialize requested - will re-search for config files")
        
        # Shutdown existing connections
        if mcp_manager.is_initialized:
            await mcp_manager.shutdown()
        
        # Initialize with current configuration
        success = await mcp_manager.initialize()
        
        if success:
            status = mcp_manager.get_server_status()
            return {
                "success": True,
                "message": "MCP manager initialized successfully",
                "servers": status
            }
        else:
            return {
                "success": False,
                "message": "MCP manager initialization failed"
            }
            
    except Exception as e:
        logger.error(f"Error initializing MCP: {e}")
        raise HTTPException(status_code=500, detail=f"Error initializing MCP: {str(e)}")

@router.get("/shell-config")
async def get_shell_config():
    """
    Get current shell configuration from the running MCP manager.
    
    Returns:
        Current shell server configuration
    """
    try:
        # Check if MCP is enabled
        if not os.environ.get("ZIYA_ENABLE_MCP", "true").lower() in ("true", "1", "yes"):
            return {
                "enabled": False,
                "disabled": True,
                "message": "MCP is disabled. Use --mcp flag to enable MCP integration."
            }
            
        mcp_manager = get_mcp_manager()
        
        if not mcp_manager.is_initialized:
            return {
                **get_default_shell_config(),
                "enabled": False
            }
        
        # Check if shell server is connected
        shell_client = mcp_manager.clients.get("shell")
        if shell_client and shell_client.is_connected:
            # Get current server config directly from the MCP manager
            server_config = mcp_manager.server_configs.get("shell", {})
            server_env = server_config.get("env", {})
            
            # Extract allowed commands from environment configuration
            # Use environment commands if present, otherwise use defaults
            allowed_commands = DEFAULT_SHELL_CONFIG["allowedCommands"].copy()
            
            if "ALLOW_COMMANDS" in server_env and server_env["ALLOW_COMMANDS"].strip():
                env_commands = [cmd.strip() for cmd in server_env["ALLOW_COMMANDS"].split(",") if cmd.strip()]
                if env_commands:
                    allowed_commands = env_commands
                    logger.info(f"Using environment override commands: {allowed_commands}")
            
            # Extract git operations from environment or use defaults  
            git_operations_enabled = server_env.get("GIT_OPERATIONS_ENABLED", "true").lower() in ("true", "1", "yes")
            git_operations = DEFAULT_SHELL_CONFIG["safeGitOperations"].copy()
            if "SAFE_GIT_OPERATIONS" in server_env:
                git_operations = [op.strip() for op in server_env["SAFE_GIT_OPERATIONS"].split(",") if op.strip()]
            
            # Extract timeout from environment
            timeout = int(server_env.get("COMMAND_TIMEOUT", DEFAULT_SHELL_CONFIG["timeout"]))
            
            return {
                "enabled": True,
                "allowedCommands": allowed_commands,
                "gitOperationsEnabled": git_operations_enabled,
                "safeGitOperations": git_operations,
                "timeout": timeout
            }
        else:
            # Shell server not connected, but check the actual server config
            # to see if it's supposed to be enabled
            default_config = get_default_shell_config()
            timeout = int(os.environ.get("COMMAND_TIMEOUT", default_config["timeout"]))
            
            # Check if shell server is in server_configs and what its enabled state is
            server_config = mcp_manager.server_configs.get("shell", {})
            configured_enabled = server_config.get("enabled", True)
            
            return {
                **default_config,
                "enabled": configured_enabled,
                "connected": False,  # Add this to distinguish config vs connection state
                "timeout": timeout
            }
        
    except Exception as e:
        logger.error(f"Error getting shell config: {e}")
        raise HTTPException(status_code=500, detail=f"Error getting shell config: {str(e)}")
 
@router.post("/shell-config")
async def update_shell_config(config: ShellConfig):
    """
    Update shell configuration and restart the shell server.
    If persist=true, also saves the configuration to ~/.ziya/mcp_config.json.
    """
    try:
        # Check if MCP is enabled
        if not os.environ.get("ZIYA_ENABLE_MCP", "true").lower() in ("true", "1", "yes"):
            return {
                "success": False,
                "message": "MCP is disabled. Use --mcp flag to enable MCP integration."
            }
            
        mcp_manager = get_mcp_manager()
        if not mcp_manager:
            return {"success": False, "message": "MCP manager not initialized"}
        
        # Update the server config to reflect the enabled state
        # This is the critical missing piece that prevents reconnection!
        if "shell" in mcp_manager.server_configs:
            mcp_manager.server_configs["shell"]["enabled"] = config.enabled
            logger.info(f"Updated shell server enabled state to: {config.enabled}")
        
        # Create new shell server configuration
        new_shell_config = {
            "command": "python",
            "args": ["-u", "app/mcp_servers/shell_server.py"],
            "enabled": config.enabled,
            "builtin": True,  # Preserve builtin flag
            "description": "Provides shell command execution",
            "env": {
                "ALLOW_COMMANDS": ",".join(config.allowedCommands),
                "GIT_OPERATIONS_ENABLED": "true" if config.gitOperationsEnabled else "false",
                "SAFE_GIT_OPERATIONS": ",".join(config.safeGitOperations),
                "COMMAND_TIMEOUT": str(config.timeout)
            }
        }
        
        # Handle persistence to config file if requested
        if config.persist:
            try:
                from pathlib import Path
                
                # Use the standard config path
                config_path = Path.home() / ".ziya" / "mcp_config.json"
                config_path.parent.mkdir(parents=True, exist_ok=True)
                
                # Load existing config or create new one
                if config_path.exists():
                    with open(config_path, 'r') as f:
                        mcp_config = json.load(f)
                else:
                    mcp_config = {"mcpServers": {}}
                
                # Ensure mcpServers section exists
                if "mcpServers" not in mcp_config:
                    mcp_config["mcpServers"] = {}
                
                # Update or create shell server config
                if "shell" not in mcp_config["mcpServers"]:
                    # Get the base command and script path
                    import sys
                    from pathlib import Path
                    
                    # Find the shell_server.py script using the same logic as builtin definitions
                    try:
                        import app.mcp_servers
                        package_dir = Path(app.mcp_servers.__file__).parent
                        shell_script_path = str(package_dir / "shell_server.py")
                    except ImportError:
                        shell_script_path = "app/mcp_servers/shell_server.py"  # Fallback to relative
                    
                    mcp_config["mcpServers"]["shell"] = {
                        "command": sys.executable,
                        "args": ["-u", shell_script_path],
                        "enabled": config.enabled,
                        "description": "Shell command execution server"
                    }
                
                # Update the env section with new configuration
                if "env" not in mcp_config["mcpServers"]["shell"]:
                    mcp_config["mcpServers"]["shell"]["env"] = {}
                
                mcp_config["mcpServers"]["shell"]["env"]["ALLOW_COMMANDS"] = ",".join(config.allowedCommands)
                mcp_config["mcpServers"]["shell"]["env"]["GIT_OPERATIONS_ENABLED"] = "true" if config.gitOperationsEnabled else "false"
                mcp_config["mcpServers"]["shell"]["env"]["SAFE_GIT_OPERATIONS"] = ",".join(config.safeGitOperations)
                mcp_config["mcpServers"]["shell"]["env"]["COMMAND_TIMEOUT"] = str(config.timeout)
                mcp_config["mcpServers"]["shell"]["enabled"] = config.enabled
                
                # Save back to file
                with open(config_path, 'w') as f:
                    json.dump(mcp_config, f, indent=2)
                
                logger.info(f"Persisted shell configuration to {config_path}")
                persist_message = f" Configuration saved to {config_path} and will persist between sessions."
                
            except Exception as persist_error:
                logger.error(f"Failed to persist configuration: {persist_error}")
                persist_message = f" Warning: Failed to save to config file: {persist_error}"
        else:
            persist_message = ""
        
        if config.enabled:
            # Update the server configuration in MCP manager before restarting
            mcp_manager.server_configs["shell"] = new_shell_config
            
            # Restart the shell server with new configuration
            logger.info(f"Attempting to restart shell server with config: {new_shell_config}")
            success = await mcp_manager.restart_server("shell", new_shell_config)
            
            if not success:
                # Get detailed error information
                shell_client = mcp_manager.clients.get("shell")
                error_details = "Unknown error"
                if shell_client and shell_client.process:
                    try:
                        stderr_output = shell_client.process.stderr.read() if shell_client.process.stderr else ""
                        if stderr_output:
                            error_details = f"Shell server stderr: {stderr_output}"
                    except Exception as e:
                        error_details = f"Could not read shell server error: {str(e)}"
                
                logger.error(f"Shell server restart failed. Details: {error_details}")
                return {"success": False, "message": f"Failed to restart shell server: {error_details}"}
            
            if success:
                logger.info(f"Shell server restarted with new config: {config.allowedCommands}")
                
                # Invalidate tools cache to ensure fresh tool list with updated shell tools
                mcp_manager.invalidate_tools_cache()
                
                return {
                    "success": True, 
                    "message": f"Shell server updated for this session. Basic commands: {', '.join(config.allowedCommands[:5])}{'...' if len(config.allowedCommands) > 5 else ''}, Git operations: {'enabled' if config.gitOperationsEnabled else 'disabled'}.{persist_message}"
                }
        else:
            # Disable shell server by disconnecting it
            if "shell" in mcp_manager.clients:
                await mcp_manager.clients["shell"].disconnect()
                del mcp_manager.clients["shell"]
                logger.info("Shell server client disconnected")
            
            # Critical: Update the server config to prevent reconnection
            if "shell" in mcp_manager.server_configs:
                mcp_manager.server_configs["shell"]["enabled"] = False
                logger.info("Shell server marked as disabled in server configs")
            
            # Invalidate tools cache to ensure fresh tool list without shell tools
            mcp_manager.invalidate_tools_cache()
            
            return {"success": True, "message": f"Shell server disabled for this session.{persist_message}"}
        
    except Exception as e:
        logger.error(f"Error updating shell config: {e}")
        raise HTTPException(status_code=500, detail=f"Error updating shell config: {str(e)}")

@router.post("/toggle-server")
async def toggle_server(request: ServerToggleRequest):
    """
    Enable or disable a specific MCP server.
    """
    try:
        # Check if MCP is enabled
        if not os.environ.get("ZIYA_ENABLE_MCP", "true").lower() in ("true", "1", "yes"):
            return {
                "success": False,
                "message": "MCP is disabled. Use --mcp flag to enable MCP integration."
            }
            
        mcp_manager = get_mcp_manager()

        # Add debug logging
        logger.info(f"Toggle server request: {request.server_name} -> {request.enabled}")
        logger.info(f"MCP manager initialized: {mcp_manager.is_initialized}")
        logger.info(f"Available server configs: {list(mcp_manager.server_configs.keys()) if mcp_manager.server_configs else 'None'}")
        logger.info(f"Server config for {request.server_name}: {mcp_manager.server_configs.get(request.server_name) if mcp_manager.server_configs else 'None'}")
        
        if not mcp_manager.is_initialized:
            return {"success": False, "message": "MCP manager not initialized"}
        
        # Update the server config
        if request.server_name in mcp_manager.server_configs:
            mcp_manager.server_configs[request.server_name]["enabled"] = request.enabled
            logger.info(f"Updated {request.server_name} server enabled state to: {request.enabled}")

        # Ensure shell server config exists
        if request.server_name == "shell" and request.server_name not in mcp_manager.server_configs:
            logger.warning("Shell server config missing, creating default config")
            mcp_manager.server_configs["shell"] = mcp_manager.builtin_server_definitions.get("shell", {})
        
        if request.enabled:
            # Restart the server with current configuration
            server_config = mcp_manager.server_configs.get(request.server_name)
            if server_config:
                success = await mcp_manager.restart_server(request.server_name, server_config)
                message = f"{request.server_name} server enabled and restarted" if success else f"Failed to restart {request.server_name} server"
                
                # Invalidate tools cache to ensure fresh tool list with newly enabled server tools
                if success:
                    mcp_manager.invalidate_tools_cache()
            else:
                message = f"No configuration found for {request.server_name} server"
        else:
            # Disable server by disconnecting it
            if request.server_name in mcp_manager.clients:
                await mcp_manager.clients[request.server_name].disconnect()
                del mcp_manager.clients[request.server_name]
                logger.info(f"{request.server_name} server disabled")
            
            # Update server config to mark as disabled
            if request.server_name in mcp_manager.server_configs:
                mcp_manager.server_configs[request.server_name]["enabled"] = False
            
            # Invalidate tools cache to ensure fresh tool list without disabled server tools
            mcp_manager.invalidate_tools_cache()
            
            message = f"{request.server_name} server disabled"
        
        return {"success": True, "message": message}
        
    except Exception as e:
        logger.error(f"Error toggling server {request.server_name}: {e}")
        raise HTTPException(status_code=500, detail=f"Error toggling server: {str(e)}")

@router.get("/permissions")
async def get_mcp_permissions():
    """Get MCP permission settings."""
    manager = get_permissions_manager()
    return manager.get_permissions()

@router.post("/permissions/server")
async def update_server_permission(request: ServerPermissionUpdateRequest):
    """Update permission for a specific server."""
    manager = get_permissions_manager()
    manager.update_server_permission(request.server_name, request.permission)
    return {"success": True, "message": f"Server '{request.server_name}' {request.permission}."}

@router.post("/permissions/tool")
async def update_tool_permission(request: ToolPermissionUpdateRequest):
    """Update permission for a specific tool on a server."""
    manager = get_permissions_manager()
    manager.update_tool_permission(request.server_name, request.tool_name, request.permission)
    return {"success": True, "message": f"Permission for tool '{request.tool_name}' on server '{request.server_name}' updated."}

@router.get("/servers/{server_name}/details")
async def get_mcp_server_details(server_name: str):
    """Get details (tools, resources, prompts) for a specific MCP server."""
    try:
        mcp_manager = get_mcp_manager()
        
        # Handle dynamic tools virtual server
        if server_name == "ondemand" or server_name == "dynamic":
            from app.mcp.dynamic_tools import get_dynamic_loader
            dynamic_loader = get_dynamic_loader()
            active_tools = dynamic_loader.get_active_tools()
            
            return {
                "tools": [
                    {
                        "name": tool.name,
                        "description": tool.description,
                        "inputSchema": tool.InputSchema.schema()
                    }
                    for tool in active_tools.values()
                ],
                "resources": [],
                "prompts": [],
                "logs": ["Dynamic tools loaded based on file selection"]
            }
        
        mcp_manager = get_mcp_manager()
        if not mcp_manager.is_initialized or server_name not in mcp_manager.clients:
            raise HTTPException(status_code=404, detail="Server not found or not initialized")
        
        client = mcp_manager.clients[server_name]
        
        return {
            "tools": [asdict(tool) for tool in client.tools],
            "resources": [asdict(resource) for resource in client.resources],
            "prompts": [asdict(prompt) for prompt in client.prompts],
            "logs": client.logs if hasattr(client, 'logs') else [],
        }
    except Exception as e:
        logger.error(f"Error getting details for server {server_name}: {e}")
        raise HTTPException(status_code=500, detail=f"Error getting server details: {str(e)}")


# ============================================================================
# MCP REGISTRY ROUTES
# ============================================================================

@router.get("/registry/providers")
async def get_registry_providers():
    """Get list of available registry providers."""
    from datetime import datetime
    import asyncio
    try:
        # Force initialize providers like CLI does
        from app.mcp.registry.registry import initialize_registry_providers, get_provider_registry
        initialize_registry_providers()
        manager = get_registry_manager()
        provider_ids = manager.get_available_providers()
        
        registry = get_provider_registry()
        
        # Load enabled/disabled state from config (would be stored in a config file)
        # For now, assume all are enabled
        enabled_providers = set()  # This would come from a config file
        
        providers = []
        
        for provider_id in provider_ids:
            provider = registry.get_provider(provider_id)
            if provider:
                # Calculate basic stats with timeout and better error handling
                try:
                    logger.info(f"Fetching services from provider {provider_id}...")
                    
                    # Add timeout to prevent hanging requests
                    services_result = await asyncio.wait_for(
                        provider.list_services(max_results=1000), 
                        timeout=30.0
                    )
                    service_count = len(services_result['services'])
                    logger.info(f"Provider {provider_id} successfully returned {service_count} services")
                    
                except asyncio.TimeoutError:
                    logger.warning(f"Provider {provider_id} timed out after 30 seconds")
                    service_count = 0
                except Exception as e:
                    logger.error(f"Provider {provider_id} failed: {type(e).__name__}: {e}")
                    # Only log full traceback for unexpected errors, not network issues
                    if not any(keyword in str(e).lower() for keyword in ['timeout', 'connection', 'network', 'unreachable']):
                        logger.exception(f"Unexpected error from provider {provider_id}:")
                    service_count = 0
                
                providers.append({
                    'id': provider.identifier,
                    'name': provider.name,
                    'isInternal': provider.is_internal,
                    'supportsSearch': provider.supports_search,
                    'enabled': provider_id not in enabled_providers if enabled_providers else True,
                    'stats': {
                        'totalServices': service_count,
                        'lastError': service_count == 0,  # Flag providers that failed
                        'lastFetched': datetime.now().isoformat()
                    }
                })
        
        return {'providers': providers}
        
    except Exception as e:
        logger.error(f"Error getting registry providers: {e}")
        raise HTTPException(status_code=500, detail=str(e))


class ToggleRegistryRequest(BaseModel):
    model_config = {"extra": "allow"}
    provider_id: str
    enabled: bool


@router.post("/registry/providers/toggle")
async def toggle_registry_provider(request: ToggleRegistryRequest):
    """Enable or disable a registry provider."""
    try:
        # This would update a configuration file or database
        # For now, just return success (the actual implementation would 
        # store this state and use it when initializing providers)
        
        logger.info(f"Registry {request.provider_id} {'enabled' if request.enabled else 'disabled'}")
        
        # In a real implementation, this would:
        # 1. Update a config file with enabled/disabled providers
        # 2. Restart the registry aggregator to pick up changes
        # 3. Clear any cached data from disabled providers
        
        return {
            'success': True,
            'provider_id': request.provider_id,
            'enabled': request.enabled,
            'message': f"Registry {request.provider_id} {'enabled' if request.enabled else 'disabled'}"
        }
        
    except Exception as e:
        logger.error(f"Error toggling registry provider: {e}")
        raise HTTPException(status_code=500, detail=str(e))


class AddRegistryRequest(BaseModel):
    model_config = {"extra": "allow"}
    name: str
    baseUrl: str
    authType: str = 'none'
    authToken: Optional[str] = None
    authUsername: Optional[str] = None
    authPassword: Optional[str] = None


@router.post("/registry/providers/add")
async def add_custom_registry(request: AddRegistryRequest):
    """Add a custom registry provider."""
    try:
        # This would create a new custom registry provider
        # For now, just return an error indicating it's not implemented
        
        return {
            'success': False,
            'error': 'Custom registry addition not yet implemented',
            'message': 'This feature will be available in a future update'
        }
        
    except Exception as e:
        logger.error(f"Error adding custom registry: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/registry/providers/{provider_id}/refresh")
async def refresh_registry_provider(provider_id: str):
    """Refresh a specific registry provider's data."""
    try:
        from app.mcp.registry.registry import get_provider_registry
        
        registry = get_provider_registry()
        provider = registry.get_provider(provider_id)
        
        if not provider:
            raise HTTPException(status_code=404, detail=f"Provider {provider_id} not found")
        
        # Force refresh by calling list_services with a flag
        # This would clear any caches and fetch fresh data
        result = await provider.list_services(max_results=1000)
        
        return {
            'success': True,
            'provider_id': provider_id,
            'services_count': len(result['services']),
            'message': f"Registry {provider_id} refreshed successfully"
        }
        
    except HTTPException:
        raise
    except Exception as e:
        error_str = str(e)
        # Handle auth errors gracefully
        if 'NotAuthorizedException' in error_str or 'Not Authorized' in error_str:
            logger.warning(f"Registry {provider_id} access not available (permissions required)")
            return {
                'success': False,
                'provider_id': provider_id,
                'services_count': 0,
                'message': f"Registry {provider_id} requires additional permissions"
            }
        logger.error(f"Error refreshing registry provider: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/registry/services")
async def get_registry_services(
    max_results: int = 2000,  # Increased to handle large registries
    provider_filter: Optional[str] = None
):
    """Get available services from all registries."""
    try:
        manager = get_registry_manager()
        
        # Parse provider filter
        providers = provider_filter.split(',') if provider_filter else None
        
        services = await manager.get_available_services(
            max_results=max_results,
            provider_filter=providers
        )
        
        # Add builtin tools as internal registry services
        builtin_services = []
        
        # Add time server as builtin
        builtin_services.append({
            'serviceId': 'builtin_time',
            'serviceName': 'Time Server',
            'serviceDescription': 'Provides current date and time information • Always available',
            'supportLevel': 'Recommended',
            'status': 'stable',
            'version': 1,
            'installationType': 'builtin',
            'createdAt': '2024-01-01T00:00:00.000Z',
            'lastUpdatedAt': '2024-01-01T00:00:00.000Z',
            'securityReviewLink': None,
            'repositoryUrl': None,
            'tags': ['builtin', 'utility', 'time'],
            'author': 'Ziya Team',
            'provider': {
                'id': 'builtin',
                'name': 'Ziya Builtin',
                'isInternal': True,
                'availableIn': ['builtin']
            },
            'downloadCount': 0,
            'starCount': 0,
            '_builtin_server': 'time',
            '_dependencies_available': True,
            '_available_tools': ['get_current_time']
        })
        
        # Add shell server as builtin
        builtin_services.append({
            'serviceId': 'builtin_shell',
            'serviceName': 'Shell Command Server',
            'serviceDescription': 'Execute shell commands with configurable safety controls • Configurable command allowlist and git operations',
            'supportLevel': 'Recommended',
            'status': 'stable',
            'version': 1,
            'installationType': 'builtin',
            'createdAt': '2024-01-01T00:00:00.000Z',
            'lastUpdatedAt': '2024-01-01T00:00:00.000Z',
            'securityReviewLink': None,
            'repositoryUrl': None,
            'tags': ['builtin', 'shell', 'commands', 'git'],
            'author': 'Ziya Team',
            'provider': {
                'id': 'builtin',
                'name': 'Ziya Builtin',
                'isInternal': True,
                'availableIn': ['builtin']
            },
            'downloadCount': 0,
            'starCount': 0,
            '_builtin_server': 'shell',
            '_dependencies_available': True,
            '_available_tools': ['run_shell_command']
        })
        
        try:
            from app.mcp.builtin_tools import BUILTIN_TOOL_CATEGORIES, get_builtin_tools_for_category, check_pcap_dependencies
            
            for category, config in BUILTIN_TOOL_CATEGORIES.items():
                # Skip hidden categories
                if config.get("hidden", False):
                    continue
                    
                # Check dependencies
                dependencies_available = True
                if category == "pcap_analysis":
                    dependencies_available = check_pcap_dependencies()
                
                # Get available tools
                tool_classes = get_builtin_tools_for_category(category)
                tool_names = []
                if dependencies_available:
                    try:
                        tool_names = [tool_class().name for tool_class in tool_classes]
                    except (AttributeError, TypeError, ValueError):
                        tool_names = []
                
                builtin_service = {
                    'serviceId': f'builtin_{category}',
                    'serviceName': config['name'],
                    'serviceDescription': config['description'] + f" • {len(tool_names)} tools available" if tool_names else config['description'] + " • Dependencies required",
                    'supportLevel': 'Recommended',  # Builtin tools are recommended
                    'status': 'stable',
                    'version': 1,
                    'installationType': 'builtin',
                    'createdAt': '2024-01-01T00:00:00.000Z',
                    'lastUpdatedAt': '2024-01-01T00:00:00.000Z',
                    'securityReviewLink': None,
                    'repositoryUrl': None,
                    'tags': ['builtin', 'enterprise', 'internal'],
                    'author': 'Ziya Team',
                    'provider': {
                        'id': 'builtin',
                        'name': 'Ziya Builtin',
                        'isInternal': True,
                        'availableIn': ['builtin']
                    },
                    'downloadCount': 0,
                    'starCount': 0,
                    '_builtin_category': category,
                    '_dependencies_available': dependencies_available,
                    '_available_tools': tool_names
                }
                builtin_services.append(builtin_service)
        except ImportError:
            pass
        
        # Convert external services to JSON-serializable format
        services_data = []
        
        # Handle builtin services
        for service in builtin_services:
            services_data.append(service)  # Already in correct format
        
        # Handle external services  
        for service in services:
            if hasattr(service, 'provider_metadata'):
                sources = service.provider_metadata.get('available_in', 
                    [service.provider_metadata.get('provider_id')])
            else:
                sources = ['unknown']
                
            sources = [s for s in sources if s]
            
            services_data.append({
                'serviceId': service.service_id,
                'serviceName': service.service_name,
                'serviceDescription': service.service_description,
                'supportLevel': service.support_level.value if hasattr(service.support_level, 'value') else service.support_level,
                'status': service.status.value if hasattr(service.status, 'value') else service.status,
                'version': service.version,
                'installationType': service.installation_type.value if hasattr(service.installation_type, 'value') else service.installation_type,
                'createdAt': service.created_at.isoformat() if hasattr(service.created_at, 'isoformat') else service.created_at,
                'lastUpdatedAt': service.last_updated_at.isoformat() if hasattr(service.last_updated_at, 'isoformat') else service.last_updated_at,
                'securityReviewLink': getattr(service, 'security_review_url', None),
                'repositoryUrl': getattr(service, 'repository_url', None),
                'tags': getattr(service, 'tags', []),
                'author': getattr(service, 'author', None),
                'provider': {
                    'id': service.provider_metadata.get('provider_id') if hasattr(service, 'provider_metadata') else 'unknown',
                    'name': sources[0] if sources else 'unknown',
                    'isInternal': service.provider_metadata.get('is_internal', False) if hasattr(service, 'provider_metadata') else False,
                    'availableIn': sources
                },
                'downloadCount': getattr(service, 'download_count', 0),
                'starCount': getattr(service, 'star_count', 0)
            })
        
        return {'services': services_data, 'total': len(services_data)}
        
    except Exception as e:
        logger.error(f"Error getting registry services: {e}")
        raise HTTPException(status_code=500, detail=str(e))


class ToolSearchRequest(BaseModel):
    model_config = {"extra": "allow"}
    query: str
    maxTools: int = 20
    providers: Optional[List[str]] = None


@router.post("/registry/tools/search")
async def search_registry_tools(request: ToolSearchRequest):
    """Search for tools across registries."""
    try:
        manager = get_registry_manager()
        
        results = await manager.search_services_by_tools(
            query=request.query,
            provider_filter=request.providers
        )
        
        # Convert to JSON-serializable format
        results_data = []
        for result in results:
            service = result.service
            sources = service.provider_metadata.get('available_in',
                [service.provider_metadata.get('provider_id')])
            sources = [s for s in sources if s]
            
            results_data.append({
                'service': {
                    'serviceId': service.service_id,
                    'serviceName': service.service_name,
                    'serviceDescription': service.service_description,
                    'supportLevel': service.support_level.value,
                    'installationType': service.installation_type.value,
                    'tags': service.tags,
                    'provider': {
                        'id': service.provider_metadata.get('provider_id'),
                        'availableIn': sources
                    }
                },
                'matchingTools': [
                    {
                        'toolName': tool.tool_name,
                        'mcpServerId': tool.service_id,
                        'description': tool.description
                    }
                    for tool in result.matching_tools
                ],
                'relevanceScore': result.relevance_score
            })
        
        return {'results': results_data, 'total': len(results_data)}
        
    except Exception as e:
        logger.error(f"Error searching tools: {e}")
        raise HTTPException(status_code=500, detail=str(e))


class InstallServiceRequest(BaseModel):
    model_config = {"extra": "allow"}
    service_id: str
    provider_id: Optional[str] = None


@router.post("/registry/services/install")
async def install_registry_service(request: InstallServiceRequest):
    """Install a service from the registry."""
    try:
        manager = get_registry_manager()
        
        result = await manager.install_service(
            service_id=request.service_id,
            provider_id=request.provider_id
        )
        
        return result
        
    except Exception as e:
        logger.error(f"Error installing service: {e}")
        raise HTTPException(status_code=500, detail=str(e))


class UninstallServiceRequest(BaseModel):
    model_config = {"extra": "allow"}
    server_name: str


@router.post("/registry/services/uninstall")
async def uninstall_registry_service(request: UninstallServiceRequest):
    """Uninstall a registry service."""
    try:
        manager = get_registry_manager()
        
        result = await manager.uninstall_service(request.server_name)
        
        return result
        
    except Exception as e:
        logger.error(f"Error uninstalling service: {e}")
        raise HTTPException(status_code=500, detail=str(e))


        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# Builtin Tools API endpoints
class BuiltinToolToggleRequest(BaseModel):
    model_config = {"extra": "allow"}
    """Request model for toggling builtin tools."""
    category: str
    enabled: bool


@router.get("/builtin-tools/status")
async def get_builtin_tools_status():
    """Get status of all builtin tool categories."""
    try:
        from app.mcp.builtin_tools import (
            BUILTIN_TOOL_CATEGORIES,
            is_builtin_category_enabled,
            get_builtin_tools_for_category,
            check_pcap_dependencies
        )
        
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


@router.post("/builtin-tools/toggle")
async def toggle_builtin_tool_category(request: BuiltinToolToggleRequest):
    """Enable or disable a builtin tool category."""
    try:
        from app.mcp.builtin_tools import BUILTIN_TOOL_CATEGORIES
        
        if request.category not in BUILTIN_TOOL_CATEGORIES:
            raise HTTPException(status_code=400, detail=f"Unknown builtin tool category: {request.category}")
        
        # Set environment variable to persist the setting
        env_var = f"ZIYA_ENABLE_{request.category.upper()}"
        os.environ[env_var] = "true" if request.enabled else "false"
        
        # Clear the MCP tools cache to force reload
        try:
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
@router.get("/registry/services/installed")
async def get_installed_registry_services():
    """Get list of installed registry services."""
    try:
        manager = get_registry_manager()
        mcp_manager = get_mcp_manager()
        
        # Get services that were explicitly installed via registry
        registry_installed_services = manager.get_installed_services()
        
        # Build a set of service IDs that are already accounted for
        accounted_service_ids = set()
        for svc in registry_installed_services:
            accounted_service_ids.add(svc.get('serviceId'))
        
        services = list(registry_installed_services)
        
        # Add builtin MCP servers (time, shell) if they're enabled
        if mcp_manager.is_initialized:
            # Check time server
            if 'time' in mcp_manager.clients:
                time_client = mcp_manager.clients['time']
                if time_client.is_connected:
                    accounted_service_ids.add('builtin_time')
                    services.append({
                        'serverName': 'time',
                        'serviceId': 'builtin_time',
                        'serviceName': 'Time Server',
                        'enabled': True,
                        'provider': {'id': 'builtin', 'name': 'Ziya Builtin'}
                    })
            
            # Check shell server
            if 'shell' in mcp_manager.clients:
                shell_client = mcp_manager.clients['shell']
                shell_config = mcp_manager.server_configs.get('shell', {})
                is_enabled = shell_config.get('enabled', True)
                if shell_client.is_connected or is_enabled:
                    accounted_service_ids.add('builtin_shell')
                    services.append({
                        'serverName': 'shell',
                        'serviceId': 'builtin_shell',
                        'serviceName': 'Shell Command Server',
                        'enabled': is_enabled,
                        'provider': {'id': 'builtin', 'name': 'Ziya Builtin'}
                    })
            
            # Now correlate any other MCP servers with registry entries
            # Get all available services from registry for correlation
            try:
                all_registry_services = await manager.get_available_services(max_results=10000)
                
                # Build correlation map: normalize server names to match registry entries
                from app.mcp.registry.aggregator import RegistryAggregator
                aggregator = RegistryAggregator()
                
                for server_name, client in mcp_manager.clients.items():
                    # Skip if already accounted for
                    if any(s.get('serverName') == server_name for s in services):
                        continue
                    
                    if not client.is_connected:
                        continue
                    
                    # Try to correlate with a registry entry using fingerprinting
                    server_config = mcp_manager.server_configs.get(server_name, {})
                    
                    # Try to match by looking for common patterns
                    matched_service = None
                    for registry_service in all_registry_services:
                        # Match by name similarity or repository
                        service_name_lower = registry_service.service_name.lower().replace('-', '_').replace(' ', '_')
                        server_name_lower = server_name.lower().replace('-', '_')
                        
                        if (service_name_lower in server_name_lower or 
                            server_name_lower in service_name_lower or
                            (registry_service.repository_url and server_config.get('repository_url') and 
                             registry_service.repository_url == server_config.get('repository_url'))):
                            matched_service = registry_service
                            break
                    
                    if matched_service and matched_service.service_id not in accounted_service_ids:
                        accounted_service_ids.add(matched_service.service_id)
                        services.append({
                            'serverName': server_name,
                            'serviceId': matched_service.service_id,
                            'serviceName': matched_service.service_name,
                            'version': matched_service.version,
                            'supportLevel': matched_service.support_level.value if hasattr(matched_service.support_level, 'value') else matched_service.support_level,
                            'installedAt': None,
                            'enabled': server_config.get('enabled', True),
                            'provider': {
                                'id': matched_service.provider_metadata.get('provider_id') if hasattr(matched_service, 'provider_metadata') else 'unknown',
                                'name': matched_service.provider_metadata.get('provider_id') if hasattr(matched_service, 'provider_metadata') else 'Unknown',
                                'isInternal': matched_service.provider_metadata.get('is_internal', False) if hasattr(matched_service, 'provider_metadata') else False,
                                'availableIn': matched_service.provider_metadata.get('available_in', []) if hasattr(matched_service, 'provider_metadata') else []
                            },
                            'installationType': matched_service.installation_type.value if hasattr(matched_service.installation_type, 'value') else matched_service.installation_type,
                            'repositoryUrl': matched_service.repository_url if hasattr(matched_service, 'repository_url') else None,
                            'securityReviewLink': matched_service.security_review_url if hasattr(matched_service, 'security_review_url') else None,
                            'serviceDescription': matched_service.service_description,
                            '_manually_configured': True  # Flag to indicate this wasn't installed via registry
                        })
                
            except Exception as e:
                logger.warning(f"Error correlating MCP servers with registry: {e}")
        
        return {'services': services, 'total': len(services)}
        
    except Exception as e:
        logger.error(f"Error getting installed services: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/registry/services/{service_id}/preview")
async def get_service_preview(service_id: str, provider_id: Optional[str] = None):
    """Get preview of a service without installing it."""
    try:
        from app.mcp.registry.registry import get_provider_registry
        
        registry = get_provider_registry()
        
        # Find the provider
        if provider_id:
            provider = registry.get_provider(provider_id)
        else:
            # Search all providers
            providers = registry.get_available_providers(include_internal=True)
            provider = None
            for p in providers:
                try:
                    service = await p.get_service_detail(service_id)
                    provider = p
                    break
                except Exception:
                    continue
        
        if not provider:
            raise HTTPException(status_code=404, detail=f"Service {service_id} not found")
        
        # Get service detail
        service = await provider.get_service_detail(service_id)
        
        # Get installation preview
        preview = await provider.get_installation_preview(service_id)
        
        return {
            'serviceId': service.service_id,
            'serviceName': service.service_name,
            'serviceDescription': service.service_description,
            'supportLevel': service.support_level.value,
            'installationType': service.installation_type.value,
            'repositoryUrl': service.repository_url,
            'tags': service.tags,
            'installationInstructions': service.installation_instructions,
            'preview': preview,
            'requiredEnvVars': service.installation_instructions.get('env_vars', []),
            'securityReviewUrl': service.security_review_url
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting service preview: {e}")
        raise HTTPException(status_code=500, detail=str(e))


class FavoritesRequest(BaseModel):
    model_config = {"extra": "allow"}
    favorites: List[str]


@router.post("/registry/favorites")
async def update_favorites(request: FavoritesRequest):
    """Update user's favorite services."""
    # Store in user config (for now, in a simple JSON file)
    config_path = Path.home() / ".ziya" / "registry_favorites.json"
    config_path.parent.mkdir(exist_ok=True)
    
    with open(config_path, 'w') as f:
        json.dump({'favorites': request.favorites}, f)
    
    return {'success': True, 'favorites': request.favorites}


@router.get("/registry/favorites")
async def get_favorites():
    """Get user's favorite services."""
    config_path = Path.home() / ".ziya" / "registry_favorites.json"
    
    if config_path.exists():
        with open(config_path, 'r') as f:
            data = json.load(f)
            return {'favorites': data.get('favorites', [])}
    
    return {'favorites': []}
