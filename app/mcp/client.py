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
from dataclasses import dataclass, asdict, fields, field
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
    arguments: List[Dict[str, Any]] = field(default_factory=list)


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
        # SDK-based transport for remote (SSE / StreamableHTTP) connections
        self._sdk_session = None          # mcp.client.session.ClientSession
        self._sdk_exit_stack = None       # contextlib.AsyncExitStack keeping transports alive
        self._is_remote = bool(server_config.get("url"))

        self._response_buffer: Dict[int, Dict[str, Any]] = {}  # Buffer for out-of-order responses
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
            # Remote server: use the official MCP SDK transports
            if self._is_remote:
                return await self._connect_remote()

            # Start the MCP server process
            command = self.server_config.get("command", [])
            if isinstance(command, str):
                command = [command]
            
            # For registry-installed services without explicit command, generate from installation_path
            if not command and self.server_config.get("installation_path"):
                installation_path = self.server_config["installation_path"]
                logger.debug(f"Attempting to generate command for registry service at: {installation_path}")
                self.logs.append(f"INFO: Looking for executable in {installation_path}")
                
                if not os.path.exists(installation_path):
                    logger.error(f"Installation path does not exist: {installation_path}")
                    self.logs.append(f"ERROR: Installation path does not exist: {installation_path}")
                else:
                    # Look for common executable patterns in the installation directory
                    import glob
                    
                    # Check for Python scripts
                    python_files = glob.glob(os.path.join(installation_path, "*.py"))
                    logger.debug(f"Found Python files: {python_files}")
                    self.logs.append(f"INFO: Found Python files: {python_files}")
                    
                    if python_files:
                        # Use the first Python file found
                        command = ["python", python_files[0]]
                        logger.debug(f"Generated command for registry service: {command}")
                        self.logs.append(f"INFO: Generated command: {command}")
                    else:
                        # Check for executable files
                        try:
                            files = os.listdir(installation_path)
                            logger.debug(f"Files in installation directory: {files}")
                            self.logs.append(f"INFO: Files in directory: {files}")
                            
                            if not files:
                                logger.error(f"MCP server '{self.server_config.get('name', 'unknown')}': Installation directory is empty")
                                logger.error(f"  Path: {installation_path}")
                                logger.error(f"  This server cannot start - reinstall or disable it")
                                self.logs.append("ERROR: Installation directory is empty")
                                # Return early - no point trying to start with no executable
                                return False
                            
                            for file in files:
                                file_path = os.path.join(installation_path, file)
                                if os.path.isfile(file_path) and os.access(file_path, os.X_OK):
                                    command = [file_path]
                                    logger.debug(f"Found executable for registry service: {command}")
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
            # For workspace-scoped instances, prefer the env override from server config
            config_env = self.server_config.get("env", {})
            working_dir = config_env.get("ZIYA_USER_CODEBASE_DIR") or os.environ.get("ZIYA_USER_CODEBASE_DIR")

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
            
            logger.debug(f"Starting MCP server '{self.server_config.get('name', 'unknown')}' with working directory: {working_dir}")
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
                            logger.debug(f"Found MCP server script '{part}' at: {current_part_resolved_path}")
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

            logger.debug(f"Starting MCP server with command: {' '.join(final_popen_command)} in {working_dir}")
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
            
            # CRITICAL: Give servers time to start up before initializing
            # External servers (npx, uvx, node) need more time than builtins
            server_name = self.server_config.get('name', 'unknown')
            command = self.server_config.get('command', [])
            args = self.server_config.get('args', [])
            command_str = ' '.join(command) if isinstance(command, list) else str(command)
            full_command = f"{command_str} {' '.join(args)}"
            
            logger.debug(f"Server {server_name}: command='{command_str}', args={args}")
            
            is_external = any(indicator in full_command.lower() 
                            for indicator in ['npx', 'uvx', 'node'])
            
            logger.debug(f"Server {server_name}: is_external={is_external}, using {0.3 if is_external else 0.05}s delay")
            startup_delay = 0.3 if is_external else 0.05
            await asyncio.sleep(startup_delay)
            logger.debug(f"Waited {startup_delay}s for {server_name} to start")
            
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
                # CRITICAL: Verify process is still alive after initialization
                if self.process.returncode is not None:
                    logger.error(f"Server {self.server_config.get('name', 'unknown')} died during initialization (exit code: {self.process.returncode})")
                    self.is_connected = False
                    # Try to capture any error output
                    if self.logs:
                        logger.error(f"  Last logs: {self.logs[-5:]}")
                        
                        # Check for npm authentication failures in captured logs
                        log_text = ' '.join(self.logs[-10:])  # Check last 10 log entries
                        auth_indicators = ['FETCH_ERROR', 'authentication', 'login', 'unauthorized', '401', '403']
                        
                        if any(indicator in log_text for indicator in auth_indicators):
                            import sys
                            import subprocess
                            
                            # Detect current npm registry
                            current_registry = "unknown"
                            try:
                                result = subprocess.run(
                                    ['npm', 'config', 'get', 'registry'],
                                    capture_output=True,
                                    text=True,
                                    timeout=2
                                )
                                if result.returncode == 0:
                                    current_registry = result.stdout.strip()
                            except Exception:
                                pass
                            
                            print("\n" + "=" * 80, file=sys.stderr)
                            print("⚠️  NPM AUTHENTICATION ERROR", file=sys.stderr)
                            print("=" * 80, file=sys.stderr)
                            print(f"\nMCP server '{self.server_config.get('name', 'unknown')}' failed to start.", file=sys.stderr)
                            print(f"Your npm registry authentication has expired.\n", file=sys.stderr)
                            print(f"Current registry: {current_registry}\n", file=sys.stderr)
                            
                            if current_registry != "unknown" and current_registry != "https://registry.npmjs.org/":
                                print("To fix, re-authenticate to your npm registry:", file=sys.stderr)
                                print(f"  npm login --registry={current_registry}\n", file=sys.stderr)
                            else:
                                print("To fix:", file=sys.stderr)
                                print("  npm login\n", file=sys.stderr)
                            
                            print("Or switch to public npm registry:", file=sys.stderr)
                            print("  npm config set registry https://registry.npmjs.org/", file=sys.stderr)
                            print("=" * 80 + "\n", file=sys.stderr)
                            raise ValueError(f"npm authentication required for MCP server")
                    return False
                
                self.capabilities = init_result.get("capabilities", {})
                self.is_connected = True
                
                # Initialize successful call timestamp on connection
                self._last_successful_call = time.time()
                
                # Send initialized notification
                await self._send_notification("notifications/initialized")
                
                # Load available resources, tools, and prompts
                await self._load_server_capabilities()
                
                logger.debug(f"Successfully connected to MCP server: {self.server_config.get('name', 'unknown')}")
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
                            
                            # Check for npm/package manager authentication failures - fail fast
                            auth_indicators = [
                                'FETCH_ERROR',
                                'authentication',
                                'login',
                                'unauthorized',
                                '401',
                                '403'
                            ]
                            
                            if any(indicator in stderr_output.lower() for indicator in auth_indicators):
                                import sys
                                import subprocess
                                
                                # Try to detect current npm registry configuration
                                current_registry = "unknown"
                                try:
                                    result = subprocess.run(
                                        ['npm', 'config', 'get', 'registry'],
                                        capture_output=True,
                                        text=True,
                                        timeout=2
                                    )
                                    if result.returncode == 0:
                                        current_registry = result.stdout.strip()
                                except Exception:
                                    pass
                                
                                print("\n" + "=" * 80, file=sys.stderr)
                                print("⚠️  NPM AUTHENTICATION ERROR", file=sys.stderr)
                                print("=" * 80, file=sys.stderr)
                                print(f"\nMCP server '{self.server_config.get('name', 'unknown')}' failed to start.", file=sys.stderr)
                                print(f"Your npm registry authentication has expired.\n", file=sys.stderr)
                                print(f"Current registry: {current_registry}\n", file=sys.stderr)
                                
                                if current_registry != "unknown" and current_registry != "https://registry.npmjs.org/":
                                    print("To fix, re-authenticate to your npm registry:", file=sys.stderr)
                                    print(f"  npm login --registry={current_registry}\n", file=sys.stderr)
                                else:
                                    print("To fix:", file=sys.stderr)
                                    print("  npm login\n", file=sys.stderr)
                                
                                print("Or switch to public npm registry:", file=sys.stderr)
                                print("  npm config set registry https://registry.npmjs.org/", file=sys.stderr)
                                print("=" * 80 + "\n", file=sys.stderr)
                                raise ValueError(f"npm authentication required for MCP server")
                        
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
        # Remote SDK session cleanup
        if self._sdk_exit_stack:
            try:
                await self._sdk_exit_stack.aclose()
            except Exception as e:
                logger.error(f"Error closing remote MCP session: {e}")
            finally:
                self._sdk_session = None
                self._sdk_exit_stack = None
                self.is_connected = False
            return

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
    
    # ----------------------------------------------------------------
    # Remote (SSE / StreamableHTTP) connection support
    # ----------------------------------------------------------------
    async def _connect_remote(self) -> bool:
        """Connect to a remote MCP server via SSE or StreamableHTTP."""
        import contextlib
        url = self.server_config["url"]
        server_name = self.server_config.get("name", url)
        transport_type = self.server_config.get("transport", "streamable-http")
        logger.info(f"Connecting to remote MCP server: {server_name} at {url} (transport={transport_type})")

        # Build optional headers (e.g. Authorization: Bearer <token>)
        headers: Dict[str, str] = dict(self.server_config.get("headers", {}))
        auth_token = self.server_config.get("auth_token")
        if auth_token:
            headers.setdefault("Authorization", f"Bearer {auth_token}")

        try:
            from mcp.client.session import ClientSession

            stack = contextlib.AsyncExitStack()
            await stack.__aenter__()

            if transport_type == "sse":
                from mcp.client.sse import sse_client
                transport_cm = sse_client(url, headers=headers or None, timeout=30, sse_read_timeout=300)
            else:
                # Default: StreamableHTTP (the modern MCP transport)
                import httpx
                from mcp.client.streamable_http import streamable_http_client
                http_client = httpx.AsyncClient(
                    headers=headers or {},
                    timeout=httpx.Timeout(30, read=300),
                )
                transport_cm = streamable_http_client(url, http_client=http_client)

            read_stream, write_stream, *_rest = await stack.enter_async_context(transport_cm)
            session = await stack.enter_async_context(ClientSession(read_stream, write_stream))

            init_result = await session.initialize()
            logger.info(f"Remote MCP server initialized: {server_name} (protocol={init_result.protocolVersion})")

            self._sdk_session = session
            self._sdk_exit_stack = stack
            self.capabilities = {}
            if init_result.capabilities:
                caps = init_result.capabilities
                if caps.tools:
                    self.capabilities["tools"] = True
                if caps.resources:
                    self.capabilities["resources"] = True
                if caps.prompts:
                    self.capabilities["prompts"] = True

            self.is_connected = True
            self._last_successful_call = time.time()

            # Load tools, resources, prompts via the SDK session
            await self._load_remote_capabilities()
            logger.info(f"Remote MCP: {server_name} — {len(self.tools)} tools, {len(self.resources)} resources")
            return True

        except Exception as e:
            logger.error(f"Failed to connect to remote MCP server {server_name}: {e}", exc_info=True)
            self.logs.append(f"ERROR: Remote connection failed — {e}")
            self.is_connected = False
            return False

    async def _load_remote_capabilities(self):
        """Load tools/resources/prompts from a remote MCP session."""
        session = self._sdk_session
        server_name = self.server_config.get("name", "remote")

        if "tools" in self.capabilities:
            try:
                result = await session.list_tools()
                self.tools = [
                    MCPTool(
                        name=t.name,
                        description=t.description or "",
                        inputSchema=t.inputSchema if isinstance(t.inputSchema, dict) else {},
                    )
                    for t in (result.tools if result else [])
                ]
            except Exception as e:
                logger.error(f"Failed to list tools from remote {server_name}: {e}")

        if "resources" in self.capabilities:
            try:
                result = await session.list_resources()
                self.resources = [
                    MCPResource(uri=str(r.uri), name=r.name, description=r.description)
                    for r in (result.resources if result else [])
                ]
            except Exception as e:
                logger.error(f"Failed to list resources from remote {server_name}: {e}")

        if "prompts" in self.capabilities:
            try:
                result = await session.list_prompts()
                self.prompts = [
                    MCPPrompt(name=p.name, description=p.description or "")
                    for p in (result.prompts if result else [])
                ]
            except Exception as e:
                logger.error(f"Failed to list prompts from remote {server_name}: {e}")

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
        # Remote servers don't have a process — check session
        if self._is_remote:
            return self._sdk_session is not None and self.is_connected

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
        
        if not self.process:
            return create_error_response("No active process")
        
        if not self.process.stdin or not self.process.stdout:
            logger.error(f"Process streams not available: stdin={self.process.stdin}, stdout={self.process.stdout}")
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
                
                # First, check if we already have this response in the buffer
                if self.request_id in self._response_buffer:
                    logger.debug(f"Found response for request {self.request_id} in buffer")
                    buffered_response = self._response_buffer.pop(self.request_id)
                    
                    # Update timing
                    read_time = time.time() - read_start
                    logger.debug(f"🔍 MCP_TIMING: Read from buffer took {read_time*1000:.1f}ms")
                    
                    # Skip to response parsing (jump to line ~497)
                    response = buffered_response
                    
                    # Validate response ID matches request ID (should always match since we used it as key)
                    response_id = response.get("id")
                    if response_id != self.request_id:
                        logger.error(f"Buffer consistency error: expected {self.request_id}, got {response_id}")
                        return create_error_response(f"Buffer consistency error: expected {self.request_id}, got {response_id}")
                    
                    # Skip to error handling and result return
                    if "error" in response:
                        error_info = response['error']
                        error_code = error_info.get("code", -1)
                        error_message = str(error_info.get("message", "Unknown error"))
                        logger.error(f"MCP server error (from buffer): {error_info}")
                        return {
                            "error": True,
                            "message": error_message,
                            "code": error_code
                        }
                    
                    # Success - return result
                    self._last_successful_call = time.time()
                    return response.get("result")
                
                # Determine readline timeout based on context:
                # 1. If this is a tools/call with an explicit timeout arg, honour it (+ buffer)
                # 2. External servers get a longer default
                # 3. Everything else gets 30s
                server_name = self.server_config.get('name', 'unknown')
                is_external_server = any(keyword in server_name.lower() 
                                       for keyword in ['fetch', 'web', 'http', 'api', 'external'])
                timeout_duration = 60.0 if is_external_server else 30.0

                # For tool calls, extract the tool's own timeout so long-running
                # commands aren't killed by the readline timeout before the
                # subprocess timeout fires (which gives a cleaner error).
                if method == 'tools/call' and params:
                    tool_timeout = params.get('arguments', {}).get('timeout')
                    if tool_timeout is not None:
                        try:
                            tool_timeout = float(tool_timeout)
                            # Add 10s buffer so subprocess.TimeoutExpired fires first
                            timeout_duration = max(timeout_duration, tool_timeout + 10.0)
                        except (ValueError, TypeError):
                            pass  # Invalid value, keep default
                
                logger.debug(f"Using {timeout_duration}s timeout for server: {server_name}")
                
                # Read responses until we find the one matching our request ID
                max_read_attempts = 10  # Prevent infinite loops
                read_attempts = 0
                response = None  # Initialize to avoid unbound variable
                
                while read_attempts < max_read_attempts:
                    read_attempts += 1
                    
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
                    
                    if not response_line_bytes:
                        # EOF, process likely terminated
                        if self.process.returncode is not None:
                            # Process died - try to read stderr for auth errors before logging generic EOF
                            try:
                                stderr_bytes = await self.process.stderr.read() if self.process.stderr else b""
                                stderr_output = stderr_bytes.decode('utf-8', errors='ignore')
                                
                                # Check for npm authentication failures
                                auth_indicators = ['FETCH_ERROR', 'authentication', 'login', 'unauthorized', '401', '403']
                                
                                if stderr_output and any(indicator in stderr_output.lower() for indicator in auth_indicators):
                                    import sys
                                    import subprocess
                                    
                                    # Detect current npm registry
                                    current_registry = "unknown"
                                    try:
                                        result = subprocess.run(
                                            ['npm', 'config', 'get', 'registry'],
                                            capture_output=True,
                                            text=True,
                                            timeout=2
                                        )
                                        if result.returncode == 0:
                                            current_registry = result.stdout.strip()
                                    except Exception:
                                        pass
                                    
                                    print("\n" + "=" * 80, file=sys.stderr)
                                    print("⚠️  NPM AUTHENTICATION ERROR", file=sys.stderr)
                                    print("=" * 80, file=sys.stderr)
                                    print(f"\nMCP server '{self.server_config.get('name', 'unknown')}' failed to start.", file=sys.stderr)
                                    print(f"Your npm registry authentication has expired.\n", file=sys.stderr)
                                    print(f"Current registry: {current_registry}\n", file=sys.stderr)
                                    
                                    if current_registry != "unknown" and current_registry != "https://registry.npmjs.org/":
                                        print("To fix, re-authenticate to your npm registry:", file=sys.stderr)
                                        print(f"  npm login --registry={current_registry}\n", file=sys.stderr)
                                    else:
                                        print("To fix:", file=sys.stderr)
                                        print("  npm login\n", file=sys.stderr)
                                    
                                    print("Or switch to public npm registry:", file=sys.stderr)
                                    print("  npm config set registry https://registry.npmjs.org/", file=sys.stderr)
                                    print("=" * 80 + "\n", file=sys.stderr)
                                    raise ValueError(f"npm authentication required for MCP server")
                            except Exception:
                                pass  # If we can't read stderr, fall through to generic EOF error
                        
                        logger.error("No response from MCP server (EOF)")
                        if self.process.returncode is not None:
                            logger.error(f"MCP server process has terminated with code: {self.process.returncode}")
                        return create_error_response("No response from MCP server (EOF)")
                    
                    response_text = response_line_bytes.decode('utf-8').strip()
                    
                    if not response_text:
                        logger.warning("Empty response line, continuing to read...")
                        continue
                    
                    # Parse response to check ID
                    try:
                        response = json.loads(response_text)
                    except json.JSONDecodeError as je:
                        logger.error(f"JSON decode error: {je}, response: {response_text[:200]}")
                        continue  # Try next line
                    
                    response_id = response.get("id")
                    
                    if response_id == self.request_id:
                        # Found our response!
                        logger.debug(f"Found matching response for request {self.request_id} on attempt {read_attempts}")
                        break
                    else:
                        # This is a response for a different request - buffer it
                        logger.warning(f"Got response for request {response_id}, expecting {self.request_id}. Buffering it.")
                        self._response_buffer[response_id] = response
                        
                        # Continue reading to find our response
                        continue
                
                if read_attempts >= max_read_attempts:
                    logger.error(f"Failed to find response for request {self.request_id} after {max_read_attempts} attempts")
                    logger.error(f"Buffered responses: {list(self._response_buffer.keys())}")
                    return create_error_response(f"Response not found after {max_read_attempts} read attempts")
                
                # Safety check - should never happen if loop logic is correct
                if response is None:
                    logger.error("Response is None after successful loop exit - this should not happen")
                    return create_error_response("Internal error: response not set")
                
                read_time = time.time() - read_start
                logger.debug(f"🔍 MCP_TIMING: Read took {read_time*1000:.1f}ms")

            except asyncio.TimeoutError:
                logger.error(f"Timeout waiting for response from MCP server for method '{method}'")
                return {
                    "error": True,
                    "message": f"Request timed out after {timeout_duration:.0f} seconds for method '{method}'",
                    "code": -32000 # Custom timeout error code
                }
            except Exception as e:
                logger.error(f"Error reading from MCP server: {e}")
                return create_error_response(f"Error reading from MCP server: {str(e)}")

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
                
                # Don't retry policy blocks (shell BLOCKED, WRITE BLOCKED) —
                # these are permanent rejections that won't change on retry.
                if "BLOCKED" in error_message:
                    logger.info(f"MCP server policy block (not retrying): {error_message[:200]}")
                    return {
                        "error": True,
                        "message": error_message,
                        "code": error_code,
                        "policy_block": True
                    }
                
                # Check for external server specific errors that should trigger retries
                external_server_errors = [
                    "ExtractArticle.js", "non-zero exit status",
                    "temporary failure", "temporarily unavailable", "server is busy"
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
            logger.debug(f"Server {server_name_for_log} capabilities: {self.capabilities}")
            logger.debug(f"Checking tools capability: {self.capabilities.get('tools')}")
            if "tools" in self.capabilities:
                logger.debug(f"Calling tools/list for {server_name_for_log}")
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
                    logger.debug(f"Successfully loaded {len(valid_tools)} tools for server {server_name_for_log}")
                elif tools_result is None:
                    logger.warning(f"Failed to get a valid response for tools/list from {server_name_for_log}")
                else:
                    logger.warning(f"No 'tools' key in response from {server_name_for_log}: {tools_result}")
            
            # Load prompts
            logger.debug(f"Loading prompts for server: {server_name_for_log}")
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

            logger.debug(f"Loaded MCP capabilities for {server_name_for_log}: {len(self.resources)} resources, {len(self.tools)} tools, {len(self.prompts)} prompts")
            logger.debug(f"Tool names for {server_name_for_log}: {[tool.name for tool in self.tools]}")

        except Exception as e:
            logger.error(f"Error loading MCP server capabilities for {self.server_config.get('name', 'unknown')}: {str(e)}", exc_info=True)
    
    def _validate_and_clean_response(self, result: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        """Validate and clean MCP tool response.

        Delegates to the centralised response_validator module which checks
        schema conformance, size limits, MIME types, hidden-character
        stripping, and injection-pattern scanning.
        """
        if result is None:
            return result

        from app.mcp.response_validator import (
            ResponseValidationError,
            validate_response,
            run_semantic_validators,
        )

        # Determine tool name for logging (from the call context)
        tool_name = result.get("_tool_name", "unknown")

        try:
            result = validate_response(result, tool_name=tool_name)
        except ResponseValidationError as exc:
            logger.error(f"Response validation failed for '{tool_name}': {exc}")
            return {
                "error": True,
                "message": str(exc),
                "code": exc.error_code,
                "content": [{"type": "text", "text": f"❌ **Response Validation Error**: {exc}"}],
            }

        # Run per-tool semantic validators (if any are registered).
        is_valid, messages = run_semantic_validators(tool_name, result)
        for msg in messages:
            logger.warning(f"Semantic validation ({tool_name}): {msg}")
        if not is_valid:
            error_msgs = [m for m in messages if m.startswith("ERROR:")]
            return {
                "error": True,
                "message": "; ".join(error_msgs),
                "code": -32602,
                "content": [{"type": "text", "text": f"❌ **Semantic Validation Error**: {'; '.join(error_msgs)}"}],
            }

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
        # Import signing module
        from app.mcp.signing import sign_tool_result
        
        # Get conversation_id from arguments if present (for signing context)
        conversation_id = arguments.get('conversation_id', 'default')

        # Remote server path: delegate to SDK ClientSession
        if self._sdk_session and self._is_remote:
            try:
                # Strip internal metadata before sending to remote server
                clean_args = {k: v for k, v in arguments.items()
                              if k not in ('conversation_id', '_workspace_path')}
                sdk_result = await self._sdk_session.call_tool(name, clean_args)

                # Convert SDK result to our dict format
                content_list = []
                if sdk_result and sdk_result.content:
                    for item in sdk_result.content:
                        if hasattr(item, 'text'):
                            content_list.append({"type": "text", "text": item.text})
                        elif hasattr(item, 'data'):
                            content_list.append({"type": "image", "data": item.data,
                                                 "mimeType": getattr(item, 'mimeType', 'image/png')})
                        else:
                            content_list.append({"type": "text", "text": str(item)})

                result = {"content": content_list} if content_list else {"content": [{"type": "text", "text": ""}]}

                if sdk_result and getattr(sdk_result, 'isError', False):
                    result["error"] = True
                    result["message"] = content_list[0].get("text", "Unknown error") if content_list else "Unknown error"

                # Validate and sanitize the remote response
                result = self._validate_and_clean_response(result)

                # Sign the result
                if not result.get("error"):
                    result = sign_tool_result(name, clean_args, result, conversation_id)

                self._record_call_result(not result.get("error", False))
                return result

            except Exception as e:
                logger.error(f"Remote tool call failed for {name}: {e}")
                self._record_call_result(False)
                return {"error": True, "message": f"Remote tool call failed: {e}", "code": -32603}
        
        try:
            unwrapped_tool_input = False
            # CRITICAL: Unwrap tool_input if present
            # Some models wrap parameters in {'tool_input': {...}}
            # while the MCP server expects unwrapped parameters
            # Allow unwrapping even if other metadata keys (like conversation_id) are present
            if isinstance(arguments, dict) and 'tool_input' in arguments:
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
                unwrapped_tool_input = True
            
            # Validate arguments against tool schema before sending
            tool_schema = None
            for tool in self.tools:
                if tool.name == name:
                    tool_schema = tool.inputSchema
                    break
            
            # Skip validation if we unwrapped tool_input — the inner content
            # won't match the outer wrapper schema
            if tool_schema and not unwrapped_tool_input:
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
            
            # DEBUG: Log what we're about to send
            logger.info(f"🔍 MCP_CLIENT: Calling _send_request with method='tools/call', name='{name}', args={arguments}")
            
            result = await self._send_request("tools/call", {
                "name": name,
                "arguments": arguments
            })
            
            # Don't retry on validation errors - fail fast
            if isinstance(result, dict) and result.get("error") and "validation" in str(result.get("message", "")).lower():
                return result
            
            # Validate and clean the response
            result = self._validate_and_clean_response(result)
            
            # SECURITY: Sign the result before returning
            # This prevents model hallucination by cryptographically verifying
            # that results actually came from our MCP server
            if result and not result.get("error"):
                result = sign_tool_result(name, arguments, result, conversation_id)
                logger.debug(f"🔐 Signed result for {name}")
            
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
            
        except Exception as e:
            logger.error(f"Error calling MCP tool {name} on {self.server_config.get('name', 'unknown')}: {str(e)}")
            return {
                "error": True,
                "message": f"Tool execution failed: {str(e)}",
                "code": -32603
            }
            
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

        # Auto-nest stray parameters into 'input' sub-object for action+input schemas.
        # This catches cases where the manager's normalize didn't restructure
        # (e.g., workspace-scoped routing that bypasses normalize).
        if (isinstance(arguments, dict) and
            "action" in properties and
            "input" in properties and
            isinstance(properties["input"], dict) and
            properties["input"].get("type") == "object" and
            "action" in arguments):
            
            schema_keys = set(properties.keys())
            stray_keys = set(arguments.keys()) - schema_keys
            if stray_keys:
                restructured = {}
                nested = {}
                for k, v in arguments.items():
                    if k in schema_keys:
                        restructured[k] = v
                    else:
                        nested[k] = v
                if nested:
                    restructured.setdefault("input", {}).update(nested)
                    logger.info(f"Auto-nested {list(stray_keys)} into 'input' during validation")
                    arguments = restructured
        
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
            
            # Handle object type - parse JSON strings into dicts
            if expected_type == "object" and isinstance(value, str):
                try:
                    parsed = json.loads(value)
                    if isinstance(parsed, dict):
                        logger.debug(f"Parsed JSON string to object for {key}")
                        validated[key] = parsed
                    else:
                        validated[key] = value
                except (json.JSONDecodeError, TypeError):
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
                # Coerce non-string values to string when schema expects string
                # Models frequently pass numeric IDs (e.g. requestId, testRunIdentifier) as numbers
                if expected_type == "string" and not isinstance(value, str):
                    logger.debug(f"Coercing {key}={value!r} ({type(value).__name__}) to string per schema")
                    value = str(value)
                validated[key] = value
                
        # --- Schema constraint validation (enum, min/max, pattern, etc.) ---
        from app.mcp.response_validator import (
            ResponseValidationError,
            validate_input_constraints,
        )

        # Determine tool name for logging — scan self.tools for the matching schema
        tool_name = "unknown"
        for tool in self.tools:
            if tool.inputSchema is schema:
                tool_name = tool.name
                break

        try:
            validated, warnings = validate_input_constraints(validated, schema, tool_name)
            for w in warnings:
                logger.warning(f"Input validation warning: {w}")
        except ResponseValidationError as exc:
            return {"__validation_error__": True, "message": str(exc)}

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
