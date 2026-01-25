"""
Transparent cryptographic signing for MCP tool results.

This module provides a generalized security mechanism that prevents
model hallucination of tool results by signing all MCP tool outputs
and verifying signatures before displaying results to the model.

Key features:
- Automatic signing at the MCPClient level (no per-tool code needed)
- HMAC-SHA256 signatures with per-session secrets
- Transparent to tools - works for all MCP tools
- Fail-secure: unsigned results are rejected
"""

import hmac
import hashlib
import time
import secrets
import json
from typing import Dict, Any, Optional, Tuple
from app.utils.logging_utils import logger
from app.utils.logging_utils import get_mode_aware_logger

logger = get_mode_aware_logger(__name__)

# Session secret - generated once at startup, never exposed to model
_session_secret: Optional[bytes] = None

def get_session_secret() -> bytes:
    """Get or generate the session secret for signing."""
    global _session_secret
    if _session_secret is None:
        _session_secret = secrets.token_bytes(32)  # 256-bit secret
        logger.info("🔐 Generated new session secret for MCP tool result signing")
    return _session_secret

def sign_tool_result(
    tool_name: str,
    arguments: Dict[str, Any],
    result: Any,
    conversation_id: str = "default"
) -> Dict[str, Any]:
    """
    Sign a tool result with HMAC-SHA256.
    
    Args:
        tool_name: Name of the tool that was executed
        arguments: Arguments passed to the tool
        result: The result from the tool (will be wrapped with signature)
        conversation_id: Conversation ID for tracking
        
    Returns:
        Result dict with signature metadata added
    """
    timestamp = time.time()
    
    # Normalize result to dict format if needed
    if not isinstance(result, dict):
        result = {"content": [{"type": "text", "text": str(result)}]}
    
    # Ensure result has proper structure
    if "content" not in result:
        result = {"content": [{"type": "text", "text": str(result)}]}
    
    # Create canonical representation of the result content for signing
    result_content = json.dumps(result.get("content", []), sort_keys=True)
    
    # Create message to sign
    message = f"{tool_name}:{json.dumps(arguments, sort_keys=True)}:{result_content}:{timestamp}:{conversation_id}"
    
    # Generate HMAC signature
    secret = get_session_secret()
    signature = hmac.new(secret, message.encode(), hashlib.sha256).hexdigest()
    
    # Add signature metadata to result
    result["_signature"] = signature
    result["_timestamp"] = timestamp
    result["_tool_name"] = tool_name
    result["_arguments"] = arguments
    result["_conversation_id"] = conversation_id
    
    logger.debug(f"🔐 Signed tool result: {tool_name} -> {signature[:16]}...")
    
    return result

def verify_tool_result(
    result: Any,
    tool_name: Optional[str] = None,
    arguments: Optional[Dict[str, Any]] = None
) -> Tuple[bool, Optional[str]]:
    """
    Verify that a tool result has a valid signature.
    
    Args:
        result: The result to verify
        tool_name: Expected tool name (optional - will check against result metadata)
        arguments: Expected arguments (optional - will check against result metadata)
        
    Returns:
        Tuple of (is_valid, error_message)
        - (True, None) if signature is valid
        - (False, error_message) if signature is invalid or missing
    """
    # Record this verification attempt
    try:
        from app.server import record_verification_result
        # We'll call this at the end with results
        should_record = True
    except ImportError:
        # server.py not available (e.g., in tests)
        should_record = False
    
    # Handle non-dict results (shouldn't happen with signed results)
    if not isinstance(result, dict):
        error = "Result is not a dict - signature cannot be verified"
        if should_record and tool_name:
            record_verification_result(tool_name, False, error)
        return (False, error)
    
    # Check for signature metadata
    if "_signature" not in result:
        error = "Result is missing signature - possible hallucination"
        if should_record and tool_name:
            record_verification_result(tool_name or "unknown", False, error)
        return (False, error)
    
    stored_signature = result.get("_signature")
    stored_timestamp = result.get("_timestamp")
    stored_tool_name = result.get("_tool_name")
    stored_arguments = result.get("_arguments")
    stored_conversation_id = result.get("_conversation_id")
    
    # Validate required metadata exists
    if not all([stored_signature, stored_timestamp, stored_tool_name, stored_arguments is not None]):
        error = "Result signature metadata is incomplete"
        if should_record and tool_name:
            record_verification_result(tool_name or stored_tool_name or "unknown", False, error)
        return (False, error)
    
    # Check timestamp is recent (within 5 minutes)
    age = time.time() - stored_timestamp
    if age > 300:  # 5 minutes
        error = f"Result signature is stale ({age:.1f}s old)"
        if should_record and tool_name:
            record_verification_result(tool_name or stored_tool_name, False, error)
        return (False, error)
    
    # Verify tool name matches if provided
    if tool_name and stored_tool_name != tool_name:
        error = f"Tool name mismatch: expected {tool_name}, got {stored_tool_name}"
        if should_record:
            record_verification_result(tool_name, False, error)
        return (False, error)
    
    # Recreate the signature to verify
    result_content = json.dumps(result.get("content", []), sort_keys=True)
    message = f"{stored_tool_name}:{json.dumps(stored_arguments, sort_keys=True)}:{result_content}:{stored_timestamp}:{stored_conversation_id}"
    
    secret = get_session_secret()
    expected_signature = hmac.new(secret, message.encode(), hashlib.sha256).hexdigest()
    
    # Constant-time comparison to prevent timing attacks
    is_valid = hmac.compare_digest(stored_signature, expected_signature)
    
    if not is_valid:
        error = "Result signature verification failed - possible tampering or hallucination"
        if should_record and tool_name:
            record_verification_result(tool_name or stored_tool_name, False, error)
        return (False, error)
    
    logger.debug(f"🔐 Verified tool result: {stored_tool_name} -> signature valid")
    
    # Record successful verification
    if should_record and tool_name:
        record_verification_result(tool_name or stored_tool_name, True, None)
    
    return (True, None)

def strip_signature_metadata(result: Dict[str, Any]) -> Dict[str, Any]:
    """Remove signature metadata before displaying to user (keep internal)."""
    if not isinstance(result, dict):
        return result
    
    # Create a copy without signature metadata
    cleaned = {k: v for k, v in result.items() if not k.startswith("_")}
    return cleaned
