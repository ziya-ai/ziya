"""
MCP (Model Context Protocol) client implementation for Ziya.

This module provides the core MCP client functionality for connecting to
and interacting with MCP servers.
"""

import asyncio
import os
import json
import subprocess
import sys
from typing import Dict, List, Optional, Any, Union, AsyncGenerator
from dataclasses import dataclass, asdict
from pathlib import Path
import uuid
import time

from app.utils.logging_utils import logger


@dataclass
class MCPResource:
    """Represents an MCP resource."""
    uri: str
    name: str
    description: Optional[str] = None
    mimeType: Optional[str] = None
    

@dataclass
class MCPTool:
    """Represents an MCP tool."""
    name: str
    description: str
    inputSchema: Dict[str, Any]
    

@dataclass
class MCPPrompt:
    """Represents an MCP prompt template."""
    name: str
    description: str
    arguments: List[Dict[str, Any]]


class MCPClient:
    """
    MCP client for communicating with MCP servers.
    
    This client handles the JSON-RPC communication protocol used by MCP
    and provides high-level methods for interacting with MCP servers.
    """
    
    def __init__(self, server_config: Dict[str, Any]):
        """
        Initialize the MCP client.
        
        Args:
            server_config: Configuration for the MCP server
        """
        self.server_config = server_config
        self.process: Optional[subprocess.Popen] = None
        self.request_id = 0
        self.is_connected = False
        self.capabilities: Dict[str, Any] = {}
        self.resources: List[MCPResource] = []
        self.tools: List[MCPTool] = []
        self.prompts: List[MCPPrompt] = []
        
    async def connect(self) -> bool:
        """
        Connect to the MCP server.
        
        Returns:
            bool: True if connection successful, False otherwise
        """
        try:
            # Start the MCP server process
            command = self.server_config.get("command", [])
            if not command:
                logger.error(f"No command specified for MCP server: {self.server_config.get('name', 'unknown')}")
                return False
            
            logger.info(f"Starting MCP server with command: {command}")
                
            logger.info(f"Starting MCP server: {' '.join(command)}")
            
            # Set working directory to project root (parent of app directory)
            app_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            project_root = os.path.dirname(app_dir)
            working_dir = project_root if os.path.exists(os.path.join(project_root, 'mcp_servers')) else os.getcwd()
            
            logger.info(f"Using working directory: {working_dir}")
            
            self.process = subprocess.Popen(
                command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,  # Keep stderr separate for debugging
                cwd=working_dir,  # Set working directory to project root
                text=True,
                bufsize=0
            )
            
            # Initialize the connection
            init_result = await self._send_request("initialize", {
                "protocolVersion": "2024-11-05",
                "capabilities": {
                    "roots": {
                        "listChanged": True
                    },
                    "sampling": {}
                },
                "clientInfo": {
                    "name": "ziya",
                    "version": "1.0.0"
                }
            })
            
            if init_result:
                self.capabilities = init_result.get("capabilities", {})
                self.is_connected = True
                
                # Send initialized notification
                await self._send_notification("notifications/initialized")
                
                # Load available resources, tools, and prompts
                await self._load_server_capabilities()
                
                logger.info(f"Successfully connected to MCP server: {self.server_config.get('name', 'unknown')}")
                return True
            else:
                logger.error(f"Failed to initialize MCP server connection: {self.server_config.get('name', 'unknown')}")
                # Log any available output for debugging
                if self.process:
                    try:
                        stdout_output = self.process.stdout.read() if self.process.stdout else ""
                        stderr_output = self.process.stderr.read() if self.process.stderr else ""
                        if stdout_output:
                            logger.error(f"MCP server stdout: {stdout_output}")
                        if stderr_output:
                            logger.error(f"MCP server stderr: {stderr_output}")
                    except Exception as e:
                        logger.error(f"Error reading MCP server output: {e}")
                return False
                
        except Exception as e:
            logger.error(f"Error connecting to MCP server: {str(e)}")
            await self.disconnect()
            return False
    
    async def disconnect(self):
        """Disconnect from the MCP server."""
        if self.process:
            try:
                self.process.terminate()
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait()
            except Exception as e:
                logger.error(f"Error disconnecting from MCP server: {str(e)}")
            finally:
                self.process = None
                self.is_connected = False
    
    async def _send_request(self, method: str, params: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
        """Send a JSON-RPC request to the MCP server."""
        if not self.process or not self.process.stdin:
            return None
            
        self.request_id += 1
        request = {
            "jsonrpc": "2.0",
            "id": self.request_id,
            "method": method
        }
        
        if params:
            request["params"] = params
            
        try:
            request_json = json.dumps(request) + "\n"
            self.process.stdin.write(request_json)
            self.process.stdin.flush()
            
            # Read response
            try:
                response_line = self.process.stdout.readline()
            except Exception as e:
                logger.error(f"Error reading from MCP server: {e}")
                return None
                
            if not response_line:
                logger.error("No response from MCP server")
                # Check if process is still running
                if self.process.poll() is not None:
                    logger.error(f"MCP server process has terminated with code: {self.process.returncode}")
                    # Try to read any remaining output
                    try:
                        remaining_output = self.process.stdout.read()
                        if remaining_output:
                            logger.error(f"Remaining output: {remaining_output}")
                    except:
                        pass
                return None
                
            response = json.loads(response_line.strip())
            
            if "error" in response:
                logger.error(f"MCP server error: {response['error']}")
                return None
                
            return response.get("result")
            
        except Exception as e:
            logger.error(f"Error sending MCP request: {str(e)}")
            return None
    
    async def _send_notification(self, method: str, params: Optional[Dict[str, Any]] = None):
        """Send a JSON-RPC notification to the MCP server."""
        if not self.process or not self.process.stdin:
            return
            
        notification = {
            "jsonrpc": "2.0",
            "method": method
        }
        
        if params:
            notification["params"] = params
            
        try:
            notification_json = json.dumps(notification) + "\n"
            self.process.stdin.write(notification_json)
            self.process.stdin.flush()
        except Exception as e:
            logger.error(f"Error sending MCP notification: {str(e)}")
    
    async def _load_server_capabilities(self):
        """Load resources, tools, and prompts from the MCP server."""
        try:
            # Load resources
            if self.capabilities.get("resources"):
                resources_result = await self._send_request("resources/list")
                if resources_result and "resources" in resources_result:
                    self.resources = [
                        MCPResource(**resource) for resource in resources_result["resources"]
                    ]
            
            # Load tools
            if self.capabilities.get("tools"):
                tools_result = await self._send_request("tools/list")
                if tools_result and "tools" in tools_result:
                    self.tools = [
                        MCPTool(**tool) for tool in tools_result["tools"]
                    ]
            
            # Load prompts
            if self.capabilities.get("prompts"):
                prompts_result = await self._send_request("prompts/list")
                if prompts_result and "prompts" in prompts_result:
                    self.prompts = [
                        MCPPrompt(**prompt) for prompt in prompts_result["prompts"]
                    ]
                    
            logger.info(f"Loaded MCP capabilities: {len(self.resources)} resources, {len(self.tools)} tools, {len(self.prompts)} prompts")
            
        except Exception as e:
            logger.error(f"Error loading MCP server capabilities: {str(e)}")
    
    async def get_resource(self, uri: str) -> Optional[str]:
        """Get the content of a resource by URI."""
        try:
            result = await self._send_request("resources/read", {"uri": uri})
            if result and "contents" in result:
                contents = result["contents"]
                if contents and len(contents) > 0:
                    return contents[0].get("text", "")
            return None
        except Exception as e:
            logger.error(f"Error getting MCP resource {uri}: {str(e)}")
            return None
    
    async def call_tool(self, name: str, arguments: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Call a tool on the MCP server."""
        try:
            result = await self._send_request("tools/call", {
                "name": name,
                "arguments": arguments
            })
            return result
        except Exception as e:
            logger.error(f"Error calling MCP tool {name}: {str(e)}")
            return None
    
    async def get_prompt(self, name: str, arguments: Optional[Dict[str, Any]] = None) -> Optional[str]:
        """Get a prompt template from the MCP server."""
        try:
            params = {"name": name}
            if arguments:
                params["arguments"] = arguments
                
            result = await self._send_request("prompts/get", params)
            if result and "messages" in result:
                # Combine all message content
                content_parts = []
                for message in result["messages"]:
                    if "content" in message:
                        if isinstance(message["content"], str):
                            content_parts.append(message["content"])
                        elif isinstance(message["content"], dict) and "text" in message["content"]:
                            content_parts.append(message["content"]["text"])
                return "\n".join(content_parts)
            return None
        except Exception as e:
            logger.error(f"Error getting MCP prompt {name}: {str(e)}")
            return None
