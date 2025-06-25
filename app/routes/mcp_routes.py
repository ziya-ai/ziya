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

router = APIRouter(prefix="/api/mcp", tags=["mcp"])


class MCPServerConfig(BaseModel):
    name: str
    command: List[str]
    args: Optional[List[str]] = None
    env: Optional[Dict[str, str]] = None
    enabled: bool = True

class ShellConfig(BaseModel):
    enabled: bool = True
    allowedCommands: List[str] = ["ls", "cat", "pwd", "grep", "wc", "touch", "find", "date"]
    gitOperationsEnabled: bool = True
    safeGitOperations: List[str] = ["status", "log", "show", "diff", "branch", "remote", "ls-files", "blame"]
    timeout: int = 10


@router.get("/status")
async def get_mcp_status():
    """
    Get the status of all MCP servers.
    
    Returns:
        Dictionary with MCP server status information
    """
    try:
        # Check if MCP is enabled
        if not os.environ.get("ZIYA_ENABLE_MCP", "false").lower() in ("true", "1", "yes"):
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
        
        return {
            "initialized": True,
            "servers": status,
            "total_servers": len(status),
            "connected_servers": sum(1 for s in status.values() if s["connected"]),
            "config_path": config_info["config_path"],
            "config_exists": config_info["config_exists"],
            "config_search_paths": config_info["search_paths"]
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
        if not os.environ.get("ZIYA_ENABLE_MCP", "false").lower() in ("true", "1", "yes"):
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
        if not os.environ.get("ZIYA_ENABLE_MCP", "false").lower() in ("true", "1", "yes"):
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
        if not os.environ.get("ZIYA_ENABLE_MCP", "false").lower() in ("true", "1", "yes"):
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
        if not os.environ.get("ZIYA_ENABLE_MCP", "false").lower() in ("true", "1", "yes"):
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
        if not os.environ.get("ZIYA_ENABLE_MCP", "false").lower() in ("true", "1", "yes"):
            return {
                "enabled": False,
                "disabled": True,
                "message": "MCP is disabled. Use --mcp flag to enable MCP integration."
            }
            
        mcp_manager = get_mcp_manager()
        
        if not mcp_manager.is_initialized:
            return {
                "enabled": False,
                "allowedCommands": ["ls", "cat", "pwd", "grep", "wc", "touch", "find", "date"],
                "gitOperationsEnabled": True,
                "safeGitOperations": ["status", "log", "show", "diff", "branch", "remote", "ls-files", "blame"],
                "timeout": 10
            }
        
        # Check if shell server is connected
        shell_client = mcp_manager.clients.get("shell")
        if shell_client and shell_client.is_connected:
            # Try to get the current configuration from the server config
            # For built-in servers, we can check the original config
            current_config = None
            if hasattr(shell_client, 'server_config'):
                current_config = shell_client.server_config
            
            allowed_commands = ["ls", "cat", "pwd", "grep", "wc", "touch", "find", "date"]
            if current_config and "env" in current_config and "ALLOW_COMMANDS" in current_config["env"]:
                allowed_commands = [cmd.strip() for cmd in current_config["env"]["ALLOW_COMMANDS"].split(",") if cmd.strip()]
            
            # Extract git operations from environment or use defaults
            git_operations = ["status", "log", "show", "diff", "branch", "remote", "ls-files", "blame"]
            if current_config and "env" in current_config and "SAFE_GIT_OPERATIONS" in current_config["env"]:
                git_operations = [op.strip() for op in current_config["env"]["SAFE_GIT_OPERATIONS"].split(",") if op.strip()]
            
            return {
                "enabled": True,
                "allowedCommands": allowed_commands,
                "gitOperationsEnabled": True,
                "safeGitOperations": git_operations,
                "timeout": 10
            }
        else:
            return {
                "enabled": False,
                "allowedCommands": ["ls", "cat", "pwd", "grep", "wc", "touch", "find", "date"],
                "gitOperationsEnabled": True,
                "safeGitOperations": ["status", "log", "show", "diff", "branch", "remote", "ls-files", "blame"],
                "timeout": 10
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
        if not os.environ.get("ZIYA_ENABLE_MCP", "false").lower() in ("true", "1", "yes"):
            return {
                "success": False,
                "message": "MCP is disabled. Use --mcp flag to enable MCP integration."
            }
            
        mcp_manager = get_mcp_manager()
        
        if not mcp_manager.is_initialized:
            return {"success": False, "message": "MCP manager not initialized"}
        
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
                logger.info("Shell server disabled")
            
            return {"success": True, "message": "Shell server disabled instantly"}
        
    except Exception as e:
        logger.error(f"Error updating shell config: {e}")
        raise HTTPException(status_code=500, detail=f"Error updating shell config: {str(e)}")
 
