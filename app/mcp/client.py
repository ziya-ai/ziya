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
        self._last_successful_call = time.time()
        self._last_reconnect_attempt = 0  # Rate limit reconnections
        
    async def connect(self) -> bool:
        """
        Connect to the MCP server.
        
        Returns:
            bool: True if connection successful, False otherwise
        """
        try:
            # Start the MCP server process
            command = self.server_config.get("command", [])
            if isinstance(command, str):
                command = [command]
            if not command:
                logger.error(f"No command specified for MCP server: {self.server_config.get('name', 'unknown')}")
                return False
            
            # Use the preserved user codebase directory instead of current working directory
            # The current working directory may have changed during module imports
            working_dir = os.environ.get("ZIYA_USER_CODEBASE_DIR")
            if not working_dir:
                working_dir = os.getcwd()
                logger.warning(f"ZIYA_USER_CODEBASE_DIR not set, using current directory: {working_dir}")
            
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

            self.process = await asyncio.create_subprocess_exec(
                *final_popen_command,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=working_dir,
                 env=full_env,
                 limit=1024 * 1024  # 1MB buffer limit for large tool lists
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
                
                # Initialize successful call timestamp on connection
                self._last_successful_call = time.time()
                
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
                        # Terminate the process to ensure that reading from stdout/stderr does not block indefinitely.
                        self.process.terminate()
                        try:
                            await asyncio.wait_for(self.process.wait(), timeout=1.0)
                        except asyncio.TimeoutError:
                            self.process.kill()
                            await self.process.wait()

                        stdout_output_bytes = await self.process.stdout.read() if self.process.stdout else b""
                        stderr_output_bytes = await self.process.stderr.read() if self.process.stderr else b""
                        stdout_output = stdout_output_bytes.decode('utf-8', errors='ignore')
                        stderr_output = stderr_output_bytes.decode('utf-8', errors='ignore')
                        if stdout_output:
                            logger.error(f"MCP server stdout: {stdout_output}")
                        if stderr_output:
                            logger.error(f"MCP server stderr: {stderr_output}")
                    except Exception as e:
                        logger.error(f"Error reading MCP server output: {e}")
                    finally:
                        # Ensure we clean up the process and connection state
                        self.process = None
                        self.is_connected = False
                return False
                
        except Exception as e:
            logger.error(f"Error connecting to MCP server: {str(e)}")
            self.is_connected = False
            await self.disconnect()
            return False
    
    async def disconnect(self):
        """Disconnect from the MCP server."""
        if self.process:
            try:
                self.process.terminate()
                await asyncio.wait_for(self.process.wait(), timeout=5)
            except asyncio.TimeoutError:
                self.process.kill()
                await self.process.wait()
            except Exception as e:
                logger.error(f"Error disconnecting from MCP server: {str(e)}")
            finally:
                self.process = None
                self.is_connected = False
    
    def _is_process_healthy(self) -> bool:
        """Check if the MCP server process is still healthy."""
        if not self.process:
            return False
        
        # Check if process is still running
        if self.process.returncode is not None:
            logger.warning(f"MCP server process has terminated with code: {self.process.returncode}")
            self.is_connected = False
            return False
        
        # Only check if process is actually running, not based on call timeouts
        # A server shouldn't be marked unhealthy just because it hasn't been used recently
        return True
        
    async def _send_request(self, method: str, params: Optional[Dict[str, Any]] = None, _retry_count: int = 0) -> Optional[Dict[str, Any]]:
        """Send a JSON-RPC request to the MCP server."""
        # Standardized error response structure
        def create_error_response(message: str, code: int = -32000) -> Dict[str, Any]:
            return {
                "error": True,
                "message": message,
                "code": code
            }
        
        import time
        start_time = time.time()
        
        max_retries = 3
        
        if not self.process or not self.process.stdin:
            return create_error_response("No active process or stdin not available")
            
        # Check process health before sending request
        if not self._is_process_healthy():
            # Rate limit reconnection attempts to prevent runaway processes
            now = time.time()
            if now - self._last_reconnect_attempt < 30:  # Wait 30 seconds between attempts
                logger.warning("Process unhealthy, but reconnection rate limited")
                return create_error_response("Process unhealthy and reconnection rate limited")
                
            logger.warning("Process unhealthy, attempting reconnection")
            self._last_reconnect_attempt = now
            if await self.connect():
                logger.info("Reconnection successful, retrying request")
            else:
                logger.error("Reconnection failed")
                return create_error_response("Reconnection failed")
            
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
            write_start = time.time()
            self.process.stdin.write(request_json.encode('utf-8'))
            await self.process.stdin.drain()
            write_time = time.time() - write_start
            logger.debug(f"🔍 MCP_TIMING: Write took {write_time*1000:.1f}ms")
            
            # Read response with a timeout
            try:
                read_start = time.time()
                response_line_bytes = await asyncio.wait_for(
                    self.process.stdout.readline(),
                    timeout=30.0
                )
                read_time = time.time() - read_start
                logger.debug(f"🔍 MCP_TIMING: Read took {read_time*1000:.1f}ms")
                
                if not response_line_bytes:
                    # EOF, process likely terminated
                    logger.error("No response from MCP server (EOF)")
                    if self.process.returncode is not None:
                        logger.error(f"MCP server process has terminated with code: {self.process.returncode}")
                        # Try to read any remaining output
                        try:
                            remaining_output_bytes = await self.process.stdout.read()
                            if remaining_output_bytes:
                                logger.error(f"Remaining output: {remaining_output_bytes.decode('utf-8', errors='ignore')}")
                        except:
                            pass
                    return create_error_response("No response from MCP server (EOF)")
                
                response_line = response_line_bytes.decode('utf-8')

            except asyncio.TimeoutError:
                logger.error(f"Timeout waiting for response from MCP server for method '{method}'")
                return {
                    "error": True,
                    "message": f"Request timed out after 30 seconds for method '{method}'",
                    "code": -32000 # Custom timeout error code
                }
            except Exception as e:
                logger.error(f"Error reading from MCP server: {e}")
                return create_error_response(f"Error reading from MCP server: {str(e)}")

            response = json.loads(response_line.strip())

            if "error" in response:
                error_info = response['error']
                error_code = error_info.get("code", -1)
                error_message = str(error_info.get("message", "Unknown error"))
                
                # Check if this is a timeout error and we haven't exhausted retries
                is_timeout = (error_code == -32603 and 
                             ("timed out" in error_message.lower() or "timeout" in error_message.lower()))
                
                # Timeouts should fail immediately to let the model choose a lighter alternative
                if not is_timeout and _retry_count < max_retries:
                    logger.error(f"MCP server error: {error_info}")
                    # Only retry non-timeout errors
                    await asyncio.sleep(0.5)
                    return await self._send_request(method, params, _retry_count + 1)
                
                # Log all errors (timeouts fail immediately, others after retries)
                logger.error(f"MCP server {'timeout' if is_timeout else 'error'}: {error_info}")
                
                # Create error result
                return {
                    "error": True,
                    "message": error_message,
                    "code": error_code
                }
                
            # Update successful call timestamp
            self._last_successful_call = time.time()
            total_time = time.time() - start_time
            logger.debug(f"🔍 MCP_TIMING: Total request took {total_time*1000:.1f}ms for method '{method}'")
            return response.get("result")
            
        except Exception as e:
            logger.error(f"Error sending MCP request: {str(e)}")
            return create_error_response(f"Error sending MCP request: {str(e)}")
    
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
            self.process.stdin.write(notification_json.encode('utf-8'))
            await self.process.stdin.drain()
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
            if "resources" in self.capabilities:
                resources_result = await self._send_request("resources/list")
                if resources_result and "resources" in resources_result:
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
                elif resources_result is None:
                    logger.warning(f"Failed to get a valid response for resources/list from {server_name_for_log}")
                else:
                    logger.warning(f"No 'resources' key in response from {server_name_for_log}: {resources_result}")
            
            # Load tools
            logger.info(f"Server {server_name_for_log} capabilities: {self.capabilities}")
            logger.info(f"Checking tools capability: {self.capabilities.get('tools')}")
            if "tools" in self.capabilities:
                logger.info(f"Calling tools/list for {server_name_for_log}")
                tools_result = await self._send_request("tools/list")
                logger.debug(f"Tools list response from {server_name_for_log}: {tools_result}")
                if tools_result and "tools" in tools_result:
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
                elif tools_result is None:
                    logger.warning(f"Failed to get a valid response for tools/list from {server_name_for_log}")
                else:
                    logger.warning(f"No 'tools' key in response from {server_name_for_log}: {tools_result}")
            
            # Load prompts
            logger.info(f"Loading prompts for server: {server_name_for_log}")
            if "prompts" in self.capabilities:
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
                elif prompts_result is None:
                    logger.warning(f"Failed to get a valid response for prompts/list from {server_name_for_log}")
                else:
                    logger.warning(f"No 'prompts' key in response from {server_name_for_log}: {prompts_result}")

            logger.info(f"Loaded MCP capabilities for {server_name_for_log}: {len(self.resources)} resources, {len(self.tools)} tools, {len(self.prompts)} prompts")
            logger.debug(f"Tool names for {server_name_for_log}: {[tool.name for tool in self.tools]}")

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
