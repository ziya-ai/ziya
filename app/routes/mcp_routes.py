"""
API routes for MCP (Model Context Protocol) management.
"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
import os
import json
from typing import Dict, List, Any, Optional

from app.mcp.manager import get_mcp_manager
from app.utils.logging_utils import logger
from app.config.shell_config import DEFAULT_SHELL_CONFIG, get_default_shell_config

router = APIRouter(prefix="/api/mcp", tags=["mcp"])


class MCPServerConfig(BaseModel):
    name: str
    command: List[str]
    args: Optional[List[str]] = None
    env: Optional[Dict[str, str]] = None
    enabled: bool = True

class ShellConfig(BaseModel):
    enabled: bool = DEFAULT_SHELL_CONFIG["enabled"]
    allowedCommands: List[str] = DEFAULT_SHELL_CONFIG["allowedCommands"]
    gitOperationsEnabled: bool = DEFAULT_SHELL_CONFIG["gitOperationsEnabled"]
    safeGitOperations: List[str] = DEFAULT_SHELL_CONFIG["safeGitOperations"]
    timeout: int = DEFAULT_SHELL_CONFIG["timeout"]

class ServerToggleRequest(BaseModel):
    server_name: str
    enabled: bool

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
        
        return {
            "initialized": True,
            "servers": status,
            "total_servers": len(status),
            "connected_servers": sum(1 for s in status.values() if s["connected"]),
            "config_path": config_info["config_path"],
            "config_exists": config_info["config_exists"],
            "config_search_paths": config_info["search_paths"],
            "server_configs": {name: {"enabled": config.get("enabled", True)} for name, config in server_configs.items()}
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
            allowed_commands = DEFAULT_SHELL_CONFIG["allowedCommands"].copy()
            if "ALLOW_COMMANDS" in server_env:
                # If environment override exists, use it instead
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
                "gitOperationsEnabled": git_operations_enabled,
                "safeGitOperations": git_operations
            }
        else:
            # Shell server not connected, return default config with enabled=False
            return {
                **get_default_shell_config(),
                "enabled": False
            }
        
    except Exception as e:
        logger.error(f"Error getting shell config: {e}")
        raise HTTPException(status_code=500, detail=f"Error getting shell config: {str(e)}")
 
@router.post("/shell-config")
async def update_shell_config(config: ShellConfig):
    """
    Update shell configuration and restart the shell server instantly.
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
            "command": ["python", "-u", "mcp_servers/shell_server.py"],
            "enabled": config.enabled,
            "env": {
                "ALLOW_COMMANDS": ",".join(config.allowedCommands),
                "GIT_OPERATIONS_ENABLED": "true" if config.gitOperationsEnabled else "false",
                "SAFE_GIT_OPERATIONS": ",".join(config.safeGitOperations),
                "COMMAND_TIMEOUT": str(config.timeout)
            }
        }
        
        if config.enabled:
            # Restart the shell server with new configuration
            success = await mcp_manager.restart_server("shell", new_shell_config)
            
            if success:
                logger.info(f"Shell server restarted with new config: {config.allowedCommands}")
                return {
                    "success": True, 
                    "message": f"Shell server updated instantly. Basic commands: {', '.join(config.allowedCommands)}, Git operations: {'enabled' if config.gitOperationsEnabled else 'disabled'}"
                }
            else:
                return {"success": False, "message": "Failed to restart shell server"}
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
            
            return {"success": True, "message": "Shell server disabled instantly"}
        
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
            else:
                message = f"No configuration found for {request.server_name} server"
        else:
            # Disable server by disconnecting it
            if request.server_name in mcp_manager.clients:
                await mcp_manager.clients[request.server_name].disconnect()
                del mcp_manager.clients[request.server_name]
                logger.info(f"{request.server_name} server disabled")
            message = f"{request.server_name} server disabled"
        
        return {"success": True, "message": message}
        
    except Exception as e:
        logger.error(f"Error toggling server {request.server_name}: {e}")
        raise HTTPException(status_code=500, detail=f"Error toggling server: {str(e)}")
 
