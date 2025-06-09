#!/usr/bin/env python3
"""
MCP server that provides current time functionality.
"""

import asyncio
import json
import subprocess
import sys
import time
import os
from datetime import datetime, timezone
from typing import Dict, Any, Optional


class TimeServer:
    """Simple MCP server that provides time-related tools."""
    
    def __init__(self):
        self.request_id = 0
        # Log to stderr so it doesn't interfere with JSON-RPC communication
        print("Time server starting...", file=sys.stderr)
        
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
                        "name": "time-server",
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
                            "name": "get_current_time",
                            "description": "Get the current date and time",
                            "inputSchema": {
                                "type": "object",
                                "properties": {
                                    "format": {
                                        "type": "string",
                                        "description": "Time format (iso, readable, or timestamp)",
                                        "default": "readable"
                                    }
                                }
                            }
                        }
                    ]
                }
            }
        elif method == "tools/call":
            tool_name = params.get("name")
            arguments = params.get("arguments", {})
            
            if tool_name == "get_current_time":
                format_type = arguments.get("format", "readable")
                
                try:
                    # Get system time directly using the date command
                    result = subprocess.run(['date'], capture_output=True, text=True, timeout=5)
                    if result.returncode == 0:
                        system_date_output = result.stdout.strip()
                        time_str = system_date_output
                    else:
                        # Fallback to Python's time functions
                        local_now = datetime.now()
                        time_str = local_now.strftime("%a %b %d %H:%M:%S %Z %Y")
                except (subprocess.TimeoutExpired, subprocess.SubprocessError, FileNotFoundError):
                    # Fallback to Python's time functions
                    local_now = datetime.now()
                    time_str = local_now.strftime("%a %b %d %H:%M:%S %Z %Y")
                
                # Format according to requested format if not using system date directly
                if format_type != "readable":
                    local_now = datetime.now()
                    system_time = time.time()
                
                    if format_type == "iso":
                        time_str = local_now.isoformat()
                    elif format_type == "timestamp":
                        time_str = str(int(time.time()))
                
                return {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "result": {
                        "content": [
                            {
                                "type": "text",
                                "text": f"Current time: {time_str}"
                            }
                        ]
                    }
                }
        
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
                # Read line synchronously to avoid blocking issues
                line = sys.stdin.readline()
                if not line:
                    print("EOF received, shutting down", file=sys.stderr)
                    break
                    
                line = line.strip()
                if not line:
                    continue
                    
                print(f"Received: {line}", file=sys.stderr)
                
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


def test_server():
    """Test the server with a simple request."""
    import json
    
    server = TimeServer()
    
    # Test initialize request
    init_request = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {}
    }
    
    response = asyncio.run(server.handle_request(init_request))
    print(f"Test response: {json.dumps(response, indent=2)}")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "test":
        test_server()
    else:
        server = TimeServer()
        asyncio.run(server.run())
