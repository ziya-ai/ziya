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
        self.logs: List[str] = []  # Store server logs
        self._last_successful_call = time.time()
        self._last_reconnect_attempt = 0  # Rate limit reconnections
        
        # Rate limiting for tool calls
        self._tool_call_timestamps: Dict[str, float] = {}
        self._tool_rate_limits: Dict[str, float] = {}
        self._default_rate_limit: float = 2.0  # Default 2 seconds between consecutive calls
        
        # External server health monitoring
        self._consecutive_failures = 0
        self._last_health_check = 0
        
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
            
            # For registry-installed services without explicit command, generate from installation_path
            if not command and self.server_config.get("installation_path"):
                installation_path = self.server_config["installation_path"]
                logger.info(f"Attempting to generate command for registry service at: {installation_path}")
                self.logs.append(f"INFO: Looking for executable in {installation_path}")
                
                if not os.path.exists(installation_path):
                    logger.error(f"Installation path does not exist: {installation_path}")
                    self.logs.append(f"ERROR: Installation path does not exist: {installation_path}")
                else:
                    # Look for common executable patterns in the installation directory
                    import glob
                    
                    # Check for Python scripts
                    python_files = glob.glob(os.path.join(installation_path, "*.py"))
                    logger.info(f"Found Python files: {python_files}")
                    self.logs.append(f"INFO: Found Python files: {python_files}")
                    
                    if python_files:
                        # Use the first Python file found
                        command = ["python", python_files[0]]
                        logger.info(f"Generated command for registry service: {command}")
                        self.logs.append(f"INFO: Generated command: {command}")
                    else:
                        # Check for executable files
                        try:
                            files = os.listdir(installation_path)
                            logger.info(f"Files in installation directory: {files}")
                            self.logs.append(f"INFO: Files in directory: {files}")
                            
                            if not files:
                                self.logs.append("ERROR: Installation directory is empty - service may not have been properly installed")
                            
                            for file in files:
                                file_path = os.path.join(installation_path, file)
                                if os.path.isfile(file_path) and os.access(file_path, os.X_OK):
                                    command = [file_path]
                                    logger.info(f"Found executable for registry service: {command}")
                                    self.logs.append(f"INFO: Found executable: {command}")
                                    break
                        except Exception as e:
                            logger.error(f"Error listing installation directory: {e}")
                            self.logs.append(f"ERROR: Cannot list directory: {e}")
            
            if not command:
                logger.error(f"No command specified for MCP server: {self.server_config.get('name', 'unknown')}")
                self.logs.append("ERROR: No command specified in server configuration")
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
            
            # Start background task to capture logs
            asyncio.create_task(self._capture_logs())
            
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
                self.logs.append(f"ERROR: Failed to initialize MCP server connection")
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
                            self.logs.append(f"STDOUT: {stdout_output}")
                        if stderr_output:
                            logger.error(f"MCP server stderr: {stderr_output}")
                            self.logs.append(f"STDERR: {stderr_output}")
                        if not stdout_output and not stderr_output:
                            self.logs.append("ERROR: No output from server process")
                    except Exception as e:
                        logger.error(f"Error reading MCP server output: {e}")
                        self.logs.append(f"ERROR: Failed to read server output - {str(e)}")
                    finally:
                        # Ensure we clean up the process and connection state
                        self.process = None
                        self.is_connected = False
                return False
                
        except FileNotFoundError as e:
            logger.error(f"MCP server executable not found: {str(e)}")
            self.logs.append(f"ERROR: Executable not found - {str(e)}")
            self.is_connected = False
            return False
        except Exception as e:
            logger.error(f"Error connecting to MCP server: {str(e)}")
            self.logs.append(f"ERROR: Connection failed - {str(e)}")
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
    
    def _is_external_server_healthy(self) -> bool:
        """Check if external server is responding consistently."""
        server_name = self.server_config.get('name', 'unknown')
        
        # Allow higher failure tolerance for external servers
        if self._consecutive_failures >= 5:
            logger.warning(f"External server {server_name} has {self._consecutive_failures} consecutive failures")
            return False
        
        return True
    
    def _record_call_result(self, success: bool):
        """Record the result of a tool call for health monitoring."""
        if success:
            self._consecutive_failures = 0
            self._last_successful_call = time.time()
        else:
            self._consecutive_failures += 1
            
        logger.debug(f"MCP server health: {self._consecutive_failures} consecutive failures")
    
    def _is_process_healthy(self) -> bool:
        """Check if the MCP server process is still healthy."""
        if not self.process:
            return False
        
        # Check if process is still running
        if self.process.returncode is not None:
            logger.warning(f"MCP server process has terminated with code: {self.process.returncode}")
            self.is_connected = False
            return False
        
        # Additional health check for external servers
        server_name = self.server_config.get('name', 'unknown')
        if 'fetch' in server_name.lower() or 'external' in server_name.lower():
            return self._is_external_server_healthy()
        
        # Only check if process is actually running, not based on call timeouts
        # A server shouldn't be marked unhealthy just because it hasn't been used recently
        return True
        
    async def _send_request(self, method: str, params: Optional[Dict[str, Any]] = None, _retry_count: int = 0) -> Optional[Dict[str, Any]]:
        """Send a JSON-RPC request to the MCP server with enhanced retry logic."""
        
        def get_smart_retry_params(original_params: Dict[str, Any], error_message: str, tool_method: str) -> Optional[Dict[str, Any]]:
            """Generate modified parameters for smart retry based on error patterns."""
            if not original_params:
                return None
                
            # Tool-specific fallback strategies
            tool_name = original_params.get('name', '')
            tool_args = original_params.get('arguments', {})
            
            # Fetch ExtractArticle.js failures -> add raw: true
            if (tool_name == 'fetch' and 
                'ExtractArticle.js' in error_message and 
                'non-zero exit status' in error_message and
                not tool_args.get('raw')):
                modified_args = tool_args.copy()
                modified_args['raw'] = True
                return {**original_params, 'arguments': modified_args}
            
            # Add more tool-specific fallback strategies here as needed
            # Example: shell command timeout -> reduce timeout
            # Example: search tool too many results -> add limit
            
            return None
        
        # Standardized error response structure
        def create_error_response(message: str, code: int = -32000) -> Dict[str, Any]:
            return {
                "error": True,
                "message": message,
                "code": code
            }
        
        import time
        start_time = time.time()
        
        max_retries = 5  # Increased for external servers
        base_delay = 1.0  # Base delay for exponential backoff
        
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
                
                # Use longer timeout for external servers that may do complex processing
                server_name = self.server_config.get('name', 'unknown')
                is_external_server = any(keyword in server_name.lower() 
                                       for keyword in ['fetch', 'web', 'http', 'api', 'external'])
                timeout_duration = 60.0 if is_external_server else 30.0
                
                logger.debug(f"Using {timeout_duration}s timeout for server: {server_name}")
                
                try:
                    response_line_bytes = await asyncio.wait_for(
                        self.process.stdout.readline(),
                        timeout=timeout_duration
                    )
                except asyncio.TimeoutError:
                    # For external servers, try one immediate retry before giving up
                    if is_external_server and _retry_count == 0:
                        logger.warning(f"External server {server_name} timed out, trying immediate retry")
                        await asyncio.sleep(1.0)
                        return await self._send_request(method, params, _retry_count + 1)
                    raise  # Re-raise timeout for normal handling
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
                        except (UnicodeDecodeError, AttributeError):
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

            # Enhanced JSON parsing with validation
            try:
                response_text = response_line.strip()
                logger.debug(f"Raw MCP response: {response_text[:200]}...")
                
                if not response_text:
                    logger.error("Empty response from MCP server")
                    return create_error_response("Empty response from MCP server")
                
                # Validate JSON structure before parsing
                if not (response_text.startswith('{') and response_text.endswith('}')):
                    logger.error(f"Invalid JSON format from MCP server: {response_text[:100]}...")
                    return create_error_response(f"Invalid response format: expected JSON-RPC, got: {response_text[:100]}...")
                
                response = json.loads(response_text)
                
            except json.JSONDecodeError as je:
                logger.error(f"JSON decode error from MCP server: {je}")
                logger.error(f"Raw response: {response_line.strip()[:500]}...")
                return create_error_response(f"Invalid JSON response from MCP server: {str(je)}")
            
            except Exception as parse_error:
                logger.error(f"Unexpected error parsing MCP response: {parse_error}")
                return create_error_response(f"Response parsing error: {str(parse_error)}")

            if "error" in response:
                error_info = response['error']
                error_code = error_info.get("code", -1)
                error_message = str(error_info.get("message", "Unknown error"))
                
                # Don't retry security blocks - they're intentional rejections  
                # Return them in normalized format for consistent frontend display
                if "SECURITY BLOCK" in error_message:
                    logger.info(f"MCP server security block: {error_message}")
                    return {
                        "error": True,
                        "message": error_message,
                        "code": error_code
                    }
                
                # Check for external server specific errors that should trigger retries
                external_server_errors = [
                    "ExtractArticle.js", "non-zero exit status", "Command", "returned",
                    "cache", "processing", "temporary", "busy"
                ]
                
                # Add more specific error patterns
                content_processing_errors = [
                    "Content type", "cannot be simplified", "truncated"
                ]
                
                should_retry_external = any(err_pattern in error_message for err_pattern in external_server_errors)
                
                # Retry logic for external server errors
                if should_retry_external and _retry_count < max_retries:
                    # Try smart retry with modified parameters first
                    if _retry_count == 0:  # Only try smart retry on first failure
                        smart_params = get_smart_retry_params(params, error_message, method)
                        if smart_params:
                            logger.warning(f"Smart retry with modified parameters for {smart_params.get('name', 'unknown')} tool")
                            # Don't increment retry count for parameter modification attempts
                            return await self._send_request(method, smart_params, _retry_count)
                    
                    # Fall back to regular retry with exponential backoff
                    delay = base_delay * (2 ** _retry_count)  # Exponential backoff
                    logger.warning(f"External MCP server error detected, retrying in {delay}s (attempt {_retry_count + 1}/{max_retries + 1}): {error_message}")
                    
                    await asyncio.sleep(delay)
                    return await self._send_request(method, params, _retry_count + 1)
                
                # Check for cache consistency issues
                cache_indicators = ["cached", "previous", "mixed", "wrong url"]
                if any(indicator in error_message.lower() for indicator in cache_indicators) and _retry_count < 2:
                    logger.warning(f"Cache consistency issue detected, immediate retry: {error_message}")
                    await asyncio.sleep(0.5)  # Short delay for cache issues
                    return await self._send_request(method, params, _retry_count + 1)
                
                # Check if this is a timeout error and we haven't exhausted retries
                is_timeout = (error_code == -32603 and 
                             ("timed out" in error_message.lower() or "timeout" in error_message.lower()))
                
                # Timeouts should fail immediately to let the model choose a lighter alternative
                if not is_timeout and not should_retry_external and _retry_count < max_retries:
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
    
    def _validate_and_clean_response(self, result: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        """Validate and clean MCP tool response, handling external server quirks."""
        if not result:
            return result
        
        # Handle content field responses (common in external servers)
        if "content" in result and isinstance(result["content"], list):
            content_list = result["content"]
            
            # Clean up mixed/cached content responses
            if len(content_list) > 0:
                first_content = content_list[0]
                if isinstance(first_content, dict) and "text" in first_content:
                    text_content = first_content["text"]
                    
                    # Detect cache contamination patterns
                    cache_indicators = [
                        "Contents of https://wttr.in/", 
                        "Contents of https://api.",
                        "Failed to fetch https://"
                    ]
                    
                    has_cache_contamination = any(indicator in text_content for indicator in cache_indicators)
                    
                    if has_cache_contamination:
                        logger.warning(f"Cache contamination detected in response: {text_content[:100]}...")
                        
                        # Try to extract the actual content after the contamination
                        for indicator in cache_indicators:
                            if indicator in text_content:
                                # Find the end of the cache indicator line and extract what follows
                                lines = text_content.split('\n')
                                clean_lines = [line for line in lines if not any(ci in line for ci in cache_indicators)]
                                if clean_lines:
                                    first_content["text"] = '\n'.join(clean_lines).strip()
                                    logger.info(f"Cleaned cache contamination, extracted: {first_content['text'][:100]}...")
        
        return result
    
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
            raw_result = await self._send_request("tools/call", {
                "name": name,
                "arguments": arguments
            })
            
            # Validate and clean the response
            result = self._validate_and_clean_response(raw_result)
            
            return result
    
    def set_tool_rate_limit(self, tool_name: str, seconds: float) -> None:
        """
        Set a custom rate limit for a specific tool.
        
        Args:
            tool_name: Name of the tool to configure
            seconds: Minimum seconds between consecutive calls (0 to disable rate limiting)
        """
        self._tool_rate_limits[tool_name] = seconds
        logger.info(f"Set rate limit for tool '{tool_name}': {seconds}s")
    
    def _check_rate_limit(self, tool_name: str) -> Optional[float]:
        """
        Check if a tool call should be rate limited.
        
        Args:
            tool_name: Name of the tool to check
            
        Returns:
            None if call is allowed, otherwise returns seconds to wait
        """
        # Get rate limit for this tool (use default if not configured)
        rate_limit = self._tool_rate_limits.get(tool_name, self._default_rate_limit)
        
        # Rate limiting disabled for this tool
        if rate_limit <= 0:
            return None
            
        # Check last call time
        last_call = self._tool_call_timestamps.get(tool_name)
        if last_call is None:
            return None  # First call to this tool
            
        elapsed = time.time() - last_call
        return max(0, rate_limit - elapsed) if elapsed < rate_limit else None
    
    async def call_tool(self, name: str, arguments: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Call a tool on the MCP server."""
        try:
            # CRITICAL: Unwrap tool_input if present
            # Some models wrap parameters in {'tool_input': {...}}
            # while the MCP server expects unwrapped parameters
            if isinstance(arguments, dict) and 'tool_input' in arguments and len(arguments) == 1:
                logger.debug(f"Unwrapping tool_input for tool '{name}': {arguments}")
                tool_input = arguments['tool_input']
                
                # Handle case where tool_input is a JSON string instead of dict
                if isinstance(tool_input, str):
                    try:
                        arguments = json.loads(tool_input)
                        logger.debug(f"Parsed tool_input JSON string: {arguments}")
                    except json.JSONDecodeError as e:
                        logger.error(f"Failed to parse tool_input JSON: {e}")
                        return {"error": True, "message": f"Invalid tool_input JSON: {str(e)}", "code": -32602}
                else:
                    arguments = tool_input
                logger.debug(f"Unwrapped arguments: {arguments}")
            
            # Validate arguments against tool schema before sending
            tool_schema = None
            for tool in self.tools:
                if tool.name == name:
                    tool_schema = tool.inputSchema
                    break
            
            
            if tool_schema:
                validated_args = self._validate_and_convert_arguments(arguments, tool_schema)
                
                # Check if validation returned an error
                if isinstance(validated_args, dict) and validated_args.get("__validation_error__"):
                    error_msg = validated_args.get("message", "Invalid arguments")
                    return {
                        "error": True,
                        "message": error_msg,
                        "code": -32602,
                        "content": [{"type": "text", "text": f"❌ **Parameter Validation Error**: {error_msg}\n\nPlease check the tool's parameter requirements and try again with correct parameter types."}]
                    }
                arguments = validated_args
            
            # Check rate limit before executing
            wait_time = self._check_rate_limit(name)
            if wait_time is not None and wait_time > 0:
                logger.warning(f"Rate limit active for tool '{name}': waiting {wait_time:.1f}s")
                # Wait for the remaining time
                await asyncio.sleep(wait_time)
            elif wait_time == 0:
                # Exactly at the rate limit boundary, add a small delay
                await asyncio.sleep(0.1)
            
            # Record this call attempt
            self._tool_call_timestamps[name] = time.time()
            
            result = await self._send_request("tools/call", {
                "name": name,
                "arguments": arguments
            })
            
            # Don't retry on validation errors - fail fast
            if isinstance(result, dict) and result.get("error") and "validation" in str(result.get("message", "")).lower():
                return result
                
            return result
        except Exception as e:
            logger.error(f"Error calling MCP tool {name} on {self.server_config.get('name', 'unknown')}: {str(e)}")
            return None
    
            # Validate and clean the response
            result = self._validate_and_clean_response(raw_result)
            
            # Record success/failure for health monitoring
            if result and not result.get("error"):
                self._record_call_result(True)
            else:
                self._record_call_result(False)
                
                # For external servers with consistent failures, provide helpful error
                if self._consecutive_failures >= 3:
                    server_name = self.server_config.get('name', 'unknown')
                    logger.warning(f"External MCP server {server_name} has {self._consecutive_failures} consecutive failures")
                    
                    return {
                        "error": True,
                        "message": f"External MCP server '{server_name}' is experiencing issues. Consider using alternative tools or restarting the server.",
                        "code": -32001,
                        "consecutive_failures": self._consecutive_failures
                    }
            
            return result
            
    def _validate_and_convert_arguments(self, arguments: Dict[str, Any], schema: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Validate and convert argument types based on tool schema."""
        validation_errors = []
        
        # Handle string arguments - convert to dict with the string as the primary parameter
        if isinstance(arguments, str):
            logger.debug(f"Converting string argument to dict: '{arguments}'")
            # Try to determine the primary parameter from schema
            if schema and "properties" in schema:
                # Use the first required field as the key, or first property if no required fields
                required = schema.get("required", [])
                primary_key = required[0] if required else list(schema["properties"].keys())[0]
                arguments = {primary_key: arguments}
                logger.debug(f"Converted to dict: {arguments}")
            else:
                logger.error(f"Cannot convert string argument without schema")
                return {
                    "__validation_error__": True,
                    "message": "Cannot convert string argument without schema"
                }
        
        # Ensure arguments is actually a dict
        if not isinstance(arguments, dict):
            logger.error(f"Arguments must be a dict, got {type(arguments)}: {arguments}")
            return None
            
        if not schema or "properties" not in schema:
            return arguments
            
        validated = {}
        properties = schema["properties"]
        required = schema.get("required", [])
        
        # Check required fields
        for field in required:
            if field not in arguments:
                logger.error(f"Missing required field: {field}")
                validation_errors.append(f"Missing required field: {field}")
        
        if validation_errors:
            return {
                "__validation_error__": True,
                "message": " -- ".join(validation_errors)
            }
                
        # Validate and convert each argument
        for key, value in arguments.items():
            if key not in properties:
                # Allow extra fields but warn
                logger.warning(f"Unknown parameter: {key}")
                validated[key] = value
                continue
                
            field_schema = properties[key]
            expected_type = field_schema.get("type")
            
            # Handle array type conversion
            if expected_type == "array":
                if isinstance(value, str):
                    # Convert string to single-element array
                    logger.debug(f"Converting string to array for {key}: '{value}' -> ['{value}']")
                    validated[key] = [value]
                elif isinstance(value, list):
                    validated[key] = value
                else:
                    logger.warning(f"Unexpected type for array field {key}: {type(value)}")
                    validated[key] = value
                continue
            
            # Type conversion
            if expected_type == "integer" and isinstance(value, str):
                try:
                    validated[key] = int(value)
                except ValueError:
                    logger.error(f"Cannot convert {key}='{value}' to integer")
                    return {
                        "__validation_error__": True,
                        "message": f"Cannot convert {key}='{value}' to integer"
                    }
            elif expected_type == "boolean" and isinstance(value, str):
                validated[key] = value.lower() in ("true", "1", "yes")
            else:
                validated[key] = value
                
        return validated
    
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
    
    async def _capture_logs(self):
        """Capture stdout and stderr from the MCP server process."""
        if not self.process:
            return
            
        try:
            # Read stderr for startup errors
            while True:
                if self.process.stderr:
                    line = await self.process.stderr.readline()
                    if line:
                        log_entry = f"STDERR: {line.decode().strip()}"
                        self.logs.append(log_entry)
                        # Keep only last 100 log entries
                        if len(self.logs) > 100:
                            self.logs.pop(0)
                    else:
                        break
                else:
                    break
        except Exception as e:
            logger.error(f"Error capturing logs for {self.server_config.get('name', 'unknown')}: {e}")
            self.logs.append(f"ERROR: Failed to capture logs - {str(e)}")
