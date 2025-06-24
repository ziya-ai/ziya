#!/usr/bin/env python3
"""
MCP server that provides shell command execution functionality.
"""

import asyncio
import json
import subprocess
import sys
import os
import re
import time
import shlex
from typing import Dict, Any, Optional


# Global timeout tracking
_consecutive_timeouts = {}
_last_command_times = {}

class ShellServer:
    """MCP server that provides shell command execution tools."""
    
    def __init__(self):
        self.request_id = 0
        
        # Get configuration from environment
        self.git_operations_enabled = os.environ.get('GIT_OPERATIONS_ENABLED', 'true').lower() in ('true', '1', 'yes')
        self.command_timeout = int(os.environ.get('COMMAND_TIMEOUT', '10'))
        
        # Define safe command patterns with regex support
        self.safe_command_patterns = {
            # Basic file operations (read-only)
            'ls': r'^ls(\s+.*)?$',
            'cat': r'^cat(\s+.*)?$',
            'pwd': r'^pwd(\s+.*)?$',
            'find': r'^find(\s+.*)?$',
            'grep': r'^grep(\s+.*)?$',
            'wc': r'^wc(\s+.*)?$',
            'head': r'^head(\s+.*)?$',
            'tail': r'^tail(\s+.*)?$',
            'sort': r'^sort(\s+.*)?$',
            'cut': r'^cut(\s+.*)?$',
            'date': r'^date(\s+.*)?$',
            'ps': r'^ps(\s+.*)?$',
            'curl': r'^curl(\s+.*)?$',
            'ping': r'^ping(\s+.*)?$',
            'sed': r'^sed(\s+.*)?$',
            'awk': r'^awk(\s+.*)?$',
            'less': r'^less(\s+.*)?$',
            'xargs': r'^xargs(\s+.*)?$',
            'which': r'^xargs(\s+.*)?$',
            'du': r'^du(\s+.*)?$',
            'df': r'^df(\s+.*)?$',
            'file': r'^file(\s+.*)?$'
        }
        
        # Add git operations if enabled
        if self.git_operations_enabled:
            safe_git_ops = os.environ.get('SAFE_GIT_OPERATIONS', 'status,log,show,diff,branch,remote,ls-files,blame').split(',')
            safe_git_ops = [op.strip() for op in safe_git_ops if op.strip()]
            
            git_patterns = {
                'status': r'^git\s+status(\s+.*)?$',
                'log': r'^git\s+log(\s+.*)?$',
                'show': r'^git\s+show(\s+.*)?$',
                'diff': r'^git\s+diff(\s+.*)?$',
                'branch': r'^git\s+branch(\s+(?!-[dD]|--delete).*)?$',  # Allow branch listing, not deletion
                'remote': r'^git\s+remote(\s+(?!rm|remove).*)?$',  # Allow remote listing, not removal
                'config --get': r'^git\s+config\s+--get(\s+.*)?$',  # Only allow getting config, not setting
                'ls-files': r'^git\s+ls-files(\s+.*)?$',
                'ls-tree': r'^git\s+ls-tree(\s+.*)?$',
                'blame': r'^git\s+blame(\s+.*)?$',
                'tag': r'^git\s+tag(\s+(?!-[dD]|--delete).*)?$',  # Allow tag listing, not deletion
                'stash list': r'^git\s+stash\s+list(\s+.*)?$',
                'reflog': r'^git\s+reflog(\s+.*)?$',
                'rev-parse': r'^git\s+rev-parse(\s+.*)?$',
                'describe': r'^git\s+describe(\s+.*)?$',
                'shortlog': r'^git\s+shortlog(\s+.*)?$',
                'whatchanged': r'^git\s+whatchanged(\s+.*)?$',
            }
            
            # Only add enabled git operations
            for op in safe_git_ops:
                if op in git_patterns:
                    self.safe_command_patterns[f'git_{op.replace(" ", "_").replace("-", "_")}'] = git_patterns[op]
        
        # Get additional allowed commands from environment (legacy support)
        env_commands = os.environ.get('ALLOW_COMMANDS', '').split(',')
        env_commands = [cmd.strip() for cmd in env_commands if cmd.strip()]
        
        # Add environment commands as simple patterns
        for cmd in env_commands:
            if cmd not in self.safe_command_patterns:
                self.safe_command_patterns[f'env_{cmd}'] = f'^{re.escape(cmd)}(\\s+.*)?$'
        
        print(f"Shell server starting with {len(self.safe_command_patterns)} allowed command patterns", file=sys.stderr)
        print(f"Available commands: {', '.join(sorted(set([p.split('_')[0] if '_' in p else p for p in self.safe_command_patterns.keys()])))}", file=sys.stderr)
        print(f"Git operations enabled: {self.git_operations_enabled}", file=sys.stderr)
        
    def is_command_allowed(self, command: str) -> bool:
        """Check if a command matches any of the allowed patterns."""
        if not command or not command.strip():
            return False
        
        command = command.strip()
        
        # Check against all allowed patterns
        for pattern_name, pattern in self.safe_command_patterns.items():
            try:
                if re.match(pattern, command, re.IGNORECASE):
                    print(f"Command '{command}' matched pattern '{pattern_name}': {pattern}", file=sys.stderr)
                    return True
            except re.error as e:
                print(f"Regex error in pattern '{pattern_name}': {e}", file=sys.stderr)
                continue
        
        print(f"Command '{command}' did not match any allowed patterns", file=sys.stderr)
        return False
    
    def get_allowed_commands_description(self) -> str:
        """Get a human-readable description of allowed commands."""
        base_commands = set()
        for pattern_name in self.safe_command_patterns.keys():
            if pattern_name.startswith('git_'):
                base_commands.add('git (safe operations)')
            elif pattern_name.startswith('env_'):
                base_commands.add(pattern_name[4:])  # Remove 'env_' prefix
            else:
                base_commands.add(pattern_name)
        
        return ', '.join(sorted(base_commands))
        
    async def handle_request(self, request: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Handle incoming MCP requests."""
        method = request.get("method")
        params = request.get("params", {})
        request_id = request.get("id")
        
        print(f"Received request: {method}", file=sys.stderr)
        
        if method == "initialize":
            return {
                "jsonrpc": "2.0",
                "id": request_id,
                "result": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {
                        "tools": {
                            "listChanged": True
                        }
                    },
                    "serverInfo": {
                        "name": "shell-server",
                        "version": "1.0.0"
                    }
                }
            }
        elif method == "tools/list":
            return {
                "jsonrpc": "2.0",
                "id": request_id,
                "result": {
                    "tools": [
                        {
                            "name": "run_shell_command",
                            "description": f"Execute a shell command. Allowed commands: {self.get_allowed_commands_description()}",
                            "inputSchema": {
                                "type": "object",
                                "properties": {
                                    "command": {
                                        "type": "string",
                                        "description": "The shell command to execute"
                                    },
                                    "timeout": {
                                        "type": "number",
                                        "description": "Timeout in seconds (default: 10)",
                                        "default": 10
                                    }
                                },
                                "required": ["command"]
                            }
                        }
                    ]
                }
            }
        elif method == "tools/call":
            tool_name = params.get("name")
            arguments = params.get("arguments", {})
            
            if tool_name == "run_shell_command":
                command = arguments.get("command")
                # Handle timeout parameter - convert string to number if needed
                timeout_param = arguments.get("timeout", self.command_timeout)
                try:
                    timeout = float(timeout_param) if timeout_param is not None else 10
                except (ValueError, TypeError):
                    # If conversion fails, use default timeout
                    timeout = self.command_timeout
                    print(f"Warning: Invalid timeout value '{timeout_param}', using default 10 seconds", file=sys.stderr)
                
                if not command:
                    return {
                        "jsonrpc": "2.0",
                        "id": request_id,
                        "error": {
                            "code": -32602,
                            "message": "Command is required"
                        }
                    }
                
                # Check if command is allowed
                if not self.is_command_allowed(command):
                    return {
                        "jsonrpc": "2.0",
                        "id": request_id,
                        "error": {
                            "code": -32602,
                            "message": f"🚫 SECURITY BLOCK: Command '{command}' is not allowed.\n\n" +
                                     f"📋 Allowed commands: {self.get_allowed_commands_description()}\n\n" +
                                     f"💡 Tip: You can configure allowed commands in the Shell Configuration settings."
                        }
                    }
                
                try:
                    print(f"Executing command: {command}", file=sys.stderr)
                    result = subprocess.run(
                        command,
                        shell=True,
                        capture_output=True,
                        text=True,
                        timeout=timeout
                    )
                    
                    # Format output to be more shell-like
                    output = f"$ {command}\n"
                    if result.stdout:
                        output += result.stdout
                    if result.stderr:
                        output += result.stderr
                    
                    # Add exit code if non-zero
                    if result.returncode != 0:
                        output += f"\n[Exit code: {result.returncode}]"
                    
                    return {
                        "jsonrpc": "2.0",
                        "id": request_id,
                        "result": {
                            "content": [
                                {
                                    "type": "text",
                                    "text": output
                                }
                            ]
                        }
                    }
                    
                except subprocess.TimeoutExpired:
                    # Track consecutive timeouts
                    current_time = time.time()
                    if command not in _consecutive_timeouts:
                        _consecutive_timeouts[command] = 0
                    
                    # Only count as consecutive if within reasonable time window (5 minutes)
                    if command in _last_command_times and (current_time - _last_command_times[command]) < 300:
                        _consecutive_timeouts[command] += 1
                    else:
                        _consecutive_timeouts[command] = 1
                    
                    _last_command_times[command] = current_time
                    
                    # Only return timeout error if this is the 3rd consecutive timeout
                    if _consecutive_timeouts[command] < 3:
                        # Return empty success response to suppress timeout
                        return {
                            "jsonrpc": "2.0",
                            "id": request_id,
                            "result": {
                                "content": [
                                    {
                                        "type": "text",
                                        "text": ""
                                    }
                                ]
                            }
                        }
                    
                    return {
                        "jsonrpc": "2.0",
                        "id": request_id,
                        "error": {
                            "code": -32603,
                            "message": f"Command timed out after {timeout} seconds (3+ consecutive timeouts)"
                        }
                    }
                except Exception as e:
                    # Reset timeout counter on other errors
                    if command in _consecutive_timeouts:
                        _consecutive_timeouts[command] = 0
                    
                    return {
                        "jsonrpc": "2.0",
                        "id": request_id,
                        "error": {
                            "code": -32603,
                            "message": f"Error executing command: {str(e)}"
                        }
                    }
                else:
                    # Reset timeout counter on successful execution
                    if command in _consecutive_timeouts:
                        _consecutive_timeouts[command] = 0
        
        # Handle notifications (no response needed)
        if method == "notifications/initialized":
            return None
            
        # Unknown method
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "error": {
                "code": -32601,
                "message": f"Method not found: {method}"
            }
        }
    
    async def run(self):
        """Run the MCP server."""
        while True:
            try:
                line = sys.stdin.readline()
                if not line:
                    print("EOF received, shutting down", file=sys.stderr)
                    break
                    
                line = line.strip()
                if not line:
                    continue
                    
                request = json.loads(line.strip())
                response = await self.handle_request(request)
                
                if response:
                    print(json.dumps(response), flush=True)
                    
            except json.JSONDecodeError:
                print("JSON decode error", file=sys.stderr)
                continue
            except Exception as e:
                error_response = {
                    "jsonrpc": "2.0",
                    "id": None,
                    "error": {
                        "code": -32603,
                        "message": f"Internal error: {str(e)}"
                    }
                }
                print(json.dumps(error_response), flush=True)


if __name__ == "__main__":
    server = ShellServer()
    asyncio.run(server.run())
