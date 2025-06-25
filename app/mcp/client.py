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
from dataclasses import dataclass, asdict, fields # Import fields
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
            
            # Use the current working directory where the program was started
            # This is more intuitive for users - commands execute where they launched the program
            working_dir = os.getcwd()
            app_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            project_root = os.path.dirname(app_dir)
            
            # For built-in servers, check multiple possible locations for script resolution
            possible_roots = [
                project_root,  # Development mode
                os.getcwd(),   # Current working directory
                os.path.dirname(os.path.dirname(app_dir)),  # Installed package mode
                os.path.dirname(app_dir)  # Package root (where mcp_servers would be alongside app/)
            ]
            
            logger.info(f"Starting MCP server '{self.server_config.get('name', 'unknown')}' with working directory: {working_dir}")

            # Resolve command paths
            resolved_command = []
            # This flag tracks if we've found any script to potentially update working_dir
            # based on the first script encountered.
            initial_script_found_for_working_dir_update = False

            for part in command:
                if part.endswith('.py') and not os.path.isabs(part):
                    # This part is a relative Python script, try to find it
                    current_part_resolved_path = part # Default if not found in special locations
                    found_this_part_in_roots = False
                    # Try to find the script in possible locations
                    for root in possible_roots:
                        potential_path = os.path.join(root, part)
                        if os.path.exists(potential_path):
                            current_part_resolved_path = potential_path
                            if not initial_script_found_for_working_dir_update: # Set working_dir based on the first script found
                                working_dir = root # This updates working_dir for Popen
                                initial_script_found_for_working_dir_update = True
                            found_this_part_in_roots = True
                            logger.info(f"Found MCP server script '{part}' at: {current_part_resolved_path}")
                            break # Found the script for this part
                    
                    if not found_this_part_in_roots:
                        # If not found in special locations, construct path relative to current `working_dir`
                        current_part_resolved_path = os.path.join(working_dir, part)
                        logger.warning(f"MCP server script '{part}' not found in special roots, using path relative to current CWD ({working_dir}): {current_part_resolved_path}")
                    resolved_command.append(current_part_resolved_path)
                else:
                    # This part is not a relative Python script (e.g., "node", "python", or an absolute path)
                    resolved_command.append(part)

            # Combine resolved command with arguments from server_config
            final_popen_command = list(resolved_command) # Start with the resolved executable and its direct flags
            server_specific_args = self.server_config.get("args", [])
            final_popen_command.extend(server_specific_args)

            logger.info(f"Starting MCP server with command: {' '.join(final_popen_command)}")

            logger.info(f"Using working directory: {working_dir}")

            # Get environment variables for the process
            process_env = self.server_config.get("env", {})
            full_env = os.environ.copy()
            full_env.update(process_env)

            self.process = subprocess.Popen(
                final_popen_command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,  # Keep stderr separate for debugging
                cwd=working_dir,  # Set working directory to project root
                text=True,
                env=full_env,
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
    
    async def _send_request(self, method: str, params: Optional[Dict[str, Any]] = None, _retry_count: int = 0) -> Optional[Dict[str, Any]]:
        """Send a JSON-RPC request to the MCP server."""
        max_retries = 3
        
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
                timeout_occurred = False
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
                error_info = response['error']
                error_code = error_info.get("code", -1)
                error_message = error_info.get("message", "Unknown MCP server error")
                
                # Check if this is a timeout error and we haven't exhausted retries
                is_timeout = (error_code == -32603 and 
                             ("timed out" in error_message.lower() or "timeout" in error_message.lower()))
                
                if is_timeout and _retry_count < max_retries:
                    logger.warning(f"MCP server timeout (attempt {_retry_count + 1}/{max_retries + 1}), retrying silently: {error_message}")
                    # Wait a brief moment before retry
                    await asyncio.sleep(0.5)
                    return await self._send_request(method, params, _retry_count + 1)
                
                # Log error (only after retries exhausted for timeouts)
                if is_timeout:
                    logger.error(f"MCP server timeout after {max_retries + 1} attempts: {error_info}")
                else:
                    logger.error(f"MCP server error: {error_info}")
                
                # Return error information so it can be displayed to the user
                return {
                    "error": True,
                    "message": error_message,
                    "code": error_code
                }
                
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
            mcp_resource_fields = {f.name for f in fields(MCPResource)}
            mcp_tool_fields = {f.name for f in fields(MCPTool)}
            mcp_prompt_fields = {f.name for f in fields(MCPPrompt)}

            server_name_for_log = self.server_config.get('name', 'unknown')

            # Load resources
            if self.capabilities.get("resources"):
                resources_result = await self._send_request("resources/list")
                if resources_result and "resources" in resources_result:
                    if server_name_for_log == 'everything' or 'everything' in str(server_name_for_log).lower():
                        logger.info(f"DEBUG_MCP_CLIENT ({server_name_for_log}): Raw resources data: {json.dumps(resources_result['resources'], indent=2)}")
                    valid_resources = []
                    for res_data in resources_result["resources"]:
                        if not isinstance(res_data, dict):
                            logger.warning(f"Skipping non-dict resource item from {server_name_for_log}: {res_data}")
                            continue
                        filtered_data = {k: v for k, v in res_data.items() if k in mcp_resource_fields}
                        try:
                            valid_resources.append(MCPResource(**filtered_data))
                        except TypeError as e:
                            logger.error(f"Failed to create MCPResource for server {server_name_for_log}, FILTERED data {filtered_data} (original: {res_data}): {e}")
                    self.resources = valid_resources
            
            # Load tools
            if self.capabilities.get("tools"):
                tools_result = await self._send_request("tools/list")
                logger.info(f"Tools list response from {server_name_for_log}: {tools_result}")
                if tools_result and "tools" in tools_result:
                    if server_name_for_log == 'everything' or 'everything' in str(server_name_for_log).lower():
                         logger.info(f"DEBUG_MCP_CLIENT ({server_name_for_log}): Raw tools data: {json.dumps(tools_result['tools'], indent=2)}")
                    valid_tools = []
                    for tool_data in tools_result["tools"]:
                        if not isinstance(tool_data, dict):
                            logger.warning(f"Skipping non-dict tool item from {server_name_for_log}: {tool_data}")
                            continue
                        filtered_data = {k: v for k, v in tool_data.items() if k in mcp_tool_fields}
                        try:
                            valid_tools.append(MCPTool(**filtered_data))
                        except TypeError as e:
                            logger.error(f"Failed to create MCPTool for server {server_name_for_log}, FILTERED data {filtered_data} (original: {tool_data}): {e}")
                    self.tools = valid_tools
                    logger.info(f"Successfully loaded {len(valid_tools)} tools for server {server_name_for_log}")
                else:
                    logger.warning(f"No tools found in response from {server_name_for_log}: {tools_result}")
            
            # Load prompts
            logger.info(f"Loading prompts for server: {server_name_for_log}")
            if self.capabilities.get("prompts"):
                prompts_result = await self._send_request("prompts/list")
                if prompts_result and "prompts" in prompts_result:
                    valid_prompts = []
                    for prompt_data in prompts_result["prompts"]:
                        if not isinstance(prompt_data, dict):
                            logger.warning(f"Skipping non-dict prompt item from {server_name_for_log}: {prompt_data}")
                            continue
                        filtered_data = {k: v for k, v in prompt_data.items() if k in mcp_prompt_fields}
                        try:
                            valid_prompts.append(MCPPrompt(**filtered_data))
                        except TypeError as e:
                            logger.error(f"Failed to create MCPPrompt for server {server_name_for_log}, FILTERED data {filtered_data} (original: {prompt_data}): {e}")
                    self.prompts = valid_prompts

            logger.info(f"Loaded MCP capabilities for {server_name_for_log}: {len(self.resources)} resources, {len(self.tools)} tools, {len(self.prompts)} prompts")
            logger.info(f"Tool names for {server_name_for_log}: {[tool.name for tool in self.tools]}")

        except Exception as e:
            logger.error(f"Error loading MCP server capabilities for {self.server_config.get('name', 'unknown')}: {str(e)}", exc_info=True)
    
    async def get_resource(self, uri: str) -> Optional[str]:
        """Get the content of a resource by URI."""
        try:
            result = await self._send_request("resources/read", {"uri": uri})
            if result and "contents" in result:
                contents = result["contents"]
                if contents and len(contents) > 0 and isinstance(contents[0], dict):
                    return contents[0].get("text", "")
            return None
        except Exception as e:
            logger.error(f"Error getting MCP resource {uri} from {self.server_config.get('name', 'unknown')}: {str(e)}")
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
            logger.error(f"Error calling MCP tool {name} on {self.server_config.get('name', 'unknown')}: {str(e)}")
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
                    if isinstance(message, dict) and "content" in message:
                        content = message["content"]
                        if isinstance(content, str):
                            content_parts.append(content)
                        elif isinstance(content, dict) and "text" in content:
                            content_parts.append(content["text"])
                return "\n".join(content_parts)
            return None
        except Exception as e:
            logger.error(f"Error getting MCP prompt {name} from {self.server_config.get('name', 'unknown')}: {str(e)}")
            return None
