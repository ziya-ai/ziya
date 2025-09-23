"""
Security components for MCP tool execution.

This module provides security features for MCP tool execution:
1. Tool execution tokens with cryptographic verification
2. Execution registry for tracking and verifying tool executions
3. Trigger types for different kinds of tool executions
"""

import time
import hashlib
import uuid
from typing import Dict, Any, Optional
from enum import Enum
from dataclasses import dataclass

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
        import json
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
