#!/usr/bin/env python3
import asyncio
import json
import boto3
import logging
import re
import os
import threading
import time
from dataclasses import dataclass
from typing import Dict, Any, List, AsyncGenerator, Optional
from app.utils.conversation_filter import filter_conversation_for_model
from app.utils.logging_utils import get_mode_aware_logger
logger = get_mode_aware_logger(__name__)


# Global usage tracker for telemetry
_global_usage_tracker = None
_usage_tracker_lock = threading.Lock()


@dataclass
class IterationUsage:
    """Tracks token usage for a single iteration."""
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    was_throttled: bool = False
    timestamp: float = 0.0
    
    @property
    def cache_hit_rate(self) -> float:
        """Calculate cache hit rate."""
        total_input = self.input_tokens + self.cache_read_tokens
        if total_input == 0:
            return 0.0
        return self.cache_read_tokens / total_input
    
    @property
    def cache_efficiency(self) -> str:
        """Human-readable cache efficiency."""
        return f"{self.cache_hit_rate * 100:.1f}% cached"


class GlobalUsageTracker:
    """Thread-safe global tracker for token usage across all conversations."""
    
    def __init__(self):
        self.conversation_usages: Dict[str, List['IterationUsage']] = {}
        self.lock = threading.Lock()
    
    def record_usage(self, conversation_id: str, usage: 'IterationUsage'):
        """Record usage for a conversation."""
        with self.lock:
            if conversation_id not in self.conversation_usages:
                self.conversation_usages[conversation_id] = []
            
            # Add timestamp
            usage.timestamp = time.time()
            self.conversation_usages[conversation_id].append(usage)
            
            # Cleanup old conversations (keep last 100)
            if len(self.conversation_usages) > 100:
                # Remove oldest conversation
                oldest_conv = min(
                    self.conversation_usages.items(),
                    key=lambda x: x[1][0].timestamp if x[1] else 0
                )
                del self.conversation_usages[oldest_conv[0]]
    
    def get_conversation_usages(self, conversation_id: str) -> List['IterationUsage']:
        """Get all usage records for a conversation."""
        with self.lock:
            return self.conversation_usages.get(conversation_id, []).copy()
    
    def get_all_conversations(self) -> Dict[str, List['IterationUsage']]:
        """Get all conversation usage data."""
        with self.lock:
            return self.conversation_usages.copy()


def get_global_usage_tracker() -> GlobalUsageTracker:
    """Get or create the global usage tracker singleton."""
    global _global_usage_tracker
    with _usage_tracker_lock:
        if _global_usage_tracker is None:
            _global_usage_tracker = GlobalUsageTracker()
        return _global_usage_tracker


def validate_tool_args_against_schema(tool_name: str, args: dict, schema: dict) -> Optional[str]:
    """
    Validate tool arguments against the tool's input schema.
    
    Returns None if valid, or a self-correcting error message string if invalid.
    This enables the model to automatically retry with corrected parameters.
    """
    if not schema:
        return None
    
    properties = schema.get('properties', {})
    required = schema.get('required', [])
    
    errors = []
    
    # Check required parameters
    for param in required:
        if param not in args or args[param] is None or args[param] == '':
            param_info = properties.get(param, {})
            param_desc = param_info.get('description', 'No description')
            errors.append(f"- '{param}' is REQUIRED but missing. Description: {param_desc}")
    
    # Check enum values
    for param, value in args.items():
        if param in properties:
            param_schema = properties[param]
            allowed_values = param_schema.get('enum')
            if allowed_values and value not in allowed_values:
                errors.append(f"- '{param}' value '{value}' is invalid. Allowed values: {allowed_values}")
    
    if not errors:
        return None
    
    # Build self-correcting error message
    error_lines = [
        "TOOL CALL FAILED - PARAMETER VALIDATION ERROR",
        "",
        f"You called: {tool_name}",
        f"You provided: {json.dumps(args)}",
        "",
        "PROBLEMS FOUND:",
    ]
    error_lines.extend(errors)
    error_lines.append("")
    error_lines.append(f"Required parameters: {required if required else 'None'}")
    
    # Add parameter details for guidance
    if properties:
        error_lines.append("")
        error_lines.append("Parameter details:")
        for param, param_schema in properties.items():
            param_type = param_schema.get('type', 'any')
            param_desc = param_schema.get('description', '')
            param_enum = param_schema.get('enum')
            req_marker = " (REQUIRED)" if param in required else ""
            
            line = f"- {param}{req_marker}: {param_type}"
            if param_enum:
                line += f" - allowed: {param_enum}"
            error_lines.append(line)
            if param_desc:
                # Truncate long descriptions
                desc_preview = param_desc[:100] + "..." if len(param_desc) > 100 else param_desc
                error_lines.append(f"    {desc_preview}")
    
    error_lines.append("")
    error_lines.append("Retry with corrected parameters.")
    
    return "\n".join(error_lines)


class StreamingToolExecutor:
    def __init__(self, profile_name: str = 'ziya', region: str = 'us-west-2', model_id: str = None):
        
        # Only initialize Bedrock client for Bedrock endpoints
        from app.agents.models import ModelManager
        endpoint = os.environ.get("ZIYA_ENDPOINT", "bedrock")
        model_name = os.environ.get("ZIYA_MODEL")
        self.model_config = ModelManager.get_model_config(endpoint, model_name)
        
        # Use provided model_id or get from ModelManager (which handles region-specific IDs)
        if model_id:
            self.model_id = model_id
            logger.debug(f"StreamingToolExecutor: Using provided model_id: {self.model_id}")
        else:
            config_model_id = self.model_config.get('model_id') if self.model_config else None
            if config_model_id:
                # Use ModelManager's region-aware resolution
                self.model_id, _ = ModelManager._get_region_specific_model_id_with_region_update(
                    config_model_id, region, self.model_config, model_name
                )
                logger.debug(f"StreamingToolExecutor: Resolved model_id from config: {self.model_id} (config was: {config_model_id})")
            else:
                raise ValueError("No model_id configured. Set ZIYA_MODEL or provide model_id parameter.")
        
        if endpoint == "bedrock":
            # Use ModelManager's wrapped bedrock client for proper extended context handling
            try:
                self.bedrock = ModelManager._get_persistent_bedrock_client(
                    aws_profile=profile_name,
                    region=region,
                    model_id=self.model_id,
                    model_config=self.model_config
                )
                logger.debug(f"🔍 Using ModelManager's wrapped bedrock client with extended context support")
            except Exception as e:
                logger.warning(f"🔍 Could not get wrapped client, falling back to direct client: {e}")
                # Fallback to direct client creation
                session = boto3.Session(profile_name=profile_name)
                self.bedrock = session.client('bedrock-runtime', region_name=region)
        else:
            # Non-Bedrock endpoints don't need a bedrock client
            self.bedrock = None
            logger.debug(f"🔍 Skipping Bedrock client initialization for endpoint: {endpoint}")

    @staticmethod
    def _normalize_tool_name(tool_name: str) -> str:
        """
        Normalize tool names to handle common malformations.
        Examples:
            'mcp_run_shell_command' -> 'run_shell_command'
            'mcp_$mcp_run_shell_command' -> 'run_shell_command'
            'run_shell_command' -> 'run_shell_command'
        """
        # Remove all 'mcp_' prefixes (handles repeated prefixes like mcp_$mcp_)
        normalized = tool_name
        while normalized.startswith('mcp_') or '_mcp_' in normalized:
            normalized = normalized.replace('mcp_', '', 1)
            # Also clean up any remaining $ or special chars after mcp_ removal
            normalized = normalized.lstrip('$_')
        return normalized

    def _decode_chunk_bytes(self, chunk_bytes):
        """
        Safely decode chunk bytes to string for json.loads().
        Handles both bytes and string types for compatibility across Python/boto3 versions.
        """
        if isinstance(chunk_bytes, bytes):
            try:
                return chunk_bytes.decode('utf-8')
            except UnicodeDecodeError as e:
                logger.error(f"Failed to decode chunk bytes: {e}")
                raise
        elif isinstance(chunk_bytes, str):
            return chunk_bytes
        else:
            raise TypeError(f"Unexpected chunk type: {type(chunk_bytes)}")

    def _convert_tool_schema(self, tool):
        """Convert tool schema to JSON-serializable format"""
        if isinstance(tool, dict):
            # Already a dict, but check input_schema
            result = tool.copy()
            input_schema = result.get('input_schema')
            if isinstance(input_schema, dict):
                # Already a dict, use as-is
                pass
            elif hasattr(input_schema, 'model_json_schema'):
                # Pydantic class - convert to JSON schema
                result['input_schema'] = input_schema.model_json_schema()
            elif input_schema is not None:
                # Some other object - try to convert
                try:
                    result['input_schema'] = input_schema.model_json_schema()
                except Exception:
                    logger.warning(f"🔍 TOOL_SCHEMA: Could not convert input_schema, using fallback")
                    result['input_schema'] = {"type": "object", "properties": {}}
            return result
        else:
            # Tool object - extract properties
            name = getattr(tool, 'name', 'unknown')
            description = getattr(tool, 'description', 'No description')
            
            # Try multiple ways to get the schema
            input_schema = getattr(tool, 'input_schema', None)
            if input_schema is None:
                input_schema = getattr(tool, 'inputSchema', None)
            # For SecureMCPTool, check metadata
            if input_schema is None and hasattr(tool, 'metadata'):
                input_schema = tool.metadata.get('input_schema', {})
            if input_schema is None:
                input_schema = {}
            
            logger.debug(f"🔍 TOOL_SCHEMA: Converting tool '{name}', input_schema type: {type(input_schema)}")
            
            # Handle different input_schema types
            if isinstance(input_schema, dict):
                # Already a dict, use as-is
                logger.debug(f"🔍 TOOL_SCHEMA: Tool '{name}' has dict schema with keys: {list(input_schema.keys())}")
            elif hasattr(input_schema, 'model_json_schema'):
                # Pydantic class - convert to JSON schema
                input_schema = input_schema.model_json_schema()
                logger.debug(f"🔍 TOOL_SCHEMA: Converted Pydantic schema for '{name}'")
            elif input_schema:
                # Some other object - try to convert
                try:
                    input_schema = input_schema.model_json_schema()
                    logger.debug(f"🔍 TOOL_SCHEMA: Converted object schema for '{name}'")
                except Exception:
                    logger.warning(f"🔍 TOOL_SCHEMA: Failed to convert schema for '{name}', using empty schema")
                    input_schema = {"type": "object", "properties": {}}
            else:
                logger.warning(f"🔍 TOOL_SCHEMA: Tool '{name}' has no input_schema, using empty schema")
                input_schema = {"type": "object", "properties": {}}
            
            result = {
                'name': name,
                'description': description,
                'input_schema': input_schema
            }
            logger.debug(f"🔍 TOOL_SCHEMA: Final schema for '{name}': {json.dumps(result, indent=2)}")
            return result

    def _commands_similar(self, cmd1: str, cmd2: str) -> bool:
        """Check if two shell commands are functionally similar"""
        # Only consider commands similar if they are nearly identical
        # Remove minor variations like different head counts
        def normalize(cmd):
            return cmd.replace('head -20', 'head').replace('head -30', 'head').replace(' | head', '').strip()
        
        norm1, norm2 = normalize(cmd1), normalize(cmd2)
        
        # Only consider exact matches as similar to avoid blocking legitimate exploration
        return norm1 == norm2

    def _format_tool_result(self, tool_name: str, result_text: str, args: dict) -> str:
        """Format tool result based on tool type."""
        actual_tool_name = self._normalize_tool_name(tool_name)
        
        if actual_tool_name == 'run_shell_command':
            # For shell commands, return result as-is - frontend will add command to header
            return result_text
        elif actual_tool_name == 'get_current_time':
            # For time tool, clean up the result format
            clean_result = result_text
            # Remove "Input: {}" prefix if present
            clean_result = clean_result.replace('Input: {}\n\nResult:\n', '').strip()
            clean_result = clean_result.replace('Input: {}\n\n', '').strip()
            clean_result = clean_result.replace('Result:\n', '').strip()
            # Remove any remaining "Result:" prefix
            if clean_result.startswith('Result:'):
                clean_result = clean_result[7:].strip()
            return clean_result
        else:
            # For other tools, return result as-is
            return result_text
    
    def _get_tool_header(self, tool_name: str, args: dict) -> str:
        """Get appropriate header for tool display."""
        actual_tool_name = self._normalize_tool_name(tool_name)
        
        if actual_tool_name == 'run_shell_command':
            return 'Shell Command'
        elif actual_tool_name == 'get_current_time':
            return 'Current Time'
        else:
            return actual_tool_name.replace('_', ' ').title()

    def _get_text_after_last_structured_content(self, text: str) -> str:
        """Get text that appears after the last tool result, diff block, or code block."""
        # Find the last occurrence of structured content markers
        last_positions = []
        
        # Check for tool blocks
        tool_pattern = r'```?```'
        for match in re.finditer(tool_pattern, text, re.DOTALL):
            last_positions.append(match.end())
        
        # Check for diff blocks  
        diff_pattern = r'```diff.*?```'
        for match in re.finditer(diff_pattern, text, re.DOTALL):
            last_positions.append(match.end())
            
        # Check for any code blocks
        code_pattern = r'```.*?```'
        for match in re.finditer(code_pattern, text, re.DOTALL):
            last_positions.append(match.end())
        
        if last_positions:
            # Return text after the last structured content block
            last_pos = max(last_positions)
            return text[last_pos:].strip()
        else:
            # No structured content found, return the entire text
            return text.strip()

    async def _execute_fake_tool(self, tool_name, command, assistant_text, tool_results, mcp_manager):
        """Execute a fake tool call detected in the text stream"""
        actual_tool_name = self._normalize_tool_name(tool_name)
        if actual_tool_name == 'run_shell_command':
            try:
                result = await mcp_manager.call_tool('run_shell_command', {'command': command.strip()})
                
                if isinstance(result, dict) and 'content' in result:
                    content = result['content']
                    if isinstance(content, list) and len(content) > 0:
                        result_text = content[0].get('text', str(result))
                    else:
                        result_text = str(result)
                else:
                    result_text = str(result)
                
                tool_results.append({
                    'tool_id': f'fake_{len(tool_results)}',
                    'tool_name': tool_name,
                    'result': result_text
                })
                
                return {
                    'type': 'tool_display',
                    'tool_id': f'fake_{len(tool_results)}',
                    'tool_name': tool_name,
                    'result': result_text
                }
            except Exception as e:
                logger.error(f"Error executing intercepted tool call: {e}")
                return None

    def _extract_file_contents_from_messages(self, messages: List[Dict[str, Any]], system_content=None) -> Dict[str, str]:
        """
        Extract file contents from messages for calibration.
        
        Returns:
            Dict mapping file_path -> content
        """
        if system_content:
            first_file = system_content.find('File: ') if isinstance(system_content, str) else -1
            logger.debug(f"📊 EXTRACT: {system_content.count('File: ')} files, {first_file:,} chars overhead")
        
        file_contents = {}
        
        # Check system content first (this is where files are in StreamingToolExecutor)
        content_to_parse = None
        
        if system_content:
            # System content passed directly
            content_to_parse = system_content
        else:
            # Look for system messages in message list
            for message in messages:
                if message.get('role') == 'system':
                    content_to_parse = message.get('content', '')
                    break
        
        if not content_to_parse:
            return {}
            
            
        if isinstance(content_to_parse, list):
            # Handle multi-part content
            content_to_parse = ' '.join(block.get('text', '') for block in content_to_parse if block.get('type') == 'text')
        
        # Parse file sections from system message
        # Format: "File: path/to/file.py\n[content]\n\n"
        if 'File: ' in content_to_parse:
            logger.info(f"📊 EXTRACT: Found 'File: ' in content, starting parse...")
            logger.info(f"📊 EXTRACT: Content preview (first 500 chars): {content_to_parse[:500]}")
            
            # Find where the first file content starts
            first_file_pos = content_to_parse.find('File: ')
            first_file_pos = max(0, first_file_pos)  # Ensure it's always defined
            if first_file_pos > 0:
                logger.info(f"📊 EXTRACT: First file starts at position {first_file_pos}")
                logger.info(f"📊 EXTRACT: First file section preview: {content_to_parse[first_file_pos:first_file_pos+200]}")
            
            lines = content_to_parse.split('\n')
            current_file = None
            current_content = []
            files_found = 0
            lines_processed = 0
            lines_skipped_as_line_numbers = 0
            
            for line in lines:
                lines_processed += 1
                
                if line.startswith('File: '):
                    files_found += 1
                    logger.info(f"📊 EXTRACT: Found file marker #{files_found}: '{line}'")
                    
                    # Save previous file
                    if current_file and current_content:
                        file_contents[current_file] = '\n'.join(current_content)
                        logger.debug(f"📊 EXTRACT: Saved file '{current_file}' with {len(current_content)} lines")
                    elif current_file and not current_content:
                        logger.warning(f"📊 EXTRACT: File '{current_file}' had NO content lines!")
                    
                    # Start new file
                    current_file = line[6:].strip()
                    current_content = []
                    logger.debug(f"📊 EXTRACT: Starting new file '{current_file}'")
                elif current_file:
                        # Skip line number annotations like [001+]
                        # Pattern: [DIGITS<marker>] where DIGITS is 3+ digits, marker is space/+/*
                        # CRITICAL FIX: Don't skip the line, extract content AFTER the marker!
                        actual_line_content = line  # Default: use whole line if no marker
                        
                        if line.startswith('[') and len(line) >= 6:
                            # Find closing bracket
                            bracket_pos = line.find(']', 1)
                            if bracket_pos > 1 and bracket_pos <= 8:  # Support [001 ] to [999999*]
                                inner = line[1:bracket_pos]
                                # Check: at least 3 digits followed by space/+/*
                                if len(inner) >= 4:
                                    digits = inner[:-1]
                                    marker = inner[-1]
                                    if digits.isdigit() and marker in [' ', '+', '*']:
                                        # This IS a line number - extract content after '] '
                                        actual_line_content = line[bracket_pos + 2:] if bracket_pos + 2 < len(line) else ''
                                        lines_skipped_as_line_numbers += 1
                        
                        # DEBUG: Log first few content decisions
                        if lines_processed < first_file_pos + 10:
                            logger.debug(f"📊 LINE_DEBUG: Line {lines_processed}: extracted='{actual_line_content[:80]}'")
                        
                        # Always append the extracted content (even if empty, to preserve line structure)
                        if actual_line_content is not None:
                            current_content.append(actual_line_content)
                
            # Save last file
            if current_file and current_content:
                file_contents[current_file] = '\n'.join(current_content)
                logger.debug(f"📊 EXTRACT: Saved final file '{current_file}' with {len(current_content)} lines")
                
                logger.info(f"📊 EXTRACT: Processed {lines_processed:,} lines, found {files_found} files, extracted {len(file_contents)} files, skipped {lines_skipped_as_line_numbers:,} line number annotations")
                
                # Log total extracted content
                total_extracted_chars = sum(len(content) for content in file_contents.values())
                logger.info(f"📊 EXTRACT: Total extracted content: {total_extracted_chars:,} chars from {len(file_contents)} files")
        
        logger.debug(f"📊 CALIBRATION: Extracted {len(file_contents)} files from messages")
        for path in list(file_contents.keys())[:3]:
            logger.debug(f"   {path}: {len(file_contents[path]):,} chars")
        
        return file_contents

    def _cleanup_iteration_resources(self):
        """Clean up iteration-specific resources to prevent memory leaks."""
        # Remove content optimizer - it's per-iteration state
        if hasattr(self, '_content_optimizer'):
            delattr(self, '_content_optimizer')
        if hasattr(self, '_block_opening_buffer'):
            delattr(self, '_block_opening_buffer')

    def _prepare_messages_with_cache_control(self, conversation: List[Dict[str, Any]], iteration: int) -> List[Dict[str, Any]]:
        """
        Prepare messages with cache_control applied to optimize token reuse.
        
        TEMPORARILY DISABLED: Bedrock has a 4-block cache_control limit.
        With system prompt + nested conversation blocks, we exceed this limit.
        
        Strategy:
        - System prompt: Always cached (handled separately)
        - Recent conversation: Cache the last N messages to reuse tool results
        - Keep adding new messages without cache markers
        
        This ensures:
        1. System prompt cached once (codebase files)
        2. Conversation history cached incrementally
        3. Only NEW tool calls/results are fresh tokens
        """
        if iteration == 0 or len(conversation) < 6:
            # First iteration or very short conversation - no conversation caching needed
            return conversation
        
        # CRITICAL: Bedrock allows max 4 cache_control blocks total
        # System prompt uses 1, leaving us 3 for conversation
        # We must consolidate to stay within limits
        
        # CRITICAL: Deep copy to avoid modifying original
        import copy
        messages = copy.deepcopy(conversation)
        total_messages = len(messages)
        
        # STEP 1: Remove ALL existing cache_control markers from conversation
        # This prevents accumulation across iterations
        for msg in messages:
            content = msg.get('content')
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and 'cache_control' in block:
                        del block['cache_control']
        
        logger.debug(f"🔍 CONV_CACHE: Cleaned existing cache markers from {total_messages} messages")
        
        # Keep last 4 messages fresh for rapid tool iteration
        # Last 4 = [assistant with tool_use, user with tool_result, assistant, user]
        cache_boundary = total_messages - 4
        
        if cache_boundary <= 0:
            return messages
        
        # STEP 2: Apply single NEW cache_control at boundary
        # This replaces all old markers with one new marker
        # Total blocks: 1 (system) + 1 (conversation boundary) = 2/4
        msg_at_boundary = messages[cache_boundary]
        content = msg_at_boundary.get('content')
        
        if isinstance(content, str):
            # Simple string - wrap with cache_control
            messages[cache_boundary]['content'] = [
                {
                    "type": "text",
                    "text": content,
                    "cache_control": {"type": "ephemeral"}
                }
            ]
            logger.info(f"🔍 CONV_CACHE: Applied cache at message {cache_boundary} (string content)")
            logger.debug(f"🔍 CONV_CACHE: Applied cache at message {cache_boundary} (string content)")            
        elif isinstance(content, list) and len(content) > 0:
            # Multi-block content - add cache_control to LAST block only
            last_block = content[-1]
            
            if 'cache_control' not in last_block:
                last_block['cache_control'] = {"type": "ephemeral"}
                logger.debug(f"🔍 CONV_CACHE: Applied cache at message {cache_boundary} (multi-block)")
        
        logger.debug(f"🔍 CONV_CACHE: Cache point at message {cache_boundary}/{total_messages}")
        logger.debug(f"   Total blocks: 1 (system) + 1 (conversation boundary) = 2/4 ✓")
        logger.debug(f"   Messages cached: {cache_boundary}, Fresh: {total_messages - cache_boundary}")
        
        return messages

    async def stream_with_tools(self, messages: List[Dict[str, Any]], tools: Optional[List] = None, conversation_id: Optional[str] = None, project_root: Optional[str] = None) -> AsyncGenerator[Dict[str, Any], None]:
        # Initialize streaming metrics
        stream_metrics = {
            'events_sent': 0,
            'bytes_sent': 0,
            'chunk_sizes': [],
            'start_time': time.time()
        }
        
        def track_yield(event_data):
            """Track metrics for yielded events"""
            chunk_size = len(json.dumps(event_data))
            stream_metrics['events_sent'] += 1
            stream_metrics['bytes_sent'] += chunk_size
            stream_metrics['chunk_sizes'].append(chunk_size)
            
            if stream_metrics['events_sent'] % 100 == 0:
                logger.info(f"📊 Stream metrics: {stream_metrics['events_sent']} events, "
                           f"{stream_metrics['bytes_sent']} bytes, "
                           f"avg={stream_metrics['bytes_sent']/stream_metrics['events_sent']:.2f}")
            return event_data
        
        # Extended context handling for sonnet4.5
        if conversation_id:
            logger.debug(f"🔍 EXTENDED_CONTEXT: Processing conversation_id = {conversation_id}")
            # Set conversation_id in custom_bedrock module global so CustomBedrockClient can use it
            try:
                import app.utils.custom_bedrock as custom_bedrock_module
                custom_bedrock_module._current_conversation_id = conversation_id
                logger.debug(f"🔍 EXTENDED_CONTEXT: Set module global conversation_id")
            except Exception as e:
                logger.warning(f"🔍 EXTENDED_CONTEXT: Could not set conversation_id: {e}")
        
        # Get MCP tools
        from app.mcp.manager import get_mcp_manager
        mcp_manager = get_mcp_manager()
        if not mcp_manager.is_initialized:
            await mcp_manager.initialize()
        from app.mcp.enhanced_tools import DirectMCPTool

        # Get ALL tools (both MCP server tools and builtin tools)
        from app.mcp.enhanced_tools import create_secure_mcp_tools
        all_tools = create_secure_mcp_tools()
        
        # Separate builtin from external MCP tools for proper naming
        builtin_tool_names = {tool.name for tool in all_tools if isinstance(tool, DirectMCPTool)}
        
        logger.debug(f"🔍 TOOL_LOADING: Total tools={len(all_tools)}, builtin={len(builtin_tool_names)}, external={len(all_tools)-len(builtin_tool_names)}")
        logger.debug(f"🔍 BUILTIN_TOOLS: {sorted(builtin_tool_names)}")
        
        # Convert ALL tools to JSON-serializable format and deduplicate by name
        converted_tools = [self._convert_tool_schema(tool) for tool in all_tools]
        
        # Deduplicate tools by name (keep first occurrence)
        seen_names = set()
        bedrock_tools = []
        for tool in converted_tools:
            tool_name = tool.get('name', 'unknown')
            if tool_name not in seen_names:
                seen_names.add(tool_name)
                # Add mcp_ prefix only for actual MCP tools, not builtin tools
                if not tool_name.startswith('mcp_') and tool_name not in builtin_tool_names:
                    tool['name'] = f'mcp_{tool_name}'
                bedrock_tools.append(tool)

        # Build conversation
        conversation = []
        system_content = None

        logger.debug(f"🔍 STREAMING_TOOL_EXECUTOR: Received {len(messages)} messages")
        for i, msg in enumerate(messages):
            # Handle both dict format and LangChain message objects
            if hasattr(msg, 'type') and hasattr(msg, 'content'):
                # LangChain message object
                role = msg.type if msg.type != 'human' else 'user'
                content = msg.content
            elif isinstance(msg, str):
                # String format - treat as user message
                role = 'user'
                content = msg
            else:
                # Dict format
                role = msg.get('role', '')
                content = msg.get('content', '')
            
            logger.debug(f"🔍 STREAMING_TOOL_EXECUTOR: Message {i}: role={role}, content_length={len(content)}")
            
            # CRITICAL: Preserve list content for multi-modal (images)
            if isinstance(content, list):
                logger.debug(f"🖼️ STREAMING_TOOL_EXECUTOR: Message {i} has multi-modal content with {len(content)} blocks")
            
            if role == 'system':
                system_content = content
                logger.debug(f"🔍 STREAMING_TOOL_EXECUTOR: Found system message with {len(content)} characters")
            elif role in ['user', 'assistant', 'ai']:
                # Normalize ai role to assistant for Bedrock
                bedrock_role = 'assistant' if role == 'ai' else role
                conversation.append({"role": bedrock_role, "content": content})

        # Iterative execution with proper tool result handling
        recent_commands = []  # Track recent commands to prevent duplicates
        using_extended_context = False  # Track if we've enabled extended context
        consecutive_empty_tool_calls = 0  # Track empty tool calls to break loops
        
        # Intelligent throttle backoff state
        throttle_state = {
            'retry_count': 0,
            'max_retries': 5,
            'base_delay': 2,
            'last_cache_efficiency': 0.0,
            'cache_working': None,  # None=unknown, True=working, False=broken
            'output_tokens_reduction_factor': 1.0,
        }
        
        # Track cumulative usage across all iterations
        cumulative_usage = IterationUsage()
        iteration_usages: List[IterationUsage] = []
        
        # Check if baseline needs to be established
        # Only run baseline if:
        # 1. We have calibration loaded
        # 2. The baseline for this model family is NOT already established
        # 3. This is the first iteration of the first request
        should_establish_baseline = False
        if conversation_id and system_content:
            try:
                from app.utils.token_calibrator import get_token_calibrator
                calibrator = get_token_calibrator()
                
                from app.agents.models import ModelManager
                endpoint = os.environ.get("ZIYA_ENDPOINT", "bedrock")
                model_name = os.environ.get("ZIYA_MODEL")
                model_config = ModelManager.get_model_config(endpoint, model_name)
                model_family = model_config.get('family', 'claude')
                
                # Only establish baseline if not already measured
                should_establish_baseline = model_family not in calibrator.baselines_measured
            except Exception as e:
                logger.debug(f"Could not check baseline status: {e}")
        
        for iteration in range(100):  # Increased limit to support complex multi-step tasks
            logger.debug(f"🔍 ITERATION_START: Beginning iteration {iteration}")
            
            # Suppress verbose iteration logs in chat mode
            chat_mode = os.environ.get('ZIYA_MODE', 'server') == 'chat'
            if chat_mode and iteration > 0:
                # Only log errors in chat mode after first iteration
                pass
            
            # BASELINE ESTABLISHMENT: Only once per model family
            if should_establish_baseline:
                should_establish_baseline = False  # Only run once
                logger.info(f"📊 BASELINE: Establishing baseline for {model_family} (first time)")
                
                try:
                    # Count MCP tools for baseline measurement
                    mcp_tool_count = len(bedrock_tools) if bedrock_tools else 0
                    
                    # Use system_content as-is, just replace file section with placeholder
                    # This ensures cache structure matches real requests perfectly
                    baseline_system_text = system_content  # Default to full content
                    if isinstance(system_content, str) and 'Below is the current codebase of the user:' in system_content:
                        parts = system_content.split('Below is the current codebase of the user:')
                        baseline_system_text = parts[0] + "\n\nBelow is the current codebase of the user:\n\n(No files selected)"
                    
                    logger.info(f"📊 BASELINE: {len(baseline_system_text):,} chars, {mcp_tool_count} tools")
                    
                    # Make baseline request
                    baseline_body = {
                        "anthropic_version": "bedrock-2023-05-31",
                        "max_tokens": 100,
                        "messages": [{"role": "user", "content": "Hello"}],
                        "system": [
                            {
                                "type": "text",
                                "text": baseline_system_text,
                                "cache_control": {"type": "ephemeral"}
                            }
                        ]
                    }
                    
                    # Add tools to match real request structure
                    if bedrock_tools:
                        baseline_body["tools"] = bedrock_tools
                    
                    # Use existing client's underlying boto3 client directly to avoid wrapper recursion
                    # Access the raw client to bypass CustomBedrockClient wrapper
                    # Unwrap all layers: ThrottleSafeBedrock -> CustomBedrockClient -> boto3 client
                    raw_client = self.bedrock
                    while hasattr(raw_client, 'client') and raw_client.client != raw_client:
                        logger.debug(f"📊 BASELINE: Unwrapping layer: {type(raw_client).__name__}")
                        raw_client = raw_client.client
                    
                    logger.info(f"📊 BASELINE: Using raw client type: {type(raw_client).__name__}")
                    
                    baseline_response = raw_client.invoke_model(
                        modelId=self.model_id,
                        body=json.dumps(baseline_body)
                    )
                    
                    baseline_response_body = json.loads(baseline_response['body'].read())
                    
                    # DEBUG: Log the full response to see what we actually got
                    logger.info(f"📊 BASELINE_RESPONSE: Keys in response: {list(baseline_response_body.keys())}")
                    baseline_usage = baseline_response_body.get('usage', {})
                    logger.info(f"📊 BASELINE_USAGE: {baseline_usage}")
                    
                    # CRITICAL: Use TOTAL input (fresh + cached + cache_creation) for baseline
                    # On FIRST baseline call: cache is being CREATED, so use cache_creation_input_tokens
                    # On SUBSEQUENT calls: cache exists, so use cache_read_input_tokens
                    baseline_fresh = baseline_usage.get('input_tokens', 0)
                    baseline_cached = baseline_usage.get('cache_read_input_tokens', 0)
                    baseline_cache_created = baseline_usage.get('cache_creation_input_tokens', 0)
                    
                    # Total = fresh + (cached OR created)
                    baseline_tokens = baseline_fresh + baseline_cached + baseline_cache_created
                    
                    logger.info(f"📊 BASELINE_TOTAL: {baseline_tokens:,} tokens (fresh: {baseline_fresh:,}, cached: {baseline_cached:,})")
                    if baseline_cache_created > 0:
                        logger.info(f"📊 BASELINE_CACHE_CREATED: {baseline_cache_created:,} tokens (first baseline call)")
                    
                    # Also check if stop_reason indicates an error
                    stop_reason = baseline_response_body.get('stop_reason')
                    if stop_reason:
                        logger.info(f"📊 BASELINE_STOP: stop_reason={stop_reason}")
                        
                        # Check content to see if model actually responded
                        content = baseline_response_body.get('content', [])
                        logger.info(f"📊 BASELINE_CONTENT: {len(content)} content blocks")
                        
                        # Validate - use baseline_system_text length as proxy
                        # Rough estimate: 1 token per 3-4 chars for text, ~500 tokens per tool
                        expected_min = len(baseline_system_text) // 6 + mcp_tool_count * 300
                        expected_max = len(baseline_system_text) // 2 + mcp_tool_count * 1500
                        
                        if baseline_tokens < expected_min or baseline_tokens > expected_max:
                            logger.warning(f"📊 BASELINE: Invalid measurement {baseline_tokens:,} (expected {expected_min:,}-{expected_max:,})")
                        else:
                            # Store the baseline overhead (system prompt + tools)
                            calibrator.baseline_overhead_tokens[model_family] = baseline_tokens
                            calibrator.baselines_measured.add(model_family)
                            calibrator._save_calibration_data()
                            if not chat_mode:
                                logger.info(f"✅ BASELINE: Established {baseline_tokens:,} tokens")
                                logger.info(f"   System prompt: {len(baseline_system_text):,} chars")
                                logger.info(f"   MCP tools: {mcp_tool_count}")
                            logger.debug(f"📊 BASELINE: Baseline established, will not run again for {model_family}")
                except Exception as e:
                    logger.debug(f"📊 BASELINE: Establishment failed (will retry next time): {e}")
                    logger.warning(f"📊 BASELINE: Establishment failed (will retry next time): {e}")
            
            # WARNING: Approaching iteration limit - notify model to wrap up
            iterations_remaining = 100 - iteration
            warning_message = None
            
            if iterations_remaining == 5:
                warning_message = (
                    "\n\n⚠️ **Iteration Limit Notice:** You have 5 iterations remaining in this cycle. "
                    "Please begin wrapping up your current discovery and prepare to summarize your findings.\n\n"
                )
                logger.warning(f"🔔 ITERATION_WARNING: 5 iterations remaining, notifying model")
            elif iterations_remaining == 2:
                warning_message = (
                    "\n\n⚠️ **Iteration Limit Warning:** You have only 2 iterations remaining in this cycle. "
                    "Please conclude your current work and provide a summary of what you've discovered. "
                    "Focus on completing your current task rather than starting new explorations.\n\n"
                )
                logger.warning(f"🔔 ITERATION_WARNING: 2 iterations remaining, notifying model")
            elif iterations_remaining == 1:
                warning_message = (
                    "\n\n🚨 **FINAL ITERATION:** This is your last iteration in this cycle. "
                    "You must provide your final response now. Summarize what you've accomplished and "
                    "any remaining recommendations. Do not attempt to use tools in this iteration.\n\n"
                )
                logger.warning(f"🔔 ITERATION_WARNING: Final iteration, notifying model")
            
            # Inject warning message into conversation if needed
            if warning_message:
                yield track_yield({'type': 'text', 'content': warning_message})
                await asyncio.sleep(0.1)  # Ensure message is sent
            
            # Check for user feedback at the start of each iteration
            if conversation_id and iteration > 0:  # Skip check on first iteration
                try:
                    from app.server import active_feedback_connections
                    if conversation_id in active_feedback_connections:
                        feedback_queue = active_feedback_connections[conversation_id]['feedback_queue']
                        try:
                            feedback_data = feedback_queue.get_nowait()
                            if feedback_data.get('type') == 'tool_feedback':
                                feedback_message = feedback_data.get('message', '')
                                logger.info(f"🔄 FEEDBACK_INTEGRATION: Iteration-level feedback: {feedback_message}")
                                if any(stop_word in feedback_message.lower() for stop_word in ['stop', 'halt', 'abort', 'cancel', 'quit']):
                                    yield track_yield({'type': 'text', 'content': f"\n\n**User feedback:** {feedback_message}\n**Stopping execution as requested.**\n\n"})
                                    yield track_yield({'type': 'stream_end'})
                                    return
                                else:
                                    # Handle directive feedback at iteration level
                                    logger.info(f"🔄 FEEDBACK_INTEGRATION: Iteration-level directive: {feedback_message}")
                                    
                                    # Add feedback to conversation so model can respond
                                    conversation.append({
                                        "role": "user",
                                        "content": f"[User feedback]: {feedback_message}"
                                    })
                                    logger.info(f"🔄 FEEDBACK_DELIVERED: Added iteration-level feedback to conversation at iteration {iteration}")
                                    
                                    # Let user know feedback was received
                                    yield track_yield({
                                        'type': 'text',
                                        'content': f"\n\n**Feedback received:** {feedback_message}\n**Adjusting approach...**\n\n"
                                    })
                                    
                                    # Continue with the iteration, but now the conversation includes user feedback
                                    logger.info(f"🔄 FEEDBACK_INTEGRATION: Added feedback to conversation, continuing iteration")
                        except asyncio.QueueEmpty:
                            pass
                except Exception as e:
                    logger.debug(f"Error checking iteration feedback: {e}")
            
            # Log last 2 messages to debug conversation state
            if len(conversation) >= 2:
                for i, msg in enumerate(conversation[-2:]):
                    role = msg.get('role', msg.get('type', 'unknown'))
                    content = msg.get('content', '')
                    content_preview = str(content)[:150] if content else 'empty'
                    logger.debug(f"🔍 CONV_DEBUG: Message -{2-i}: role={role}, content_preview={content_preview}")
            
            tools_executed_this_iteration = False  # Track if tools were executed in this iteration
            blocked_tools_this_iteration = 0  # Track blocked tools to prevent runaway loops
            commands_this_iteration = []  # Track commands executed in this specific iteration
            empty_tool_calls_this_iteration = 0  # Track empty tool calls in this iteration
            
            # Safety guard: prevent sending conversation ending with assistant message
            # to models that don't support assistant prefill (e.g. Opus 4 via Bedrock)
            if (iteration > 0 and conversation and
                    conversation[-1].get('role') == 'assistant' and
                    not self.model_config.get('supports_assistant_prefill', True)):
                logger.info(
                    f"🛑 PREFILL_GUARD: Iteration {iteration} would send conversation ending "
                    f"with assistant message to non-prefill model. Ending stream."
                )
                yield {'type': 'stream_end'}
                break

            body = {
                "anthropic_version": "bedrock-2023-05-31",
                "max_tokens": self.model_config.get('max_output_tokens', 4000),
                "messages": self._prepare_messages_with_cache_control(conversation, iteration)
            }

            # DEBUG: Log what we're actually sending
            logger.debug(f"🔍 REQUEST_DEBUG: Iteration {iteration}")
            logger.debug(f"   Messages in request: {len(conversation)}")
            logger.debug(f"   Max tokens: {body['max_tokens']}")
            for i, msg in enumerate(conversation[:2]):  # First 2 messages only
                content = msg.get('content', '')
                content_len = len(content) if isinstance(content, str) else sum(len(b.get('text', '')) for b in content if b.get('type') == 'text')
                logger.info(f"   Message {i} ({msg['role']}): {content_len:,} chars")
            
            if system_content:
                # With precision prompts, system content is already clean - no regex needed
                logger.debug(f"🔍 SYSTEM_DEBUG: Using clean system content length: {len(system_content)}")
                logger.debug(f"🔍 SYSTEM_DEBUG: File count in system content: {system_content.count('File:')}")
                
                # Log cache control setup for debugging
                logger.debug(f"🔍 CACHE_SETUP: Iteration {iteration}")
                logger.debug(f"   System content length: {len(system_content):,} chars")
                logger.debug(f"   Conversation messages: {len(conversation)}")
                
                # Use system_content as-is - prompt system handles all formatting
                system_text = system_content
                
                # Use prompt caching for large system prompts to speed up iterations
                if len(system_text) > 1024:
                    body["system"] = [
                        {
                            "type": "text",
                            "text": system_text,
                            "cache_control": {"type": "ephemeral"}
                        }
                    ]
                    logger.debug(f"🔍 CACHE: Enabled prompt caching for {len(system_text)} char system prompt")
                    logger.debug(f"🔍 CACHE_CONTROL: Set cache_control ephemeral on system message")
                    logger.debug(f"   Expected cache creation: ~{len(system_text) // 4:,} tokens")
                else:
                    body["system"] = system_text
                    logger.warning(f"🔍 CACHE_CONTROL: NOT using cache_control (system too small: {len(system_text)} chars)")
                
                logger.debug(f"🔍 SYSTEM_DEBUG: Final system prompt length: {len(system_text)}")
                logger.debug(f"🔍 SYSTEM_CONTENT_DEBUG: First 500 chars of system prompt: {system_text[:500]}")
                logger.debug(f"🔍 SYSTEM_CONTENT_DEBUG: System prompt contains 'File:' count: {system_text.count('File:')}")
                logger.debug(f"🔍 SYSTEM_CONTENT_DEBUG: Last 500 chars of system prompt: {system_text[-500:]}")
            
            # If we've already enabled extended context, keep using it
            if using_extended_context and self.model_config:
                header_value = self.model_config.get('extended_context_header')
                if header_value:
                    body['anthropic_beta'] = [header_value]
                    logger.debug(f"🔍 EXTENDED_CONTEXT: Continuing with extended context header")

            if bedrock_tools:
                # Don't send tools if we've had too many consecutive empty calls
                if consecutive_empty_tool_calls >= 5:
                    logger.warning(f"🔍 TOOL_SUPPRESSION: Suppressing tools due to {consecutive_empty_tool_calls} consecutive empty calls")
                    # Don't add tools to body - force model to respond without them
                else:
                    body["tools"] = bedrock_tools
                    # Use "auto" to allow model to decide when to stop
                    body["tool_choice"] = {"type": "auto"}
                    logger.debug(f"🔍 TOOL_DEBUG: Sending {len(bedrock_tools)} tools to model: {[t['name'] for t in bedrock_tools]}")

            try:
                # Exponential backoff for rate limiting
                max_retries = 4
                base_delay = 2  # Start with 2 seconds
                iteration_start_time = time.time()
                
                # Store original max_tokens for potential reduction
                original_max_tokens = body.get('max_tokens', 4000)
                
                # Initialize tool_results early so it's available in exception handlers
                tool_results = []
                tool_use_blocks = []
                
                # Track usage for this specific iteration
                iteration_usage = IterationUsage()
                
                for retry_attempt in range(max_retries + 1):
                    try:
                        api_params = {
                            'modelId': self.model_id,
                            'body': json.dumps(body)
                        }
                        
                        logger.debug(f"🔍 API_PARAMS: Calling invoke_model_with_response_stream with modelId={self.model_id}")
                        response = self.bedrock.invoke_model_with_response_stream(**api_params)
                        break  # Success, exit retry loop
                    except Exception as e:
                        error_str = str(e)
                        is_rate_limit = ("Too many tokens" in error_str or 
                                       "ThrottlingException" in error_str or
                                       "Too many requests" in error_str)
                        is_context_limit = "Input is too long" in error_str or "too large" in error_str
                        
                        # On context limit error, enable extended context and retry
                        if is_context_limit and not using_extended_context and self.model_config:
                            if self.model_config.get('supports_extended_context'):
                                header_value = self.model_config.get('extended_context_header')
                                if header_value:
                                    logger.debug(f"🔍 EXTENDED_CONTEXT: Context limit hit, enabling extended context with header {header_value}")
                                    body['anthropic_beta'] = [header_value]
                                    api_params['body'] = json.dumps(body)
                                    using_extended_context = True  # Set flag to keep using it
                                    try:
                                        response = self.bedrock.invoke_model_with_response_stream(**api_params)
                                        break
                                    except Exception as retry_error:
                                        logger.error(f"🔍 EXTENDED_CONTEXT: Retry with extended context failed: {retry_error}")
                                        raise
                        
                        if is_rate_limit and retry_attempt < max_retries:
                            # Exponential backoff with longer delays to allow token bucket refill
                            # boto3 already did fast retries, so we need longer waits
                            delay = base_delay * (2 ** retry_attempt) + 4  # Add 4s base to account for boto3 retries
                            logger.warning(f"Rate limit hit, retrying in {delay}s (attempt {retry_attempt + 1}/{max_retries + 1})")
                            await asyncio.sleep(delay)
                        else:
                            raise  # Re-raise if not rate limit or max retries exceeded

                # Process this iteration's stream - collect ALL tool calls first
                assistant_text = ""
                yielded_text_length = 0  # Track how much text we've yielded
                all_tool_calls = []  # Collect all tool calls from this response
                
                active_tools = {}
                completed_tools = set()
                expected_tools = set()
                skipped_tools = set()  # Track tools we're skipping due to limits
                executed_tool_signatures = set()  # Track tool name + args to prevent duplicates
                
                # Timeout protection - use configured timeout from shell config
                last_activity_time = time.time()
                from app.config.shell_config import DEFAULT_SHELL_CONFIG
                chunk_timeout = int(os.environ.get('COMMAND_TIMEOUT', DEFAULT_SHELL_CONFIG["timeout"]))

                # Initialize content buffer and visualization detector
                content_buffer = ""
                viz_buffer = ""  # Track potential visualization blocks
                in_viz_block = False
                
                # Code block continuation tracking
                code_block_tracker = {
                    'in_block': False,
                    'block_type': None,
                    'accumulated_content': ''
                }
                
                # Track event count for debugging
                event_count = 0
                
                for event in response['body']:
                    event_count += 1
                    
                    # Decode chunk once for all processing
                    if 'chunk' not in event:
                        continue
                    
                    chunk_bytes = event['chunk']['bytes']
                    chunk_str = self._decode_chunk_bytes(chunk_bytes)
                    chunk = json.loads(chunk_str)
                    
                    
                    # ZERO-COST TELEMETRY: Extract usage from decoded chunks
                    # Metrics are INSIDE the chunk JSON, not at event level!
                    if 'amazon-bedrock-invocationMetrics' in chunk:
                        metrics = chunk['amazon-bedrock-invocationMetrics']
                        
                        iteration_usage.input_tokens = metrics.get('inputTokenCount', 0)
                        iteration_usage.output_tokens = metrics.get('outputTokenCount', 0)
                        iteration_usage.cache_read_tokens = metrics.get('cacheReadInputTokenCount', 0)
                        iteration_usage.cache_write_tokens = metrics.get('cacheWriteInputTokenCount', 0)
                        
                        # Compute derived values for logging
                        total_input = iteration_usage.input_tokens + iteration_usage.cache_read_tokens
                        fresh = iteration_usage.input_tokens
                        cached = iteration_usage.cache_read_tokens
                        
                        # DEBUG: Log ALL fields in metrics to see what we're getting
                        if iteration == 0:
                            logger.info(f"🔍 METRICS_DEBUG: All fields in metrics:")
                            for key, value in metrics.items():
                                throttle_state['cache_working'] = False
                        elif cached > 0:
                            throttle_state['cache_working'] = True
                            throttle_state['last_cache_efficiency'] = iteration_usage.cache_hit_rate
                            logger.debug(f"✅ CACHE WORKING: {cached:,} tokens reused")
                        
                            # CRITICAL WARNING: High token counts increase throttle risk
                            if total_input > 400000:
                                logger.warning("⚠️  HIGH THROTTLE RISK: Processing {total_input:,} total tokens")
                                logger.warning(f"   Even though {cached:,} are cached (free),")
                                logger.warning(f"   they STILL count toward 'Too many tokens' rate limits")
                                logger.warning(f"   Consider reducing max_output_tokens on retries")
                        
                        # ACCURACY TRACKING: Compare our estimate to actual
                        if iteration == 0 and conversation_id:  # Only on first iteration
                            try:
                                # CRITICAL: Check if we have calibration data available
                                # If yes, use calibrated estimates; if no, use naive 4.0 baseline
                                try:
                                    from app.utils.token_calibrator import get_token_calibrator
                                    calibrator = get_token_calibrator()
                                    has_calibration = True
                                    logger.info(f"📊 ESTIMATE: Loaded calibrator for accuracy check")
                                except (ImportError, FileNotFoundError, PermissionError) as e:
                                    logger.warning(f"📊 CALIBRATION_UNAVAILABLE: {type(e).__name__}: {e}")
                                    calibrator = None
                                    has_calibration = False
                                except Exception as e:
                                    # Log unexpected errors but don't break the flow
                                    logger.error(f"📊 CALIBRATION_ERROR: Unexpected error loading calibrator: {e}")
                                    calibrator = None
                                    has_calibration = False
                                except Exception as e:
                                    # Log unexpected errors but don't break the flow
                                    logger.error(f"📊 CALIBRATION_ERROR: Unexpected error loading calibrator: {e}")
                                    calibrator = None
                                    has_calibration = False
                                
                                # Calculate what we ESTIMATED this would cost
                                estimated_tokens = 0
                                estimation_method = "naive (4.0 chars/token)"
                                
                                # CRITICAL FIX: Get model_family ONCE before estimation loop
                                # This ensures calibrated data is used during estimation
                                estimation_model_family = None
                                if has_calibration:
                                    try:
                                        from app.agents.models import ModelManager
                                        
                                        model_id = ModelManager.get_model_id()
                                        if isinstance(model_id, dict):
                                            model_id = list(model_id.values())[0]
                                        
                                        endpoint = os.environ.get("ZIYA_ENDPOINT", "bedrock")
                                        model_name = os.environ.get("ZIYA_MODEL")
                                        model_config = ModelManager.get_model_config(endpoint, model_name)
                                        estimation_model_family = model_config.get('family', 'claude')
                                        
                                        logger.info(f"📊 ESTIMATE-FAMILY: Using model_family='{estimation_model_family}' for estimation")
                                    except Exception as e:
                                        logger.warning(f"📊 ESTIMATE-FAMILY: Failed to get model family: {e}, using 'claude' as fallback")
                                        estimation_model_family = 'claude'  # Fallback to claude instead of default
                                
                                for msg in conversation:
                                    content = msg.get('content', '')
                                    if isinstance(content, str):
                                        if has_calibration:
                                            estimated_tokens += calibrator.estimate_tokens(content, model_family=estimation_model_family)
                                            estimation_method = "calibrated"
                                        else:
                                            estimated_tokens += len(content) // 4
                                    elif isinstance(content, list):
                                        for block in content:
                                            if block.get('type') == 'text':
                                                text = block.get('text', '')
                                                if has_calibration:
                                                    estimated_tokens += calibrator.estimate_tokens(text, model_family=estimation_model_family)
                                                else:
                                                    estimated_tokens += len(text) // 4
                                
                                # ALSO include system content if present
                                if system_content:
                                    if isinstance(system_content, str):
                                        if has_calibration:
                                            estimated_tokens += calibrator.estimate_tokens(system_content, model_family=estimation_model_family)
                                        else:
                                            estimated_tokens += len(system_content) // 4
                                    elif isinstance(system_content, list):
                                        for block in system_content:
                                            if (isinstance(block, dict) and block.get('type') == 'text'):
                                                text = block.get('text', '')
                                                if has_calibration:
                                                    estimated_tokens += calibrator.estimate_tokens(text, model_family=estimation_model_family)
                                                else:
                                                    estimated_tokens += len(text) // 4
                                
                                # Add back overhead that was subtracted during recording
                                if has_calibration and estimation_model_family:
                                    try:
                                        baseline_overhead = calibrator.get_baseline_overhead(
                                            model_family=estimation_model_family
                                        )
                                        if baseline_overhead > 0:
                                            estimated_tokens += baseline_overhead
                                            logger.info(f"📊 ESTIMATE_OVERHEAD: Added {baseline_overhead:,} baseline tokens")
                                        else:
                                            # No baseline yet, use conservative estimate
                                            logger.info(f"📊 ESTIMATE_OVERHEAD: No baseline measured yet")
                                    except Exception as e:
                                        logger.debug(f"Could not add MCP overhead to estimate: {e}")
                                
                                # Compare to actual
                                # NOTE: actual_tokens here is fresh + cached, which is what matters for throttling
                                # Otherwise use fresh + cache_read
                                cache_written = iteration_usage.cache_write_tokens
                                if cache_written > 0:
                                    # First request - cache being created
                                    actual_tokens = fresh + cache_written
                                else:
                                    # Subsequent request - using cached content
                                    actual_tokens = total_input
                                
                                estimation_error = abs(estimated_tokens - actual_tokens)
                                error_pct = (estimation_error / actual_tokens * 100) if actual_tokens > 0 else 0
                                
                                logger.info("=" * 80)
                                logger.info("📊 ESTIMATION ACCURACY CHECK")
                                logger.info("=" * 80)
                                logger.info(f"   Our Estimate:   {estimated_tokens:>8,} tokens ({estimation_method})")
                                logger.info(f"   Bedrock Total:  {actual_tokens:>8,} tokens (fresh + cached)")
                                logger.info(f"     └─ Fresh:     {fresh:>8,} tokens (billable)")
                                logger.info(f"     └─ Cached:    {cached:>8,} tokens (free but counts for throttle)")
                                if cache_written > 0:
                                    logger.info(f"     └─ Written:   {cache_written:>8,} tokens (cache creation)")
                                    logger.info(f"   Note: Using fresh + written for comparison (first request)")
                                
                                logger.info(f"   Error:          {estimation_error:>8,} tokens (±{error_pct:.1f}%)")
                                
                                # Log comparison
                                accuracy_status = "✅ Excellent" if error_pct < 5 else "⚠️ Fair" if error_pct < 15 else "❌ Poor"
                                # Only log if accuracy is concerning
                                if error_pct >= 15:
                                    logger.warning(f"   Accuracy:       {accuracy_status}")
                                
                                if error_pct > 15:
                                    logger.warning("   ⚠️  Estimation is significantly off!")
                                    logger.warning("   💡 Calibration will improve this over time")
                                elif error_pct < 5:
                                    logger.info("   ✅ Calibration is working well!")
                                
                                logger.info("=" * 80 + "\n")
                                
                            except Exception as e:
                                logger.debug(f"Error in accuracy tracking: {e}")
                    
                        # CALIBRATION: Record actual usage for future estimate improvement
                        # This happens automatically - no user action needed
                        logger.debug(f"📊 Calibration: iter={iteration}, total_input={total_input:,}, cache_write={iteration_usage.cache_write_tokens:,}")
                        
                        if iteration == 0:  # Only on first iteration to get clean baseline
                            logger.info(f"📊 DEBUG: Entering calibration block (iteration 0)")
                            try:
                                logger.info(f"📊 DEBUG: Attempting to import token_calibrator...")
                                try:
                                    from app.utils.token_calibrator import get_token_calibrator
                                except ImportError as import_err:
                                    logger.error(f"📊 CALIBRATION IMPORT FAILED: {import_err}")
                                    raise
                                
                                logger.info(f"📊 DEBUG: Successfully imported, getting calibrator instance...")
                                
                                calibrator = get_token_calibrator()
                                logger.info(f"📊 DEBUG: Got calibrator instance, extracting file contents...")
                                
                                # Extract file contents from the conversation
                                file_contents = self._extract_file_contents_from_messages(conversation, system_content)
                                
                                logger.info(f"📊 DEBUG: Extracted {len(file_contents)} files for calibration, total_input={total_input}")
                                
                                if file_contents and total_input > 0:
                                    logger.info(f"📊 DEBUG: Conditions met, proceeding with calibration...")
                                    # Get current model info
                                    from app.agents.models import ModelManager
                                    
                                    model_id = ModelManager.get_model_id()
                                    if isinstance(model_id, dict):
                                        model_id = list(model_id.values())[0]
                                    
                                    endpoint = os.environ.get("ZIYA_ENDPOINT", "bedrock")
                                    model_name = os.environ.get("ZIYA_MODEL")
                                    model_config = ModelManager.get_model_config(endpoint, model_name)
                                    model_family = model_config.get('family', 'default')
                                    
                                    # CRITICAL: Use correct token count for calibration
                                    # CRITICAL: Use TOTAL input (fresh + cached) for throttle-aware calibration
                                    # Cached tokens are free for billing but STILL count for rate limits
                                    calibration_tokens = fresh + cached
                                    if iteration_usage.cache_write_tokens > 0:
                                        # First iteration: also include cache creation
                                        calibration_tokens += iteration_usage.cache_write_tokens
                                    
                                    logger.debug(f"📊 CALIBRATION: {calibration_tokens:,} tokens from {len(file_contents)} files, {sum(len(c) for c in file_contents.values()):,} chars")
                                    
                                    # Get baseline overhead (established on first request)
                                    baseline_overhead = calibrator.get_baseline_overhead(model_family)
                                    
                                    # Estimate chat history tokens (small overhead from conversation)
                                    chat_tokens = 0
                                    for msg in conversation:
                                        content = msg.get('content', '')
                                        if isinstance(content, str):
                                            chat_tokens += len(content) // 4
                                        elif isinstance(content, list):
                                            for block in content:
                                                if block.get('type') == 'text':
                                                    chat_tokens += len(block.get('text', '')) // 4
                                    
                                    # Subtract fixed costs (baseline + chat) to get file-only tokens
                                    # This allows calibrator to learn pure file tokenization rate
                                    file_only_tokens = max(1, calibration_tokens - baseline_overhead - chat_tokens)
                                    
                                    logger.info(f"📊 CALIBRATION: Total={calibration_tokens:,}, Baseline={baseline_overhead:,}, "
                                               f"Chat={chat_tokens:,}, File-only={file_only_tokens:,}")
                                    
                                    # Record actual token usage for calibration
                                    calibrator.record_actual_usage(
                                        conversation_id=conversation_id,
                                        file_contents=file_contents,
                                        actual_tokens=file_only_tokens,  # File tokens only!
                                        model_id=str(model_id),
                                        model_family=model_family
                                    )
                                    
                                    logger.debug(f"📊 CALIBRATION: Recorded {len(file_contents)} files for {model_family}")
                                    
                            except Exception as calib_error:
                                logger.error(f"📊 CALIBRATION ERROR: {calib_error}")
                                import traceback
                                logger.error(f"📊 CALIBRATION TRACEBACK:\n{traceback.format_exc()}")
                    
                    if chunk['type'] == 'content_block_start':
                        # We already decoded the chunk above for metrics, reuse it
                        content_block = chunk.get('content_block', {})
                        logger.debug(f"🔍 CHUNK_DEBUG: content_block_start - type: {content_block.get('type')}, id: {content_block.get('id')}")
                        if content_block.get('type') == 'tool_use':
                            # FLUSH any buffered content before tool starts
                            if hasattr(self, '_content_optimizer'):
                                remaining = self._content_optimizer.flush_remaining()
                                if remaining:
                                    yield track_yield({
                                        'type': 'text',
                                        'content': remaining,
                                        'timestamp': f"{int((time.time() - iteration_start_time) * 1000)}ms"
                                    })
                            if content_buffer.strip():
                                yield track_yield({
                                    'type': 'text',
                                    'content': content_buffer,
                                    'timestamp': f"{int((time.time() - iteration_start_time) * 1000)}ms"
                                })
                                content_buffer = ""
                            
                            tool_id = content_block.get('id')
                            tool_name = content_block.get('name')
                            if tool_id and tool_name:
                                # Check for duplicates FIRST
                                tool_signature = f"{tool_name}_{tool_id}"
                                if tool_signature in executed_tool_signatures:
                                    logger.debug(f"🔍 DUPLICATE_SKIP: Tool {tool_signature} already executed")
                                    skipped_tools.add(chunk.get('index'))
                                    continue
                                
                                # Send tool_start event to frontend only (not to model)
                                # This prevents contamination of model training data
                                
                                # Mark as executed to prevent duplicates
                                executed_tool_signatures.add(tool_signature)
                                
                                # Collect tool call instead of executing immediately
                                all_tool_calls.append({
                                    'id': tool_id,
                                    'name': tool_name,
                                    'args': {}
                                })
                                logger.debug(f"🔍 COLLECTED_TOOL: {tool_name} (id: {tool_id})")
                                
                                active_tools[tool_id] = {
                                    'name': tool_name,
                                    'partial_json': '',
                                    'index': chunk.get('index')
                                }

                    elif chunk['type'] == 'content_block_delta':
                        delta = chunk.get('delta', {})
                        tool_id = chunk.get('index')  # Get tool ID from chunk index
                        
                        # Skip processing if this tool is in our skipped set
                        if tool_id in skipped_tools:
                            continue
                            
                        if delta.get('type') == 'text_delta':
                            text = delta.get('text', '')
                            
                            # Buffer incomplete code block openings to prevent malformed types
                            if not hasattr(self, '_block_opening_buffer'):
                                self._block_opening_buffer = ""
                            
                            # Check if we have a buffered incomplete opening
                            if self._block_opening_buffer:
                                text = self._block_opening_buffer + text
                                self._block_opening_buffer = ""
                            
                            # Check if text ends with incomplete code block opening
                            if text.endswith('```') or (text.endswith('`') and text[-3:] != '```'):
                                # Might be incomplete, buffer it
                                self._block_opening_buffer = text
                                continue
                            elif '```' in text:
                                # Has opening backticks, check if line is complete
                                lines = text.split('\n')
                                last_line = lines[-1]
                                if last_line.strip().startswith('```') and not last_line.strip().endswith('```'):
                                    # Incomplete opening line (e.g., "```vega-" without newline)
                                    # Buffer the last line, process the rest
                                    if len(lines) > 1:
                                        text = '\n'.join(lines[:-1]) + '\n'
                                        self._block_opening_buffer = last_line
                                    else:
                                        self._block_opening_buffer = text
                                        continue
                            
                            assistant_text += text
                            
                            # Check for fake tool calls in the text and intercept them
                            # DISABLED: This was causing premature execution of incomplete commands
                            if False and (('```tool:' in assistant_text and '```' in assistant_text[assistant_text.find('```tool:') + 8:]) or \
                               ('run_shell_command\n$' in assistant_text and '\n' in assistant_text[assistant_text.find('run_shell_command\n$') + 20:]) or \
                              (':mcp_run_shell_command\n$' in assistant_text and '\n' in assistant_text[assistant_text.find(':mcp_run_shell_command\n$') + 23:])):
                                # Extract and execute fake tool calls with multiple patterns
                                patterns = [
                                    r'```tool:(mcp_\w+)\n\$\s*([^`]+)```',  # Full markdown blocks only
                                    r'run_shell_command\n\$\s*([^\n]+)\n',    # Complete lines only
                                    r':mcp_run_shell_command\n\$\s*([^\n]+)\n' # Complete lines only
                                ]
                                
                                for pattern in patterns:
                                    if pattern.startswith('```tool:'):
                                        matches = re.findall(pattern, assistant_text)
                                        for tool_name, command in matches:
                                            result = await self._execute_fake_tool(tool_name, command, assistant_text, tool_results, mcp_manager)
                                            if result:
                                                yield result
                                    else:
                                        matches = re.findall(pattern, assistant_text)
                                        for command in matches:
                                            result = await self._execute_fake_tool('mcp_run_shell_command', command, assistant_text, tool_results, mcp_manager)
                                            if result:
                                                yield result
                                for pattern in patterns:
                                    if re.search(pattern, text):
                                        logger.warning(f"🚫 Intercepted fake tool call: {pattern}")
                                        # FLUSH optimizer before skipping fake tool patterns
                                        if hasattr(self, '_content_optimizer'):
                                            remaining = self._content_optimizer.flush_remaining()
                                            if remaining:
                                                yield track_yield({
                                                    'type': 'text',
                                                    'content': remaining,
                                                    'timestamp': f"{int((time.time() - iteration_start_time) * 1000)}ms"
                                                })
                            if '```tool:' in text or '`tool:' in text:
                                # FLUSH optimizer before skipping fake tool patterns
                                if hasattr(self, '_content_optimizer'):
                                    remaining = self._content_optimizer.flush_remaining()
                                    if remaining:
                                        yield track_yield({
                                            'type': 'text',
                                            'content': remaining,
                                            'timestamp': f"{int((time.time() - iteration_start_time) * 1000)}ms"
                                        })
                                continue
                            
                            # Initialize content optimizer if not exists
                            if not hasattr(self, '_content_optimizer'):
                                from app.utils.streaming_optimizer import StreamingContentOptimizer
                                self._content_optimizer = StreamingContentOptimizer()
                            
                            
                            if '```tool:' in text or '`tool:' in text:
                                if hasattr(self, '_content_optimizer'):
                                    remaining = self._content_optimizer.flush_remaining()
                                    if remaining:
                                        yield track_yield({
                                            'type': 'text',
                                            'content': remaining,
                                            'timestamp': f"{int((time.time() - iteration_start_time) * 1000)}ms"
                                        })
                                continue
                            
                            # Check for visualization block boundaries - ensure proper markdown format
                            viz_patterns = ['```vega-lite', '```mermaid', '```graphviz', '```d3']
                            has_viz_pattern = any(pattern in text for pattern in viz_patterns) or (viz_buffer and any(pattern in viz_buffer + text for pattern in viz_patterns))
                            
                            if has_viz_pattern:
                                # If we're already in a viz block and see a new opening, send the previous one first
                                if in_viz_block and any(pattern in text for pattern in viz_patterns):
                                    # New viz block starting - send accumulated buffer first
                                    if viz_buffer.strip():
                                        self._update_code_block_tracker(viz_buffer, code_block_tracker)
                                        yield track_yield({
                                            'type': 'text',
                                            'content': viz_buffer,
                                            'timestamp': f"{int((time.time() - iteration_start_time) * 1000)}ms"
                                        })
                                    viz_buffer = text
                                    in_viz_block = True
                                elif not in_viz_block:
                                    # FLUSH optimizer before starting viz block
                                    if hasattr(self, '_content_optimizer'):
                                        remaining = self._content_optimizer.flush_remaining()
                                        if remaining:
                                            yield track_yield({
                                                'type': 'text',
                                                'content': remaining,
                                                'timestamp': f"{int((time.time() - iteration_start_time) * 1000)}ms"
                                            })
                                    in_viz_block = True
                                    viz_buffer = text
                                else:
                                    viz_buffer += text
                                continue
                            elif in_viz_block:
                                viz_buffer += text
                                # Check for closing ``` in accumulated buffer
                                has_closing = any(line.strip() == '```' for line in viz_buffer.split('\n'))
                                if has_closing:
                                    # Complete visualization block - send immediately
                                    self._update_code_block_tracker(viz_buffer, code_block_tracker)
                                    yield track_yield({
                                        'type': 'text',
                                        'content': viz_buffer,
                                        'timestamp': f"{int((time.time() - iteration_start_time) * 1000)}ms"
                                    })
                                    viz_buffer = ""
                                    in_viz_block = False
                                continue
                            
                            # Use content optimizer to prevent mid-word splits
                            for optimized_chunk in self._content_optimizer.add_content(text):
                                self._update_code_block_tracker(optimized_chunk, code_block_tracker)
                                yield track_yield({
                                    'type': 'text',
                                    'content': optimized_chunk,
                                    'timestamp': f"{int((time.time() - iteration_start_time) * 1000)}ms"
                                })
                        elif delta.get('type') == 'input_json_delta':
                            # Find tool by index
                            tool_id = None
                            for tid, tdata in active_tools.items():
                                if tdata.get('index') == chunk.get('index'):
                                    tool_id = tid
                                    break
                            if tool_id:
                                active_tools[tool_id]['partial_json'] += delta.get('partial_json', '')
                                logger.debug(f"🔍 JSON_DELTA: Tool {tool_id} received delta: '{delta.get('partial_json', '')}'")
                                logger.debug(f"🔍 JSON_ACCUMULATED: Tool {tool_id} total: '{active_tools[tool_id]['partial_json']}'")

                    elif chunk['type'] == 'content_block_stop':
                        # Find and execute tool
                        tool_id = None
                        for tid, tdata in active_tools.items():
                            if tdata.get('index') == chunk.get('index'):
                                tool_id = tid
                                break
                        
                        if tool_id and tool_id not in completed_tools:
                            tool_data = active_tools[tool_id]
                            tool_name = tool_data['name']
                            args_json = tool_data['partial_json']
                            
                            # Handle empty args_json - treat as empty object for tools with no required params
                            if not args_json or not args_json.strip():
                                # Tools with no required parameters can have empty args
                                # Set to empty object and let execution proceed
                                args_json = '{}'
                                logger.debug(f"🔍 EMPTY_JSON: Tool {tool_name} has no argument JSON, using empty object")
                            
                            # Validate JSON is complete (starts with { and ends with })
                            if not (args_json.strip().startswith('{') and args_json.strip().endswith('}')):
                                logger.error(f"🔍 INCOMPLETE_JSON: Tool {tool_name} has incomplete JSON: {args_json}")
                                empty_tool_calls_this_iteration += 1
                                consecutive_empty_tool_calls += 1
                                
                                # Send self-correcting error to model
                                error_result = f"""TOOL CALL FAILED - INCOMPLETE JSON

You called: {tool_name}
You provided incomplete JSON: {args_json}

PROBLEM: The JSON arguments were truncated or malformed.

This usually means:
- The JSON string is incomplete (missing closing braces)
- The tool call was cut off during generation

Please retry the tool call with complete, valid JSON parameters."""
                                
                                tool_results.append({'tool_id': tool_id, 'tool_name': tool_name, 'result': error_result})
                                yield {'type': 'tool_result_for_model', 'tool_use_id': tool_id, 'content': error_result}
                                completed_tools.add(tool_id)
                                tools_executed_this_iteration = True
                                continue
                            logger.debug(f"🔍 TOOL_ARGS: Tool '{tool_name}' (id: {tool_id}) has args_json: '{args_json}'")

                            try:
                                args = json.loads(args_json) if args_json.strip() else {}
                                
                                # CRITICAL: Unwrap tool_input wrapper if present
                                # Models may send {"tool_input": {"command": "..."}} instead of {"command": "..."}
                                # This normalization must happen BEFORE schema validation
                                if isinstance(args, dict) and 'tool_input' in args:
                                    tool_input = args['tool_input']
                                    # Handle nested JSON string
                                    if isinstance(tool_input, str):
                                        try:
                                            tool_input = json.loads(tool_input)
                                        except json.JSONDecodeError:
                                            pass  # Keep as string if not valid JSON
                                    if isinstance(tool_input, dict):
                                        logger.debug(f"🔍 UNWRAP_TOOL_INPUT: Unwrapping tool_input for {tool_name}")
                                        args = tool_input
                                
                                # Fix parameter type conversion issues
                                if 'raw' in args and isinstance(args['raw'], str):
                                    args['raw'] = args['raw'].lower() in ('true', '1', 'yes')
                                if 'max_length' in args and isinstance(args['max_length'], str):
                                    try:
                                        args['max_length'] = int(args['max_length'])
                                    except ValueError:
                                        pass
                                
                                # Detect empty tool calls for tools that require arguments
                                                
                                actual_tool_name = self._normalize_tool_name(tool_name)
                                
                                # Generic schema-based validation
                                tool_schema = None
                                for t in all_tools:
                                    t_name = getattr(t, 'name', '')
                                    if t_name == tool_name or t_name == actual_tool_name:
                                        if hasattr(t, 'metadata') and t.metadata:
                                            tool_schema = t.metadata.get('input_schema')
                                        break
                                
                                if tool_schema:
                                    validation_error = validate_tool_args_against_schema(
                                        tool_name, args, tool_schema
                                    )
                                    if validation_error:
                                        # Log full validation error for debugging, but with line breaks for readability
                                        logger.error(f"🔍 SCHEMA_VALIDATION_FAILED: {tool_name}")
                                        for line in validation_error.split('\n')[:15]:  # Log first 15 lines
                                            if line.strip():
                                                logger.error(f"   {line}")
                                        empty_tool_calls_this_iteration += 1
                                        consecutive_empty_tool_calls += 1
                                        
                                        tool_results.append({
                                            'tool_id': tool_id,
                                            'tool_name': tool_name,
                                            'result': validation_error
                                        })
                                        yield {'type': 'tool_result_for_model', 'tool_use_id': tool_id, 'content': validation_error}
                                        completed_tools.add(tool_id)
                                        tools_executed_this_iteration = True
                                        continue
                                
                                # Check for empty args dict - provide self-correcting feedback
                                if not args or len(args) == 0:
                                    logger.error(f"🔍 EMPTY_ARGS: {tool_name} called with no arguments")
                                    consecutive_empty_tool_calls += 1
                                    
                                    # Build tool-specific correction guidance
                                    if actual_tool_name == 'run_shell_command':
                                        error_result = """TOOL CALL FAILED - EMPTY ARGUMENTS

You called: run_shell_command
You provided: {} (empty)

REQUIRED: The 'command' parameter must be provided.

CORRECT FORMAT:
{
  "command": "your_shell_command_here"
}

EXAMPLE:
{
  "command": "ls -la"
}

Retry now with the command parameter."""
                                    else:
                                        error_result = f"""TOOL CALL FAILED - EMPTY ARGUMENTS

You called: {tool_name}
You provided: {{}} (empty)

This tool requires arguments but received none.
Check the tool schema for required parameters and retry."""

                                    tool_results.append({'tool_id': tool_id, 'tool_name': tool_name, 'result': error_result})
                                    # Don't show validation errors to user - just feed back to model for self-correction
                                    yield {'type': 'tool_result_for_model', 'tool_use_id': tool_id, 'content': error_result}
                                    completed_tools.add(tool_id)
                                    tools_executed_this_iteration = True  # Continue iteration so model sees error and can retry
                                    continue
                                
                                elif actual_tool_name == 'run_shell_command' and not args.get('command'):
                                    logger.error(f"🔍 MISSING_COMMAND: {tool_name} called without 'command' param, got: {args}")
                                    empty_tool_calls_this_iteration += 1
                                    consecutive_empty_tool_calls += 1
                                    
                                    # Self-correcting feedback with exact format needed
                                    error_result = f"""TOOL CALL FAILED - MISSING 'command' PARAMETER

You called: run_shell_command
You provided: {json.dumps(args)}

PROBLEM: 'command' parameter is REQUIRED but missing.

CORRECT FORMAT:
{{
  "command": "your_shell_command_here"
}}

EXAMPLE:
{{
  "command": "find . -name '*.md' -type f"
}}
Retry with the 'command' parameter included."""

                                    tool_results.append({'tool_id': tool_id, 'tool_name': tool_name, 'result': error_result})
                                    # Don't show validation errors to user - just feed back to model for self-correction
                                    yield {'type': 'tool_result_for_model', 'tool_use_id': tool_id, 'content': error_result}
                                    completed_tools.add(tool_id)
                                    tools_executed_this_iteration = True  # Continue iteration so model sees error and can retry
                                    continue
                                    tools_executed_this_iteration = True  # Continue iteration so model sees error and can retry
                                    continue
                                
                                # Update the corresponding entry in all_tool_calls with parsed arguments
                                for tool_call in all_tool_calls:
                                    if tool_call['id'] == tool_id:
                                        tool_call['args'] = args
                                        break
                                
                                actual_tool_name = self._normalize_tool_name(tool_name)
                                
                                # Handle both run_shell_command and mcp_run_shell_command
                                if actual_tool_name in ['run_shell_command'] or tool_name in ['mcp_run_shell_command']:
                                    actual_tool_name = 'run_shell_command'
                                
                                # Create signature to detect duplicates
                                tool_signature = f"{actual_tool_name}:{json.dumps(args, sort_keys=True)}"
                                
                                # Check for recently executed similar commands to prevent duplicates across iterations
                                if actual_tool_name == 'run_shell_command' and args.get('command'):
                                    current_command = args['command']
                                    
                                    # Check if this command is similar to recent commands
                                    skip_execution = False
                                    for recent_cmd in recent_commands[-10:]:  # Check last 10 commands
                                        if self._commands_similar(current_command, recent_cmd):
                                            logger.debug(f"🔍 DUPLICATE_COMMAND_SKIP: Skipping duplicate command '{current_command}' (similar to recent '{recent_cmd}')")
                                            
                                            # Add a helpful message instead of executing
                                            duplicate_result = f"Command '{current_command}' was already executed recently. Result should be available above."
                                            tool_results.append({
                                                'tool_id': tool_id,
                                                'tool_name': tool_name,
                                                'result': duplicate_result
                                            })
                                            
                                            completed_tools.add(tool_id)
                                            tools_executed_this_iteration = True
                                            skip_execution = True
                                            break
                                    
                                    if skip_execution:
                                        continue  # Skip to next tool in the content_block_stop processing
                                
                                # Execute the tool (already checked for duplicates at collection)
                                logger.debug(f"🔍 EXECUTING_TOOL: {actual_tool_name} with args {args}")
                                
                                # Send tool_start event with complete arguments
                                yield {
                                    'type': 'tool_start',
                                    'tool_id': tool_id,
                                    'tool_name': tool_name,
                                    'display_header': self._get_tool_header(tool_name, args),
                                    'args': args,
                                    'timestamp': f"{int((time.time() - iteration_start_time) * 1000)}ms"
                                }
                                
                                # Check for user feedback before executing tool
                                if conversation_id:
                                    try:
                                        from app.server import active_feedback_connections
                                        if conversation_id in active_feedback_connections:
                                            feedback_queue = active_feedback_connections[conversation_id]['feedback_queue']
                                            # Check for feedback without blocking
                                            try:
                                                feedback_data = feedback_queue.get_nowait()
                                                if feedback_data.get('type') == 'tool_feedback':
                                                    feedback_message = feedback_data.get('message', '')
                                                    logger.info(f"🔄 FEEDBACK_INTEGRATION: Received feedback: {feedback_message}")
                                                    # If feedback suggests stopping, break out of tool execution
                                                    if any(stop_word in feedback_message.lower() for stop_word in ['stop', 'halt', 'abort', 'cancel', 'quit']):
                                                        logger.info(f"🔄 FEEDBACK_INTEGRATION: Feedback indicates stop - ending tool execution")
                                                        yield track_yield({'type': 'text', 'content': f"\n\n**User feedback received:** {feedback_message}\n**Stopping tool execution as requested.**\n\n"})
                                                        # Flush any remaining content
                                                        await asyncio.sleep(0.1)  # Give frontend time to process
                                                        yield track_yield({'type': 'stream_end'})
                                                        return
                                                    else:
                                                        # Handle directive feedback - add to conversation for model to see
                                                        logger.info(f"🔄 FEEDBACK_INTEGRATION: Adding directive feedback to conversation: {feedback_message}")
                                                        
                                                        # Add user feedback as a message to the conversation
                                                        conversation.append({
                                                            "role": "user", 
                                                            "content": f"[Real-time feedback]: {feedback_message}"
                                                        })
                                                        logger.info(f"🔄 FEEDBACK_DELIVERED: Added tool-level feedback to conversation before tool execution")
                                                        
                                                        # Acknowledge the feedback to user
                                                        yield track_yield({
                                                            'type': 'text', 
                                                            'content': f"\n\n**Feedback received:** {feedback_message}\n\n"
                                                        })
                                                        
                                                        # Skip the current planned tool and let the model respond to feedback
                                                        logger.info(f"🔄 FEEDBACK_INTEGRATION: Skipping planned tool to respond to feedback")
                                                        completed_tools.add(tool_id)
                                                        tools_executed_this_iteration = True
                                                        continue
                                            except asyncio.QueueEmpty:
                                                pass  # No feedback available, continue normally
                                    except Exception as e:
                                        logger.debug(f"Error checking feedback: {e}")
                               
                                # Execute the tool immediately
                                try:
                                   # Import signing and verification functions
                                   from app.mcp.signing import verify_tool_result, strip_signature_metadata
                                   
                                   # Check if this is a builtin DirectMCPTool
                                   logger.debug(f"🔍 BUILTIN_CHECK: Looking for tool '{actual_tool_name}' in {len(all_tools)} tools")
                                   builtin_tool = None
                                   if all_tools:
                                       for tool in all_tools:
                                           logger.debug(f"🔍 BUILTIN_CHECK: Checking tool {tool.name}, type={type(tool).__name__}, isinstance DirectMCPTool={isinstance(tool, DirectMCPTool)}")
                                           if isinstance(tool, DirectMCPTool) and tool.name == actual_tool_name:
                                               builtin_tool = tool
                                               logger.info(f"🔧 BUILTIN_FOUND: Found builtin tool {actual_tool_name}")
                                               break
                                   
                                   if not builtin_tool:
                                       logger.debug(f"🔍 BUILTIN_NOT_FOUND: Tool '{actual_tool_name}' not found in builtin tools, routing to MCP manager")
                                   
                                   if builtin_tool:
                                        # Call builtin tool directly
                                        logger.info(f"🔧 Calling builtin tool directly: {actual_tool_name}")
                                        # Inject project path for workspace-scoped routing
                                        if project_root:
                                            args['_workspace_path'] = project_root
                                        result = builtin_tool._run(**args)
                                        
                                        # SECURITY: Sign builtin tool results too
                                        # Builtin tools don't go through MCPClient so we sign here
                                        if result and not isinstance(result, dict):
                                            # Convert string results to dict format
                                            result = {"content": [{"type": "text", "text": str(result)}]}
                                        if result and isinstance(result, dict) and not result.get("error"):
                                            conversation_id = args.get('conversation_id', 'default')
                                            result = sign_tool_result(actual_tool_name, args, result, conversation_id)
                                            logger.debug(f"🔐 Signed builtin tool result for {actual_tool_name}")
                                   else:
                                        # Call through MCP manager for external tools
                                        # Determine which server has this tool
                                        target_server_name = None
                                        for tool in all_tools:
                                            tool_name_check = getattr(tool, 'name', '')
                                            # Check both with and without mcp_ prefix
                                            if tool_name_check == actual_tool_name or tool_name_check == f"mcp_{actual_tool_name}":
                                                # Found the tool, get its server name
                                                if hasattr(tool, 'metadata') and tool.metadata:
                                                    target_server_name = tool.metadata.get('server_name')
                                                    if target_server_name:
                                                        logger.debug(f"🔍 ROUTING: Found tool {actual_tool_name} belongs to server '{target_server_name}'")
                                                        break
                                        
                                        if not target_server_name:
                                            logger.warning(f"🔍 ROUTING: Could not determine server for tool {actual_tool_name}, manager will try all servers")
                                        
                                        # Inject project path so the MCP manager can route to
                                        # a workspace-scoped server instance with the correct cwd
                                        if project_root:
                                            args['_workspace_path'] = project_root
                                        # External tools get signed in MCPClient.call_tool automatically
                                        result = await mcp_manager.call_tool(actual_tool_name, args, server_name=target_server_name)
                                    
                                   # Initialize verification tracking variables
                                   is_verified = False
                                   verification_error = None
                                    
                                   # SECURITY: Verify the result signature before using it
                                   if result and isinstance(result, dict) and not result.get("error"):
                                        is_valid, error_message = verify_tool_result(result, actual_tool_name, args)
                                        # Replace result with corrective error
                                        is_verified = False
                                        verification_error = None
                                    
                                        if not is_valid:
                                            logger.error(f"🔐 SECURITY: Tool result verification failed for {actual_tool_name}: {error_message}")
                                            
                                            # Record security violation for monitoring
                                            from app.server import record_verification_result
                                            record_verification_result(actual_tool_name, False, error_message)
                                            
                                            # Create corrective error message for model
                                            corrective_message = f"""🚨 TOOL CALL REJECTED - SECURITY VERIFICATION FAILED

Tool: {actual_tool_name}
Reason: {error_message}

This tool call did not execute successfully. The result could not be cryptographically verified.

DO NOT proceed as if this tool executed.
DO NOT use or reference results from this tool call.

Please try again or proceed without this tool."""
                                            
                                            result = {
                                                "error": True,
                                                "message": corrective_message
                                            }
                                        else:
                                            is_verified = True
                                            
                                            # Record successful verification
                                            from app.server import record_verification_result
                                            record_verification_result(actual_tool_name, True)
                                            
                                            logger.debug(f"🔐 Verified tool result for {actual_tool_name}")
                                            
                                            # Strip signature metadata before processing
                                            # (keep verification status separate)
                                            result = strip_signature_metadata(result)
                                    
                                    # Add successfully executed command to recent commands for deduplication
                                   if actual_tool_name == 'run_shell_command' and args.get('command'):
                                        recent_commands.append(args['command'])
                                        # Keep only last 20 commands to prevent memory bloat
                                        recent_commands = recent_commands[-20:]
                                    
                                    # Process result
                                   if isinstance(result, dict) and result.get('error') and result.get('error') != False:
                                        error_msg = result.get('message', 'Unknown error')
                                        
                                        # Check if this is a security verification failure
                                        if 'SECURITY VERIFICATION FAILED' in error_msg:
                                            # Use the full corrective message for model
                                            result_text = error_msg
                                        elif 'repetitive execution' in error_msg:
                                            result_text = f"BLOCKED: {error_msg} Previous attempts may have succeeded - check the results above before retrying."
                                        elif 'non-zero exit status' in error_msg:
                                            result_text = f"COMMAND FAILED: {error_msg}. The external tool encountered an error."
                                        elif 'Content truncated' in error_msg:
                                            result_text = f"PARTIAL RESULT: {error_msg}. Use start_index parameter to get more content."
                                        elif 'validation error' in error_msg.lower():
                                            result_text = f"PARAMETER ERROR: {error_msg}. Check the tool's parameter requirements."
                                        else:
                                            result_text = f"ERROR: {error_msg}. Please try a different approach or fix the command."
                                   elif isinstance(result, dict) and 'content' in result:
                                        content = result['content']
                                        if isinstance(content, list) and len(content) > 0:
                                            result_text = content[0].get('text', str(result))
                                        else:
                                            result_text = str(result)
                                   else:
                                        result_text = str(result)

                                   tool_results.append({
                                        'tool_id': tool_id,
                                        'tool_name': tool_name,
                                        'result': result_text
                                    })

                                   # SECURITY: Only display to user if verification passed OR if it's a legitimate error
                                   # Hallucinated results (security failures) are NOT shown to user
                                   should_display_to_user = is_verified or (not verification_error)
                                   
                                   if should_display_to_user:
                                       yield {
                                           'type': 'tool_display',
                                           'tool_id': tool_id,
                                           'tool_name': tool_name,
                                           'result': self._format_tool_result(tool_name, result_text, args),
                                           'args': args,
                                           'verified': is_verified,
                                           'verification_error': verification_error,
                                           'timestamp': f"{int((time.time() - iteration_start_time) * 1000)}ms"
                                       }
                                   else:
                                       # Security failure - suppress from user display but log
                                       logger.warning(f"🔐 SECURITY: Suppressed unverified tool result from user display: {actual_tool_name}")
                                   
                                   # ALWAYS send result to model (either verified result or corrective error)
                                   yield {
                                       'type': 'tool_result_for_model',
                                       'tool_use_id': tool_id,
                                       'content': result_text
                                   }
                                   # Note: Tool result is added to conversation below (line 1905-1924)
                                                    
                                   # Immediate flush to reduce delay
                                   await asyncio.sleep(0)
                                    
                                   tools_executed_this_iteration = True
                                   logger.debug(f"🔍 TOOL_EXECUTED_FLAG: Set tools_executed_this_iteration = True for tool {tool_id}")
                                    
                                except Exception as e:
                                    error_msg = f"Tool error: {str(e)}"
                                    logger.error(f"🔍 TOOL_EXECUTION_ERROR: {error_msg}")
                                    tool_results.append({
                                        'tool_id': tool_id,
                                        'tool_name': tool_name,
                                        'result': f"ERROR: {error_msg}. Please try a different approach or fix the command."
                                    })

                                    # Frontend error display
                                    yield {'type': 'tool_display', 'tool_name': tool_name, 'result': f"ERROR: {error_msg}"}
                                    
                                    # Clean error for model
                                    yield {
                                        'type': 'tool_result_for_model',
                                        'tool_use_id': tool_id,
                                        'content': f"ERROR: {error_msg}. Please try a different approach or fix the command."
                                    }

                                completed_tools.add(tool_id)
                            
                            except json.JSONDecodeError as e:
                                logger.error(f"🔍 JSON_PARSE_ERROR: Failed to parse tool arguments for {tool_name}: {e}")
                                empty_tool_calls_this_iteration += 1
                                consecutive_empty_tool_calls += 1
                                
                                # Send self-correcting error to model
                                error_result = f"""TOOL CALL FAILED - JSON PARSE ERROR

You called: {tool_name}
You provided malformed JSON: {args_json[:200]}

Parse error: {str(e)}

PROBLEM: The JSON is syntactically invalid and cannot be parsed.

Please retry the tool call with valid JSON. Ensure:
- All strings are properly quoted
- No trailing commas
- Braces and brackets are balanced"""
                                
                                tool_results.append({'tool_id': tool_id, 'tool_name': tool_name, 'result': error_result})
                                yield {'type': 'tool_result_for_model', 'tool_use_id': tool_id, 'content': error_result}
                                completed_tools.add(tool_id)
                                tools_executed_this_iteration = True

                    elif chunk['type'] == 'message_stop':
                        # Flush any remaining content from buffers before stopping  
                        # Flush block opening buffer first
                        if hasattr(self, '_block_opening_buffer') and self._block_opening_buffer:
                            assistant_text += self._block_opening_buffer
                            self._update_code_block_tracker(self._block_opening_buffer, code_block_tracker)
                            yield track_yield({
                                'type': 'text',
                                'content': self._block_opening_buffer,
                                'timestamp': f"{int((time.time() - iteration_start_time) * 1000)}ms"
                            })
                            self._block_opening_buffer = ""
                        
                        if viz_buffer.strip():
                            self._update_code_block_tracker(viz_buffer, code_block_tracker)
                            yield track_yield({
                                'type': 'text',
                                'content': viz_buffer,
                                'timestamp': f"{int((time.time() - iteration_start_time) * 1000)}ms"
                            })
                        # Flush any remaining content from optimizer
                        if hasattr(self, '_content_optimizer'):
                            remaining = self._content_optimizer.flush_remaining()
                            if remaining:
                                self._update_code_block_tracker(remaining, code_block_tracker)
                                yield track_yield({
                                    'type': 'text',
                                    'content': remaining,
                                    'timestamp': f"{int((time.time() - iteration_start_time) * 1000)}ms"
                                })
                        if content_buffer.strip():
                            self._update_code_block_tracker(content_buffer, code_block_tracker)
                            yield track_yield({
                                'type': 'text',
                                'content': content_buffer,
                                'timestamp': f"{int((time.time() - iteration_start_time) * 1000)}ms"
                            })
                        
                        # Check if code block is still incomplete
                        # ENHANCED BLOCK COMPLETION CHECK
                        final_assistant_text = assistant_text.strip()
                        
                        # Check for unclosed code blocks using tracker
                        logger.debug(f"🔍 COMPLETION_CHECK: tracker_in_block={code_block_tracker.get('in_block', False)}")
                        
                        continuation_count = 0
                        max_continuations = 10
                        continuation_happened = False
                        
                        # Generate a stable marker ID for this continuation cycle
                        # This allows frontend to jump to the exact same spot on all retry attempts
                        continuation_marker_id = f"continuation_{time.time_ns()}"
                        
                        while code_block_tracker.get('in_block') and continuation_count < max_continuations:
                            continuation_count += 1
                            block_type = code_block_tracker.get('block_type', 'code')
                            logger.info(f"🔄 INCOMPLETE_BLOCK: Detected incomplete {block_type} block, auto-continuing (attempt {continuation_count})")
                            
                            # Mark rewind boundary before auto-continuation
                            assistant_lines = assistant_text.split('\n')
                            # Remove the incomplete last line - rewind to last complete line
                            if assistant_lines and assistant_lines[-1].strip():
                                # Last line is incomplete, remove it
                                assistant_lines = assistant_lines[:-1]
                                logger.info(f"🔄 REWIND: Removed incomplete last line, rewinding to line {len(assistant_lines)}")
                            
                            last_complete_line = len(assistant_lines)
                            
                            # Use stable marker ID so all retries rewind to the SAME spot
                            rewind_marker = f"<!-- REWIND_MARKER: {continuation_marker_id} -->"
                            
                            rewind_chunk = {
                                'type': 'text',
                                'content': f"{rewind_marker}\n\n",
                                'timestamp': f"{int((time.time() - iteration_start_time) * 1000)}ms",
                                # Tell frontend which marker to target
                                'rewind': True,
                                'to_marker': continuation_marker_id
                            }
                            logger.info(f"🔄 YIELDING_REWIND: Rewinding to line {last_complete_line}")
                            yield track_yield(rewind_chunk)
                            
                            # CRITICAL: Add delay to ensure rewind marker is sent before continuation
                            await asyncio.sleep(0.1)
                            
                            # Send heartbeat before continuation to keep connection alive
                            yield {
                                'type': 'heartbeat',
                                'heartbeat': True,
                                'timestamp': f"{int((time.time() - iteration_start_time) * 1000)}ms"
                            }
                            
                            await asyncio.sleep(0.1)  # Ensure heartbeat is sent
                            
                            continuation_had_content = False
                            continuation_happened = True
                            try:
                                async for continuation_chunk in self._continue_incomplete_code_block(
                                    conversation, code_block_tracker, mcp_manager, iteration_start_time, assistant_text
                                ):
                                    if continuation_chunk.get('content'):
                                        continuation_had_content = True
                                        logger.info(f"🔄 YIELDING_CONTINUATION: {repr(continuation_chunk.get('content', '')[:50])}")
                                        self._update_code_block_tracker(continuation_chunk['content'], code_block_tracker)
                                        assistant_text += continuation_chunk['content']
                                        
                                        if code_block_tracker['in_block']:
                                            continuation_chunk['code_block_continuation'] = True
                                            continuation_chunk['block_type'] = code_block_tracker['block_type']
                                    
                                    yield continuation_chunk
                            except Exception as continuation_error:
                                logger.error(f"Continuation failed: {continuation_error}")
                                # Send continuation failure marker
                                yield {
                                    'type': 'continuation_failed',
                                    'reason': str(continuation_error),
                                    'can_retry': 'ThrottlingException' in str(continuation_error),
                                    'timestamp': f"{int((time.time() - iteration_start_time) * 1000)}ms"
                                }
                                break
                            
                            if not continuation_had_content:
                                logger.info("🔄 CONTINUATION: No content generated, stopping continuation attempts")
                                break
                            
                            # Log tracker state after continuation
                            logger.info(f"🔄 CONTINUATION_RESULT: After attempt {continuation_count}, in_block={code_block_tracker['in_block']}, had_content={continuation_had_content}")
                        
                        # Just break out of chunk processing, handle completion logic below
                        
                        # CRITICAL: Record iteration usage BEFORE any error handling
                        # This ensures we capture usage even if subsequent logic fails
                        if conversation_id and iteration_usage.input_tokens > 0:
                            try:
                                tracker = get_global_usage_tracker()
                                tracker.record_usage(conversation_id, iteration_usage)
                                
                                logger.debug(f"📊 Recorded usage for iteration {iteration}: "
                                           f"{iteration_usage.input_tokens:,} fresh, "
                                           f"{iteration_usage.cache_read_tokens:,} cached")
                            except Exception as tracking_error:
                                # Don't let tracking errors break the flow
                                logger.error(f"Error recording usage: {tracking_error}")
                        elif conversation_id and iteration_usage.input_tokens == 0:
                            logger.warning(f"⚠️ No usage metrics captured for iteration {iteration}")
                        elif not conversation_id:
                            logger.debug(f"No conversation_id, skipping usage tracking")
                        
                        break

                # MOVED: Log usage metrics AFTER processing all chunks
                # This ensures we have all the data before logging
                if iteration_usage.input_tokens > 0 or iteration_usage.output_tokens > 0:
                    # Update cumulative
                    cumulative_usage.input_tokens += iteration_usage.input_tokens
                    cumulative_usage.output_tokens += iteration_usage.output_tokens
                    cumulative_usage.cache_read_tokens += iteration_usage.cache_read_tokens
                    cumulative_usage.cache_write_tokens += iteration_usage.cache_write_tokens
                    
                    total_input = iteration_usage.input_tokens + iteration_usage.cache_read_tokens
                    fresh = iteration_usage.input_tokens
                    cached = iteration_usage.cache_read_tokens
                    
                    # Log ALWAYS - critical operational data
                    logger.debug("=" * 80)
                    logger.debug(f"📊 BEDROCK USAGE - Iteration {iteration}")
                    logger.debug("=" * 80)
                    logger.debug(f"   Fresh Input:    {fresh:>8,} tokens")
                    logger.debug(f"   Cached Input:   {cached:>8,} tokens (FREE)")
                    logger.debug(f"   Output:         {iteration_usage.output_tokens:>8,} tokens")
                    logger.debug(f"   Cache Written:  {iteration_usage.cache_write_tokens:>8,} tokens")
                    
                    if total_input > 0:
                        cache_pct = (cached / total_input) * 100
                        logger.debug(f"   Efficiency:     {cache_pct:>7.1f}%")
                        logger.debug(f"   💰 Cost Save:   ~{cache_pct:>6.1f}%")
                    
                    logger.debug("=" * 80)
                    
                    # CRITICAL: Detect cache failures immediately
                    if iteration > 0 and cached == 0 and fresh > 10000:
                        logger.error("🚨 CACHE FAILURE DETECTED!")
                        logger.error(f"   Iteration {iteration}: {fresh:,} fresh tokens")
                        logger.error(f"   Expected cache reads but got ZERO")
                        logger.error(f"   This WILL cause throttling!")
                        
                        throttle_state['cache_working'] = False
                    elif cached > 0:
                        throttle_state['cache_working'] = True
                        throttle_state['last_cache_efficiency'] = iteration_usage.cache_hit_rate
                        logger.debug(f"✅ CACHE WORKING: {cached:,} tokens reused")
                else:
                    logger.warning(f"⚠️ No usage metrics captured for iteration {iteration}")

                # CRITICAL: Validate tool_results match tool_use blocks before building conversation
                # Remove any tool_use blocks that don't have corresponding results
                valid_tool_ids = {tr['tool_id'] for tr in tool_results}
                if all_tool_calls:
                    # Filter all_tool_calls to only include those with results
                    all_tool_calls = [tc for tc in all_tool_calls if tc['id'] in valid_tool_ids]
                    
                    if len(all_tool_calls) != len(tool_results):
                        logger.warning(f"🔍 TOOL_MISMATCH: {len(all_tool_calls)} tool calls but {len(tool_results)} results - filtered orphaned calls")
                
                # Add assistant response to conversation with proper tool_use blocks
                # ONLY include tool_use blocks that have corresponding tool_results
                if assistant_text.strip() or tools_executed_this_iteration:
                    # Build content as list with text and tool_use blocks
                    content_blocks = []
                    if assistant_text.strip():
                        content_blocks.append({"type": "text", "text": assistant_text.rstrip()})
                    
                    # Add tool_use blocks ONLY for tools that have results
                    for tool_result in tool_results:
                        # Find the corresponding tool call to get the actual args
                        tool_args = {}
                        for tool_call in all_tool_calls:
                            if tool_call['id'] == tool_result['tool_id']:
                                tool_args = tool_call.get('args', {})
                                break
                        
                        # Ensure tool_use block has the correct name format
                        tool_name = tool_result['tool_name']
                        if tool_name.startswith('mcp_'):
                            tool_name = tool_name[4:]  # Remove mcp_ prefix for Bedrock
                        
                        content_blocks.append({
                            "type": "tool_use",
                            "id": tool_result['tool_id'],
                            "name": tool_name,
                            "input": tool_args
                        })
                    
                    conversation.append({"role": "assistant", "content": content_blocks})
            
                # Add tool results to conversation BEFORE filtering
                logger.debug(f"🔍 ITERATION_END_CHECK: tools_executed_this_iteration = {tools_executed_this_iteration}, tool_results count = {len(tool_results)}")
                if tools_executed_this_iteration:
                    logger.debug(f"🔍 TOOL_RESULTS_PROCESSING: Adding {len(tool_results)} tool results to conversation")
                    for tool_result in tool_results:
                        raw_result = tool_result['result']
                        if isinstance(raw_result, str) and '$ ' in raw_result:
                            lines = raw_result.split('\n')
                            clean_lines = [line for line in lines if not line.startswith('$ ')]
                            raw_result = '\n'.join(clean_lines).strip()
                        
                        # Add in tool_result_for_model format so filter can convert to proper Bedrock format
                        conversation.append({
                            "role": "user",
                            "content": [
                                {
                                    "type": "tool_result",
                                    "tool_use_id": tool_result['tool_id'],
                                    "content": raw_result
                                }
                            ]
                        })
                
                # SAFETY CHECK: Ensure conversation is in valid Bedrock format
                # Verify that every tool_use in assistant messages has a corresponding tool_result
                if conversation:
                    tool_use_ids = set()
                    tool_result_ids = set()
                    
                    for msg in conversation:
                        if msg.get('role') == 'assistant' and isinstance(msg.get('content'), list):
                            for block in msg['content']:
                                if block.get('type') == 'tool_use':
                                    tool_use_ids.add(block.get('id'))
                        elif msg.get('role') == 'user' and isinstance(msg.get('content'), list):
                            for block in msg['content']:
                                if block.get('type') == 'tool_result':
                                    tool_result_ids.add(block.get('tool_use_id'))
                    
                    orphaned_ids = tool_use_ids - tool_result_ids
                    if orphaned_ids:
                        logger.error(f"🚨 ORPHANED_TOOL_USE: Found {len(orphaned_ids)} tool_use blocks without results: {orphaned_ids}")
                
                # The conversation should now be in proper Bedrock format
                # Remove the filter call since we're constructing messages correctly
                logger.debug(f"🤖 MODEL_RESPONSE: {assistant_text}")
                logger.debug(f"Conversation length: {len(conversation)} messages")

                # Skip duplicate execution - tools are already executed in content_block_stop
                # This section was causing duplicate tool execution

                # Continue to next iteration if tools were executed
                if tools_executed_this_iteration:
                    # Warn about consecutive empty tool calls but don't break
                    if consecutive_empty_tool_calls >= 5:
                        logger.warning(f"🔍 EMPTY_TOOL_WARNING: {consecutive_empty_tool_calls} consecutive empty tool calls detected")
                        # Add a message to guide the model to respond without tools
                        conversation.append({
                            "role": "user",
                            "content": "Please provide your response based on the information available. Do not attempt to use tools."
                        })
                    elif consecutive_empty_tool_calls >= 3:
                        logger.warning(f"🔍 EMPTY_TOOL_WARNING: {consecutive_empty_tool_calls} consecutive empty tool calls detected, adding delay")
                        # Add a small delay to slow down the loop
                        await asyncio.sleep(0.5)
                    
                    # Reset consecutive counter if we had successful tool calls
                    if empty_tool_calls_this_iteration == 0:
                        consecutive_empty_tool_calls = 0
                    
                    logger.debug(f"🔍 CONTINUING_ROUND: Tool results added, model will continue in same stream (round {iteration + 1})")
                    # Yield heartbeat to flush stream before next iteration
                    yield {'type': 'iteration_continue', 'iteration': iteration + 1}
                    await asyncio.sleep(0)
                    continue  # Immediately start next iteration
                else:
                    # CRITICAL: Check for pending feedback BEFORE deciding to end stream
                    # This ensures feedback sent during the last tool execution is not lost
                    pending_feedback_before_end = []
                    if conversation_id:
                        try:
                            from app.server import active_feedback_connections
                            if conversation_id in active_feedback_connections:
                                feedback_queue = active_feedback_connections[conversation_id]['feedback_queue']
                                
                                # Drain any pending feedback
                                try:
                                    while True:
                                        try:
                                            feedback_data = feedback_queue.get_nowait()
                                            if feedback_data.get('type') == 'tool_feedback':
                                                pending_feedback_before_end.append(feedback_data.get('message', ''))
                                            elif feedback_data.get('type') == 'interrupt':
                                                logger.info(f"🔄 PRE-END FEEDBACK: Received interrupt before stream end")
                                                yield track_yield({'type': 'text', 'content': '\n\n**User requested stop.**\n\n'})
                                                yield track_yield({'type': 'stream_end'})
                                                return
                                        except asyncio.QueueEmpty:
                                            break
                                except Exception as queue_error:
                                    logger.debug(f"Error draining pre-end feedback queue: {queue_error}")
                        except Exception as e:
                            logger.debug(f"Error checking pre-end feedback: {e}")
                    
                    # If we found pending feedback, deliver it before ending
                    if pending_feedback_before_end:
                        combined_feedback = ' '.join(pending_feedback_before_end)
                        logger.info(f"🔄 PRE-END FEEDBACK: Processing {len(pending_feedback_before_end)} feedback message(s) before stream end")
                        
                        # Add feedback to conversation
                        conversation.append({
                            "role": "user",
                            "content": f"[User feedback]: {combined_feedback}"
                        })
                        
                        # Notify user
                        yield track_yield({
                            'type': 'text',
                            'content': f"\n\n**📝 Feedback received:** {combined_feedback}\n\n",
                            'timestamp': f"{int((time.time() - iteration_start_time) * 1000)}ms"
                        })
                        
                        # Continue to next iteration so model can respond
                        logger.debug(f"🔄 PRE-END FEEDBACK: Continuing to next iteration to process feedback")
                        continue
                    
                    # Check if too many tools were blocked (indicates runaway loop)
                    if blocked_tools_this_iteration >= 3:
                        logger.warning(f"🔍 RUNAWAY_LOOP_DETECTED: {blocked_tools_this_iteration} tools blocked in iteration {iteration}, ending stream")
                        yield {'type': 'stream_end'}
                        break
                    
                    # No tools executed - check if we should end the stream
                    if assistant_text.strip():
                        # For models that don't support assistant prefill (e.g. Opus 4 via Bedrock),
                        # continuing to the next iteration would fail because the conversation ends
                        # with an assistant message and no tool results to add a user message.
                        # End the stream gracefully instead of attempting another API call.
                        supports_prefill = self.model_config.get('supports_assistant_prefill', True)
                        if not supports_prefill and not tools_executed_this_iteration:
                            logger.info(
                                f"🛑 NO_PREFILL_END: Model doesn't support prefill, "
                                f"ending stream after text-only response (continuation={continuation_happened})"
                            )
                            yield {'type': 'stream_end'}
                            break

                        # CRITICAL: Detect stable short responses to prevent infinite loops
                        # If the response is very short (< 50 chars) and we're repeating iterations
                        # with identical output, it's a stable completion - end the stream
                        if iteration >= 1 and len(assistant_text.strip()) < 50:
                            # Check if output hasn't grown in the last iteration
                            # (indicates the model has nothing more to add)
                            logger.debug(f"🔍 SHORT_STABLE_RESPONSE: Detected short response ({len(assistant_text)} chars) at iteration {iteration}, ending stream")
                            yield {'type': 'stream_end'}
                            break
                        
                        # FIRST: Check if code block is still incomplete - if so, continue
                        if code_block_tracker.get('in_block'):
                            logger.debug(f"🔍 INCOMPLETE_BLOCK_REMAINING: Code block still open, continuing to next iteration")
                            continue
                        
                        # If continuation just happened, always do another iteration
                        # to let the model respond/continue naturally
                        if continuation_happened:
                            logger.debug(f"🔍 CONTINUATION_COMPLETE: Continuation finished, continuing to next iteration")
                            continue
                        
                        # Check if there's already substantial commentary after the last tool/diff/code block
                        text_after_last_block = self._get_text_after_last_structured_content(assistant_text)
                        word_count_after_block = len(text_after_last_block.split()) if text_after_last_block else 0
                        
                        # If we have 20+ words after the last block and it ends properly, consider it complete
                        if (word_count_after_block >= 20 and 
                            text_after_last_block.rstrip().endswith(('.', '!', '?'))):
                            logger.debug(f"🔍 COMPLETE_RESPONSE: Found {word_count_after_block} words after last block, ending stream: '{text_after_last_block[-50:]}'")
                            yield {'type': 'stream_end'}
                            break
                        
                        # Otherwise check if we should continue
                        text_end = assistant_text[-200:].strip()
                        suggests_continuation = (
                            text_end.endswith((':')) or  # About to make tool call  
                            assistant_text.endswith('```') or  # Just finished code block - might add explanation
                            (word_count_after_block < 20 and not text_after_last_block.rstrip().endswith(('.', '!', '?')))
                        )
                        
                        if suggests_continuation and iteration < 2:
                            logger.debug(f"🔍 CONTINUE_RESPONSE: Only {word_count_after_block} words after last block, continuing: '{text_after_last_block[-30:] if text_after_last_block else text_end}'")
                            continue
                        else:
                            logger.debug(f"🔍 STREAM_END: Model produced text without tools, ending stream")
                            # Log final metrics
                            logger.info(
                                f"\n📊 Final stream metrics: "
                                f"events={stream_metrics['events_sent']}, "
                                f"bytes={stream_metrics['bytes_sent']}, "
                                f"avg_size={stream_metrics['bytes_sent']/max(stream_metrics['events_sent'],1):.2f}, "
                                f"min={min(stream_metrics['chunk_sizes']) if stream_metrics['chunk_sizes'] else 0}, "
                                f"max={max(stream_metrics['chunk_sizes']) if stream_metrics['chunk_sizes'] else 0}, "
                                f"duration={time.time()-stream_metrics['start_time']:.2f}s\n"
                            )
                            yield {'type': 'stream_end'}
                            break
                    elif iteration >= 100:  # Safety: end after reaching max iterations
                        logger.debug(f"🔍 MAX_ITERATIONS: Reached maximum iterations ({iteration}), ending stream")
                        yield {'type': 'stream_end'}
                        break
                    else:
                        # No tools, no text - we're done
                        logger.debug(f"🔍 NO_ACTIVITY: No tools or text in iteration {iteration}, ending stream")
                        yield {'type': 'stream_end'}
                        break
                
                # CRITICAL: Check for pending feedback after the iteration loop completes
                # This ensures feedback that arrived during the last iteration or after completion<!-- REWIND_MARKER: 20 -->
                # is not lost and gives the model a chance to respond
                if conversation_id:
                    try:
                        from app.server import active_feedback_connections
                        if conversation_id in active_feedback_connections:
                            feedback_queue = active_feedback_connections[conversation_id]['feedback_queue']
                            
                            # Collect ALL pending feedback messages
                            pending_feedback = []
                            try:
                                while True:
                                    try:
                                        feedback_data = feedback_queue.get_nowait()
                                        feedback_type = feedback_data.get('type')
                                        if feedback_type == 'tool_feedback':
                                            pending_feedback.append(feedback_data.get('message', ''))
                                            logger.info(f"🔄 POST-LOOP FEEDBACK: Queued tool_feedback: {feedback_data.get('message', '')[:50]}...")
                                        elif feedback_type == 'interrupt':
                                            # Handle interrupt - stop processing
                                            logger.info(f"🔄 POST-LOOP FEEDBACK: Received interrupt after tool chain")
                                            yield track_yield({'type': 'text', 'content': '\n\n**User requested stop.**\n\n'})
                                            yield track_yield({'type': 'stream_end'})
                                            return
                                    except asyncio.QueueEmpty:
                                        break
                            except Exception as queue_error:
                                logger.debug(f"Error draining feedback queue: {queue_error}")
                            
                            # If we have pending feedback, send it to the model
                            if pending_feedback:
                                combined_feedback = ' '.join(pending_feedback)
                                logger.info(f"🔄 POST-LOOP FEEDBACK: Processing {len(pending_feedback)} feedback message(s) after tool chain completion")
                                
                                # Add feedback to conversation
                                conversation.append({
                                    "role": "user",
                                    "content": f"[User feedback after tool execution]: {combined_feedback}"
                                })
                                logger.info(f"🔄 FEEDBACK_DELIVERED: Added post-loop feedback to conversation: {combined_feedback[:50]}...")
                                
                                # Notify user that feedback is being processed
                                yield track_yield({
                                    'type': 'text',
                            'content': f"\n\n**📝 Feedback received:** {combined_feedback}\n\n",
                                    'timestamp': f"{int((time.time() - iteration_start_time) * 1000)}ms"
                                })
                                
                                # Make ONE additional API call to get model's response to feedback
                                try:
                                    body = {
                                        "anthropic_version": "bedrock-2023-05-31",
                                        "max_tokens": self.model_config.get('max_output_tokens', 4000),
                                        "messages": conversation
                                    }
                                    
                                    if system_content:
                                        body["system"] = system_content
                                    
                                    # Don't send tools for feedback response - just let model respond
                                    response = self.bedrock.invoke_model_with_response_stream(
                                        modelId=self.model_id,
                                        body=json.dumps(body)
                                    )
                                    
                                    # Stream the feedback response
                                    for event in response['body']:
                                        chunk = json.loads(event['chunk']['bytes'])
                                        
                                        if chunk['type'] == 'content_block_delta':
                                            delta = chunk.get('delta', {})
                                            if delta.get('type') == 'text_delta':
                                                text = delta.get('text', '')
                                                yield track_yield({
                                                    'type': 'text',
                                                    'content': text,
                                                    'timestamp': f"{int((time.time() - iteration_start_time) * 1000)}ms"
                                                })
                                        elif chunk['type'] == 'message_stop':
                                            break
                                            
                                except Exception as feedback_error:
                                    logger.error(f"Error processing post-loop feedback: {feedback_error}")
                    except Exception as e:
                        logger.debug(f"Error checking post-loop feedback: {e}")
                
                # Clean up iteration resources to prevent memory leaks
                self._cleanup_iteration_resources()

            except Exception as e:
                error_str = str(e)
                logger.error(f"Error in stream_with_tools iteration {iteration}: {error_str}", exc_info=True)
                
                # Check if this is a throttling or transient service error
                is_throttling_error = any(indicator in error_str for indicator in [
                    "ThrottlingException", 
                    "Too many tokens",
                    "Too many requests", 
                    "Rate exceeded",
                ])
                
                # Check for transient AWS service errors that should be retried
                is_transient_error = any(indicator in error_str for indicator in [
                    "internalServerException",
                    "ServiceUnavailableException",
                    "The system encountered an unexpected error",
                ])
                
                is_throttling = is_throttling_error or is_transient_error
                
                # Check for authentication/credential errors
                from app.plugins import get_active_auth_provider
                from app.utils.custom_exceptions import KnownCredentialException

                auth_provider = get_active_auth_provider()
                is_auth_error = (
                    isinstance(e, KnownCredentialException) or
                    (auth_provider and auth_provider.is_auth_error(error_str))
                )
                
                if is_throttling:
                    # Update throttle state based on what we learned
                    if len(iteration_usages) > 0:
                        last_usage = iteration_usages[-1]
                        
                        # Check if cache is working
                        total_input_processed = last_usage.input_tokens + last_usage.cache_read_tokens
                        if iteration > 0 and total_input_processed > 10000 and last_usage.cache_read_tokens == 0:
                            throttle_state['cache_working'] = False
                            logger.error("🚨 THROTTLED + NO CACHE: Cache appears broken!")
                        elif last_usage.cache_read_tokens > 0:
                            throttle_state['cache_working'] = True
                            throttle_state['last_cache_efficiency'] = last_usage.cache_hit_rate
                    
                    # Calculate intelligent backoff based on cache health and token usage
                    throttle_state['retry_count'] += 1
                    
                    # Exponential time backoff
                    time_delay = throttle_state['base_delay'] * (2 ** throttle_state['retry_count'])
                    
                    # CRITICAL: Reduce output tokens to decrease throttle pressure
                    # Per @animeshx: "more throttled with a higher output token limit"
                    current_max_tokens = body.get('max_tokens', 4000)
                    
                    # Aggressive reduction strategy
                    if throttle_state['cache_working'] == False:
                        # Cache is broken - reduce more aggressively
                        reduction_factor = 0.5  # 50% of original
                        logger.warning("🔥 CACHE BROKEN: Using aggressive output token reduction")
                    elif throttle_state['retry_count'] > 2:
                        # Multiple retries - get more aggressive
                        reduction_factor = 0.6  # 60% of original
                    else:
                        # First retry - moderate reduction
                        reduction_factor = 0.75  # 75% of original
                    
                    reduced_max_tokens = int(original_max_tokens * reduction_factor)
                    reduced_max_tokens = max(reduced_max_tokens, 2048)  # Never go below 2048
                    
                    body['max_tokens'] = reduced_max_tokens
                    
                    logger.warning(f"🔄 INTELLIGENT THROTTLE BACKOFF:")
                    logger.warning(f"   Retry #{throttle_state['retry_count']}")
                    logger.warning(f"   Time delay: {min(time_delay, 60)}s")
                    logger.warning(f"   Output tokens: {original_max_tokens:,} → {reduced_max_tokens:,} ({reduction_factor*100:.0f}%)")
                    logger.warning(f"   Cache working: {throttle_state['cache_working']}")
                    logger.warning(f"   Cache efficiency: {throttle_state['last_cache_efficiency']*100:.1f}%")
                    
                    # Extract suggested wait time if available
                    suggested_wait = 60  # Default 60 seconds
                    if "please wait" in error_str.lower():
                        # Try to extract time from error message
                        import re
                        wait_match = re.search(r'wait (\d+)', error_str.lower())
                        if wait_match:
                            suggested_wait = int(wait_match.group(1))
                    
                    # Check if this is a token-based throttling (more severe)
                    is_token_throttling = "Too many tokens" in error_str
                    
                    # Determine error type for display
                    if is_transient_error:
                        error_type = 'transient_service_error'
                        retry_message = f"AWS service temporarily unavailable after {len(tool_results)} tool execution(s). Retrying..."
                    else:
                        error_type = 'throttling_error'
                        retry_message = f"AWS rate limit exceeded after {len(tool_results)} tool execution(s). Please wait {suggested_wait} seconds before retrying."
                    
                    yield {
                        'type': error_type,
                        'error': error_type,
                        'detail': error_str,
                        'suggested_wait': suggested_wait,
                        'is_token_throttling': is_token_throttling,
                        'iteration': iteration,
                        'tools_executed': len(tool_results),
                        'can_retry': True,
                        'retry_message': retry_message,
                        'is_transient': is_transient_error,
                        'timestamp': f"{int((time.time() - iteration_start_time) * 1000)}ms"
                    }
                    logger.info(f"🔄 RETRY: Yielded {'transient error' if is_transient_error else 'throttling'} chunk after {len(tool_results)} tools")
                    return
                elif is_auth_error:
                    # For authentication errors, yield a detailed error with helpful message
                    logger.error(f"Authentication error in iteration {iteration}: {error_str}")
                    
                    # Extract the most relevant part of the error message
                    error_message = auth_provider.get_credential_help_message() if auth_provider else "AWS credentials have expired."
                    
                    auth_error_chunk = {
                        'type': 'error',
                        'error': 'authentication_error',
                        'error_type': 'authentication_error',
                        'content': error_message,
                        'detail': error_str,
                        'can_retry': True,
                        'retry_message': error_message
                    }
                    logger.info(f"🔐 AUTH_ERROR: Yielding authentication error chunk: {auth_error_chunk}")
                    yield auth_error_chunk
                    logger.info(f"🔐 AUTH_ERROR: Successfully yielded authentication error chunk")
                    return
                else:
                    # For non-throttling errors, yield generic error
                    logger.error(f"Non-throttling error in iteration {iteration}: {error_str}")
                    yield {'type': 'error', 'content': f'Error: {error_str}'}
                    return
        
        # FINAL REPORT: Log comprehensive usage summary
        if iteration_usages and conversation_id:
            logger.info("\n" + "=" * 80)
            logger.info(f"📊 FINAL USAGE REPORT - Conversation {conversation_id}")
            logger.info("=" * 80)
            logger.info(f"Total Iterations:        {len(iteration_usages)}")
            logger.info(f"Total Fresh Input:       {cumulative_usage.input_tokens:>12,} tokens")
            logger.info(f"Total Cached Input:      {cumulative_usage.cache_read_tokens:>12,} tokens (FREE)")
            logger.info(f"Total Cache Written:     {cumulative_usage.cache_write_tokens:>12,} tokens")
            logger.info(f"Total Output:            {cumulative_usage.output_tokens:>12,} tokens")
            
            total_billable = cumulative_usage.input_tokens + cumulative_usage.output_tokens
            total_potential = total_billable + cumulative_usage.cache_read_tokens
            
            if total_potential > 0:
                overall_savings = (cumulative_usage.cache_read_tokens / total_potential) * 100
                logger.info(f"Overall Cache Efficiency: {overall_savings:>11.1f}%")
                logger.info(f"Total Billable Tokens:   {total_billable:>12,}")
                logger.info(f"Tokens Saved by Cache:   {cumulative_usage.cache_read_tokens:>12,}")
            
            # Check if cache ever worked
            cache_ever_worked = any(u.cache_read_tokens > 0 for u in iteration_usages)
            if not cache_ever_worked and len(iteration_usages) > 1:
                logger.error("🚨 CACHE NEVER ACTIVATED ACROSS ALL ITERATIONS!")
                logger.error("   This conversation used ZERO cached tokens")
                logger.error("   Caching may be disabled or broken")
            
            # Check for throttling events
            throttle_events = [u for u in iteration_usages if getattr(u, 'was_throttled', False)]
            if throttle_events:
                logger.warning(f"⚠️  Throttled {len(throttle_events)} times during this conversation")
            
            logger.info("=" * 80 + "\n")

    def _update_code_block_tracker(self, text: str, tracker: Dict[str, Any]) -> None:
        """Update code block tracking state based on text content."""
        if not text:
            return
            
        # Debug logging to track state changes
        was_in_block = tracker.get('in_block', False)
        was_block_type = tracker.get('block_type')
            
        lines = text.split('\n')
        for line in lines:
            stripped = line.strip()
            if stripped.startswith('```'):
                # Extract potential language/type after ```
                lang_or_type = stripped[3:].strip()
                
                if lang_or_type:
                    # Has a language specifier - this is ALWAYS an opening, even if we're in a block
                    # This handles cases like: ```mermaid\n...\n```vega-lite (no closing ```)
                    if tracker['in_block']:
                        logger.debug(f"🔍 TRACKER: Implicitly closing {tracker['block_type']} block, opening {lang_or_type} block")
                    tracker['in_block'] = True
                    tracker['block_type'] = lang_or_type
                    tracker['accumulated_content'] = line + '\n'
                    logger.debug(f"🔍 TRACKER: Opened {lang_or_type} block")
                elif tracker['in_block']:
                    # No language specifier and we're in a block - this is a closing ```
                    tracker['in_block'] = False
                    tracker['block_type'] = None
                    logger.debug(f"🔍 TRACKER: Closed block")
        
        # Log state changes for debugging
        if was_in_block != tracker.get('in_block') or was_block_type != tracker.get('block_type'):
            logger.debug(f"🔍 TRACKER_STATE_CHANGE: {was_block_type or 'none'}[{was_in_block}] → {tracker.get('block_type') or 'none'}[{tracker.get('in_block')}]")
            logger.debug(f"🔍 TRACKER_TEXT: Processing text: {repr(text[:100])}")

    async def _continue_incomplete_code_block(
        self,
        conversation: List[Dict[str, Any]], 
        code_block_tracker: Dict[str, Any],
        mcp_manager,
        start_time: float,
        assistant_text: str
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """Continue an incomplete code block by making a new API call."""
        try:
            block_type = code_block_tracker['block_type']
            # Preserve diff context in continuation prompt
            if block_type == 'diff':
                continuation_prompt = f"Continue the incomplete diff block from where it left off. Maintain all + and - line prefixes. Output ONLY the continuation of the diff content, preserving the exact diff format."
            else:
                continuation_prompt = f"Continue the incomplete {block_type} code block from where it left off and close it with ```. Output ONLY the continuation of the code block, no explanations."
            
            continuation_conversation = conversation.copy()
            
            # Check if model supports assistant message prefill
            supports_prefill = self.model_config.get('supports_assistant_prefill', True)
            
            if assistant_text.strip() and supports_prefill:
                # Use text truncated to last complete line as assistant prefill
                lines = assistant_text.split('\n')
                if lines and lines[-1].strip():
                    lines = lines[:-1]
                cleaned_text = '\n'.join(lines)
                
                if continuation_conversation and continuation_conversation[-1].get('role') == 'assistant':
                    continuation_conversation[-1]['content'] = [{"type": "text", "text": cleaned_text}]
                else:
                    continuation_conversation.append({"role": "assistant", "content": [{"type": "text", "text": cleaned_text}]})
            elif assistant_text.strip():
                # For models without prefill support, include context in the user prompt
                lines = assistant_text.split('\n')
                last_lines = '\n'.join(lines[-20:]) if len(lines) > 20 else '\n'.join(lines)
                continuation_prompt = f"You were generating content that ended with:\n```\n{last_lines}\n```\n\n{continuation_prompt}"
            
            continuation_conversation.append({"role": "user", "content": continuation_prompt})
            
            body = {
                "messages": continuation_conversation,
                "max_tokens": self.model_config.get('max_output_tokens', 2000),
                "temperature": 0.1,
                "anthropic_version": "bedrock-2023-05-31"
            }
            
            logger.info(f"🔄 CONTINUATION: Making API call to continue {block_type} block")
            
            # Yield initial heartbeat
            yield {
                'type': 'heartbeat',
                'heartbeat': True,
                'timestamp': f"{int((time.time() - start_time) * 1000)}ms"
            }
            
            # Make the Bedrock call - this returns immediately with a stream
            response = self.bedrock.invoke_model_with_response_stream(
                modelId=self.model_id,
                body=json.dumps(body)
            )
            
            # Send heartbeat after getting response object (before first chunk)
            yield {
                'type': 'heartbeat',
                'heartbeat': True,
                'timestamp': f"{int((time.time() - start_time) * 1000)}ms"
            }
            
            accumulated_start = ""
            header_filtered = False
            chunk_count = 0
            continuation_buffer = ""  # Buffer for continuation chunks
            
            for event in response['body']:
                # Send heartbeat every 10 chunks to keep connection alive
                chunk_count += 1
                if chunk_count % 10 == 0:
                    yield {
                        'type': 'heartbeat',
                        'heartbeat': True,
                        'timestamp': f"{int((time.time() - start_time) * 1000)}ms"
                    }
                
                chunk_data = self._decode_chunk_bytes(event['chunk']['bytes'])
                chunk = json.loads(chunk_data)
                
                if chunk['type'] == 'content_block_delta':
                    delta = chunk.get('delta', {})
                    if delta.get('type') == 'text_delta':
                        text = delta.get('text', '')
                        
                        # Buffer continuation text to avoid tiny chunks
                        continuation_buffer += text
                        
                        # Only yield when we have a substantial amount or hit a major boundary
                        should_yield = (
                            len(continuation_buffer) >= 200 or  # Substantial chunk size
                            '```\n' in continuation_buffer or  # Complete code block boundary
                            continuation_buffer.count('\n') >= 5  # Multiple complete lines
                        )
                        
                        if not should_yield:
                            continue
                        
                        text = continuation_buffer
                        continuation_buffer = ""
                        
                        if not header_filtered:
                            accumulated_start += text
                            
                            if '\n' in accumulated_start or len(accumulated_start) > 20:
                                if accumulated_start.strip().startswith('```'):
                                    lines = accumulated_start.split('\n', 1)
                                    if len(lines) > 1:
                                        remaining_text = '\n' + lines[1]  # Preserve the newline
                                        header_type = lines[0].strip()
                                        logger.info(f"🔄 FILTERED: Removed redundant {header_type} from continuation")
                                    else:
                                        remaining_text = ""
                                    
                                    if remaining_text:
                                        yield {
                                            'type': 'text',
                                            'content': remaining_text,
                                            'timestamp': f"{int((time.time() - start_time) * 1000)}ms",
                                            'continuation': True
                                        }
                                else:
                                    yield {
                                        'type': 'text',
                                        'content': accumulated_start,
                                        'timestamp': f"{int((time.time() - start_time) * 1000)}ms",
                                        'continuation': True
                                    }
                                
                                header_filtered = True
                        else:
                            if text:
                                yield {
                                    'type': 'text',
                                    'content': text,
                                    'timestamp': f"{int((time.time() - start_time) * 1000)}ms",
                                    'continuation': True
                                }
            
            # Flush any remaining buffered content
            if continuation_buffer:
                yield {
                    'type': 'text',
                    'content': continuation_buffer,
                    'timestamp': f"{int((time.time() - start_time) * 1000)}ms",
                    'continuation': True
                }
        
        except Exception as e:
            logger.error(f"🔄 CONTINUATION: Error in continuation: {e}")
