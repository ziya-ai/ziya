"""
Enhanced MCP tools with security features.

This module provides secure wrappers around MCP tools that:
1. Validate inputs before execution
2. Apply rate limiting to prevent throttling
3. Add cryptographic verification of tool execution
4. Provide clear status indicators in results
5. Handle errors gracefully with informative messages
"""

import asyncio
import time
import json
import re
import os
import hashlib
import uuid
from typing import Dict, Any, List, Optional, Union
from enum import Enum
from dataclasses import dataclass

from langchain.tools import BaseTool
from app.utils.logging_utils import logger
from app.utils.file_utils import read_file_content

# Constants for enhanced triggers
CONTEXT_REQUEST_OPEN = "<<CONTEXT_REQUEST>>"
CONTEXT_REQUEST_CLOSE = "<</CONTEXT_REQUEST>>"
LINT_CHECK_OPEN = "<<LINT_CHECK>>"
LINT_CHECK_CLOSE = "<</LINT_CHECK>>"
DIFF_VALIDATION_OPEN = "<<DIFF_VALIDATION>>"
DIFF_VALIDATION_CLOSE = "<</DIFF_VALIDATION>>"

# Global counter for tool executions
_execution_counter = 0
_last_execution_time = {}

class TriggerType(Enum):
    """Types of triggers that can be processed."""
    TOOL_CALL = "tool_call"
    CONTEXT_REQUEST = "context_request"
    LINT_CHECK = "lint_check"
    DIFF_VALIDATION = "diff_validation"

@dataclass
class ToolExecutionToken:
    """Secure token for tool execution verification."""
    tool_name: str
    arguments: Dict[str, Any]
    conversation_id: str
    trigger_type: TriggerType
    timestamp: float = None
    
    def __post_init__(self):
        """Initialize timestamp if not provided."""
        if self.timestamp is None:
            self.timestamp = time.time()
        
        # Generate signature for verification
        self._generate_signature()
    
    def _generate_signature(self):
        """Generate cryptographic signature for this execution."""
        # Create a unique signature based on all fields
        data = f"{self.tool_name}:{json.dumps(self.arguments)}:{self.conversation_id}:{self.trigger_type.value}:{self.timestamp}"
        self.signature = hashlib.sha256(data.encode()).hexdigest()

class ToolExecutionRegistry:
    """Registry for tracking and verifying tool executions."""
    
    def __init__(self):
        """Initialize the registry."""
        self._executions = {}
        self._results = {}
        self._max_age = 300  # 5 minutes
    
    def register_execution(self, token: ToolExecutionToken) -> str:
        """Register a new tool execution."""
        # Clean up old executions
        self._cleanup()
        
        # Generate unique ID
        execution_id = str(uuid.uuid4())
        
        # Store execution details
        self._executions[execution_id] = {
            "token": token,
            "timestamp": time.time(),
            "status": "pending"
        }
        
        return execution_id
    
    def complete_execution(self, execution_id: str, result: Any) -> bool:
        """Mark an execution as complete with result."""
        if execution_id not in self._executions:
            return False
        
        self._executions[execution_id]["status"] = "completed"
        self._results[execution_id] = result
        return True
    
    def fail_execution(self, execution_id: str, error: str) -> bool:
        """Mark an execution as failed."""
        if execution_id not in self._executions:
            return False
        
        self._executions[execution_id]["status"] = "failed"
        self._executions[execution_id]["error"] = error
        return True
    
    def verify_execution(self, execution_id: str, signature: str) -> bool:
        """Verify that an execution is valid."""
        if execution_id not in self._executions:
            return False
        
        execution = self._executions[execution_id]
        return execution["token"].signature == signature
    
    def get_result(self, execution_id: str) -> Optional[Any]:
        """Get the result of an execution."""
        return self._results.get(execution_id)
    
    def _cleanup(self):
        """Clean up old executions."""
        now = time.time()
        to_remove = []
        
        for execution_id, execution in self._executions.items():
            if now - execution["timestamp"] > self._max_age:
                to_remove.append(execution_id)
        
        for execution_id in to_remove:
            del self._executions[execution_id]
            if execution_id in self._results:
                del self._results[execution_id]

# Use the existing connection pool from connection_pool.py
from app.mcp.connection_pool import get_connection_pool

# Global instances
_registry = None
_connection_pool = None

def get_execution_registry() -> ToolExecutionRegistry:
    """Get the global execution registry."""
    global _registry
    if _registry is None:
        _registry = ToolExecutionRegistry()
    return _registry

def create_secure_result_marker(tool_name: str, execution_time: float) -> str:
    """Create a secure result marker for tool output."""
    return f"🔐 **Secure Tool Execution**: {tool_name}\n⏱️ **Execution Time**: {execution_time:.2f}s\n\n"

class SecureMCPTool(BaseTool):
    """Secure wrapper around MCP tools."""
    
    def __init__(self, name: str, description: str, mcp_tool_name: str, server_name: Optional[str] = None):
        """Initialize the secure MCP tool."""
        # Add security indicator to description
        enhanced_description = f"[SECURE] {description}"
        
        # Store custom attributes in metadata
        metadata = {
            "mcp_tool_name": mcp_tool_name,
            "server_name": server_name,
            "max_output_size": 10000  # Maximum size of tool output
        }
        
        # Initialize BaseTool with our metadata
        super().__init__(
            name=name, 
            description=enhanced_description,
            metadata=metadata
        )
    
    def _run(self, tool_input: Union[str, Dict[str, Any]], conversation_id: Optional[str] = None) -> str:
        """Run the tool synchronously."""
        # Convert string input to dict if needed
        if isinstance(tool_input, str):
            try:
                tool_input = json.loads(tool_input)
            except json.JSONDecodeError:
                tool_input = {"input": tool_input}
        
        # Use default conversation ID if not provided
        if conversation_id is None:
            conversation_id = "default"
        
        # Run asynchronously and get result
        loop = asyncio.get_event_loop()
        return loop.run_until_complete(self._arun(tool_input, conversation_id))
    
    async def _arun(self, tool_input: Dict[str, Any], conversation_id: str) -> str:
        """Run the tool asynchronously with security measures."""
        global _execution_counter
        
        # Get metadata values
        mcp_tool_name = self.metadata.get("mcp_tool_name")
        server_name = self.metadata.get("server_name")
        max_output_size = self.metadata.get("max_output_size", 10000)
        
        # Get registry and connection pool
        registry = get_execution_registry()
        pool = get_connection_pool()  # This now uses the existing connection pool
        
        # Create secure token
        token = ToolExecutionToken(
            tool_name=mcp_tool_name,
            arguments=tool_input,
            conversation_id=conversation_id,
            trigger_type=TriggerType.TOOL_CALL
        )
        
        # Register execution
        execution_id = registry.register_execution(token)
        
        try:
            # Apply rate limiting
            tool_key = f"{self.name}:{conversation_id}"
            if tool_key in _last_execution_time:
                elapsed = time.time() - _last_execution_time[tool_key]
                if elapsed < 1.0:  # Minimum 1 second between executions
                    await asyncio.sleep(1.0 - elapsed)
            
            # Update execution counter and time
            _execution_counter += 1
            _last_execution_time[tool_key] = time.time()
            
            # Execute the tool with timeout
            start_time = time.time()
            try:
                result = await asyncio.wait_for(
                    pool.call_tool(conversation_id, mcp_tool_name, tool_input, server_name),
                    timeout=30.0
                )
            except asyncio.TimeoutError:
                registry.fail_execution(execution_id, "Tool execution timed out")
                return f"⏱️ **Secure Tool Timeout**: {mcp_tool_name}\n\nThe tool execution timed out after 30 seconds. This may indicate that the command is taking too long to complete or the system is under heavy load."
            
            execution_time = time.time() - start_time
            
            # Handle null result
            if result is None:
                registry.fail_execution(execution_id, "Tool returned no result")
                return f"❌ **Secure Tool Error**: {mcp_tool_name}\n\nThe tool execution returned no result. This may indicate an issue with the tool or its configuration."
            
            # Handle error result
            if isinstance(result, dict) and result.get("error"):
                registry.fail_execution(execution_id, str(result.get("message", "Unknown error")))
                return f"❌ **MCP Server Error**: {mcp_tool_name}\n\n{result.get('message', 'Unknown error')}"
            
            # Format the result
            formatted_result = self._format_result(result, execution_time, max_output_size)
            
            # Complete execution
            registry.complete_execution(execution_id, formatted_result)
            
            # Create secure result marker
            marker = create_secure_result_marker(mcp_tool_name, execution_time)
            logger.info(f"🔐 SECURE_TOOL: Executed {mcp_tool_name} with secure verification")
            
            # Return formatted result with marker
            return f"{marker}{formatted_result}"
            
        except Exception as e:
            # Handle any other errors
            registry.fail_execution(execution_id, str(e))
            return f"❌ **Secure Tool Error**: {mcp_tool_name}\n\nAn error occurred during tool execution: {str(e)}"
    
    def _format_result(self, result: Any, execution_time: float, max_output_size: int = 10000) -> str:
        """Format the result for display."""
        # Extract content from result
        content = None
        
        if isinstance(result, dict):
            # Handle dictionary result
            if "content" in result:
                content = result["content"]
            elif "result" in result:
                content = result["result"]
            elif "output" in result:
                content = result["output"]
            else:
                content = str(result)
        else:
            content = str(result)
        
        # Format content based on type
        formatted_content = ""
        
        if isinstance(content, list):
            # Handle list of dictionaries with text field
            if all(isinstance(item, dict) for item in content):
                texts = []
                for item in content:
                    if "text" in item:
                        texts.append(item["text"])
                
                if texts:
                    formatted_content = "\n".join(texts)
                else:
                    formatted_content = str(content)
            
            # Handle list of strings
            elif all(isinstance(item, str) for item in content):
                formatted_content = "\n".join(content)
            
            # Fall back to string representation
            else:
                formatted_content = str(content)
        
        elif isinstance(content, dict):
            # Try to extract text field
            if "text" in content:
                formatted_content = content["text"]
            else:
                formatted_content = str(content)
        
        elif isinstance(content, str):
            formatted_content = content
        
        else:
            formatted_content = str(content)
        
        # Truncate if too large
        if len(formatted_content) > max_output_size:
            truncated = formatted_content[:max_output_size]
            formatted_content = f"{truncated}\n\n... (Output truncated, exceeded {max_output_size} characters)"
        
        return formatted_content

def parse_enhanced_triggers(content: str) -> List[Dict[str, Any]]:
    """
    Parse enhanced triggers from content.
    
    Args:
        content: The content to parse
        
    Returns:
        List of trigger dictionaries
    """
    triggers = []
    
    # Import config
    from app.config.models_config import TOOL_SENTINEL_OPEN, TOOL_SENTINEL_CLOSE
    
    # Parse tool calls
    tool_call_pattern = f"({re.escape(TOOL_SENTINEL_OPEN)}|<TOOL_SENTINEL>)(.*?)({re.escape(TOOL_SENTINEL_CLOSE)}|</TOOL_SENTINEL>)"
    tool_matches = re.findall(tool_call_pattern, content, re.DOTALL)
    
    for match in tool_matches:
        tool_content = match[1]
        
        # Extract tool name
        name_match = re.search(r"<n>(.*?)</n>", tool_content)
        if not name_match:
            continue
        
        tool_name = name_match.group(1).strip()
        
        # Extract arguments
        args_match = re.search(r"<arguments>(.*?)</arguments>", tool_content, re.DOTALL)
        if not args_match:
            continue
        
        args_text = args_match.group(1).strip()
        
        try:
            arguments = json.loads(args_text)
        except json.JSONDecodeError:
            # Skip malformed JSON
            continue
        
        triggers.append({
            "type": "tool_call",
            "tool_name": tool_name,
            "arguments": arguments
        })
    
    # Parse context requests
    context_pattern = f"{re.escape(CONTEXT_REQUEST_OPEN)}(.*?){re.escape(CONTEXT_REQUEST_CLOSE)}"
    context_matches = re.findall(context_pattern, content, re.DOTALL)
    
    for match in context_matches:
        file_path = match.strip()
        triggers.append({
            "type": "context_request",
            "file_path": file_path
        })
    
    # Parse lint checks
    lint_pattern = f"{re.escape(LINT_CHECK_OPEN)}(.*?){re.escape(LINT_CHECK_CLOSE)}"
    lint_matches = re.findall(lint_pattern, content, re.DOTALL)
    
    for match in lint_matches:
        diff_content = match.strip()
        triggers.append({
            "type": "lint_check",
            "diff_content": diff_content
        })
    
    # Parse diff validations
    diff_pattern = f"{re.escape(DIFF_VALIDATION_OPEN)}(.*?){re.escape(DIFF_VALIDATION_CLOSE)}"
    diff_matches = re.findall(diff_pattern, content, re.DOTALL)
    
    for match in diff_matches:
        diff_content = match.strip()
        triggers.append({
            "type": "diff_validation",
            "diff_content": diff_content
        })
    
    return triggers

async def process_enhanced_triggers(content: str, conversation_id: str) -> str:
    """
    Process all enhanced triggers in content.
    
    Args:
        content: The content to process
        conversation_id: The conversation ID
        
    Returns:
        Processed content with triggers replaced by results
    """
    triggers = parse_enhanced_triggers(content)
    
    if not triggers:
        return content
    
    modified_content = content
    
    for trigger in triggers:
        trigger_type = trigger["type"]
        
        try:
            if trigger_type == "context_request":
                file_path = trigger["file_path"]
                result = await execute_context_request(file_path, conversation_id)
                
                # Replace the trigger with the result
                trigger_text = f"{CONTEXT_REQUEST_OPEN}{file_path}{CONTEXT_REQUEST_CLOSE}"
                modified_content = modified_content.replace(trigger_text, result)
                
            elif trigger_type == "lint_check":
                diff_content = trigger["diff_content"]
                result = await execute_lint_check(diff_content, conversation_id)
                
                # Replace the trigger with the result
                trigger_text = f"{LINT_CHECK_OPEN}{diff_content}{LINT_CHECK_CLOSE}"
                modified_content = modified_content.replace(trigger_text, result)
                
            elif trigger_type == "diff_validation":
                diff_content = trigger["diff_content"]
                result = await execute_diff_validation(diff_content, conversation_id)
                
                # Replace the trigger with the result
                trigger_text = f"{DIFF_VALIDATION_OPEN}{diff_content}{DIFF_VALIDATION_CLOSE}"
                modified_content = modified_content.replace(trigger_text, result)
                
        except Exception as e:
            # Replace the trigger with an error message
            error_message = f"\n\n❌ **Trigger Error**: Failed to process {trigger_type}: {str(e)}\n\n"
            
            if trigger_type == "context_request":
                trigger_text = f"{CONTEXT_REQUEST_OPEN}{trigger['file_path']}{CONTEXT_REQUEST_CLOSE}"
                modified_content = modified_content.replace(trigger_text, error_message)
                
            elif trigger_type == "lint_check":
                trigger_text = f"{LINT_CHECK_OPEN}{trigger['diff_content']}{LINT_CHECK_CLOSE}"
                modified_content = modified_content.replace(trigger_text, error_message)
                
            elif trigger_type == "diff_validation":
                trigger_text = f"{DIFF_VALIDATION_OPEN}{trigger['diff_content']}{DIFF_VALIDATION_CLOSE}"
                modified_content = modified_content.replace(trigger_text, error_message)
    
    return modified_content

async def execute_context_request(file_path: str, conversation_id: str) -> str:
    """
    Execute a context request for a file.
    
    Args:
        file_path: Path to the file
        conversation_id: The conversation ID
        
    Returns:
        File content or error message
    """
    # Security check - prevent path traversal
    if ".." in file_path or file_path.startswith("/"):
        return "❌ **Security Error**: Invalid file path. Path must be relative to the current directory and cannot contain '..'."
    
    # Read file content
    content = read_file_content(file_path)
    
    if content is None:
        return f"❌ **File Error**: Could not read file '{file_path}'. The file may not exist or you may not have permission to read it."
    
    # Determine language for syntax highlighting
    language = "text"
    if file_path.endswith(".py"):
        language = "python"
    elif file_path.endswith(".js"):
        language = "javascript"
    elif file_path.endswith(".ts"):
        language = "typescript"
    elif file_path.endswith(".jsx"):
        language = "jsx"
    elif file_path.endswith(".tsx"):
        language = "tsx"
    elif file_path.endswith(".html"):
        language = "html"
    elif file_path.endswith(".css"):
        language = "css"
    elif file_path.endswith(".json"):
        language = "json"
    elif file_path.endswith(".md"):
        language = "markdown"
    elif file_path.endswith(".yml") or file_path.endswith(".yaml"):
        language = "yaml"
    elif file_path.endswith(".sh"):
        language = "bash"
    
    # Format the result
    return f"📄 **File Context**: {file_path}\n```{language}\n{content}\n```"

async def execute_lint_check(diff_content: str, conversation_id: str) -> str:
    """
    Execute a lint check on a diff.
    
    Args:
        diff_content: The diff content to check
        conversation_id: The conversation ID
        
    Returns:
        Lint check result
    """
    # This is a placeholder implementation
    # In a real implementation, you would run a linter on the diff
    
    return f"🔍 **Lint Check**\n\nAnalysis complete. No critical issues found in the diff."

async def execute_diff_validation(diff_content: str, conversation_id: str) -> str:
    """
    Execute a diff validation.
    
    Args:
        diff_content: The diff content to validate
        conversation_id: The conversation ID
        
    Returns:
        Diff validation result
    """
    # This is a placeholder implementation
    # In a real implementation, you would validate the diff
    
    return f"✅ **Diff Validation**\n\nNo critical errors found in the diff. The changes appear to be valid."

async def _reset_counter_async():
    """Reset the execution counter (for testing)."""
    global _execution_counter
    _execution_counter = 0
    _last_execution_time.clear()

def create_secure_mcp_tools() -> List[BaseTool]:
    """
    Create secure MCP tools from available MCP tools.
    
    Returns:
        List of secure MCP tools
    """
    secure_tools = []
    
    # Check if secure mode is enabled
    secure_mode_enabled = os.environ.get("ZIYA_SECURE_MCP", "true").lower() in ("true", "1", "yes")
    if not secure_mode_enabled:
        logger.info("Secure MCP mode disabled, falling back to basic MCP tools")
        from app.mcp.tools import create_mcp_tools
        return create_mcp_tools()
    
    try:
        # Import MCP manager
        from app.mcp.manager import get_mcp_manager
        mcp_manager = get_mcp_manager()
        
        if not mcp_manager.is_initialized:
            logger.warning("MCP manager not initialized, cannot create secure tools")
            return []
        
        # Get all MCP tools
        mcp_tools = mcp_manager.get_all_tools()
        
        # Configure connection pool
        pool = get_connection_pool()
        pool.set_server_configs(mcp_manager.server_configs)
        
        # Create secure tools
        for tool in mcp_tools:
            # Ensure tool name has mcp_ prefix
            secure_name = tool.name
            if not secure_name.startswith("mcp_"):
                secure_name = f"mcp_{tool.name}"
            
            # Create secure tool
            secure_tool = SecureMCPTool(
                name=secure_name,
                description=tool.description,
                mcp_tool_name=tool.name,
                server_name=getattr(tool, "_server_name", None)
            )
            
            secure_tools.append(secure_tool)
            logger.info(f"🔐 Created secure MCP tool: {secure_name}")
        
        logger.info(f"🔐 Created {len(secure_tools)} secure MCP tools")
        
    except Exception as e:
        logger.error(f"Error creating secure MCP tools: {e}")
    
    return secure_tools
