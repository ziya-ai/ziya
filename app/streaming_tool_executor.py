#!/usr/bin/env python3
import asyncio
import json
import boto3
import logging
import re
import os
import threading
import time
from botocore.config import Config as BotoConfig
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
            
            # Cap per-conversation list to prevent unbounded growth
            if len(self.conversation_usages[conversation_id]) > 500:
                self.conversation_usages[conversation_id] = self.conversation_usages[conversation_id][-500:]
            
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
        # Also create provider for the new normalized streaming interface.
        # During migration, both self.bedrock and self.provider coexist.
        self.provider = None
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
            except (ImportError, AttributeError, ValueError, KeyError) as e:
                logger.warning(f"🔍 Could not get wrapped client, falling back to direct client: {e}")
                # Fallback to direct client creation
                session = boto3.Session(profile_name=profile_name)
                self.bedrock = session.client(
                    'bedrock-runtime',
                    region_name=region,
                    config=BotoConfig(
                        max_pool_connections=50,
                        retries={'max_attempts': 3, 'mode': 'adaptive'},
                    )
                )
        else:
            # Non-Bedrock endpoints don't need a bedrock client
            self.bedrock = None
            logger.debug(f"Skipping Bedrock client initialization for endpoint: {endpoint}")

        # Create the provider (normalized interface for all endpoints)
        try:
            from app.providers.factory import create_provider
            self.provider = create_provider(
                endpoint=endpoint,
                model_id=self.model_id,
                model_config=self.model_config or {},
                aws_profile=profile_name,
                region=region,
            )
            logger.info(f"StreamingToolExecutor: created {self.provider.provider_name} provider")
        except (ImportError, ValueError, TypeError, KeyError, RuntimeError) as e:
            logger.warning(f"StreamingToolExecutor: provider creation failed ({e}), will use legacy path")
            self.provider = None

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
                except (AttributeError, TypeError, ValueError):
                    logger.warning(f"🔍 TOOL_SCHEMA: Could not convert input_schema, using fallback")
                    result['input_schema'] = {"type": "object", "properties": {}}
            return result
        else:
            # Tool object - extract properties
            name = getattr(tool, 'name', 'unknown')
            description = getattr(tool, 'description', 'No description')
            
            # Check metadata FIRST — it holds the real MCP server schema.
            # getattr(tool, 'input_schema') returns a Pydantic model auto-generated
            # from _run()'s signature, which shadows the real schema with a generic
            # tool_input wrapper. The real schema has actual parameter names, enums,
            # descriptions, and usage warnings that the LLM needs to see.
            input_schema = None
            if hasattr(tool, 'metadata') and isinstance(getattr(tool, 'metadata', None), dict):
                input_schema = tool.metadata.get('input_schema')
            if input_schema is None:
                input_schema = getattr(tool, 'input_schema', None)
            if input_schema is None:
                input_schema = getattr(tool, 'inputSchema', None)
            if input_schema is None:
                input_schema = {}
            
            logger.debug(f"🔍 TOOL_SCHEMA: Converting tool '{name}', input_schema type: {type(input_schema)}")
            
            # Handle different input_schema types
            if isinstance(input_schema, dict):
                # Already a dict, use as-is
                logger.debug(f"🔍 TOOL_SCHEMA: Tool '{name}' has dict schema with keys: {list(input_schema.keys())}")
            elif hasattr(input_schema, 'model_json_schema'):
                # Pydantic class - convert to JSON schema
                try:
                    input_schema = input_schema.model_json_schema()
                    logger.debug(f"🔍 TOOL_SCHEMA: Converted Pydantic schema for '{name}'")
                except (AttributeError, TypeError, ValueError):
                    logger.warning(f"🔍 TOOL_SCHEMA: Failed to convert schema for '{name}', using empty schema")
                    input_schema = {"type": "object", "properties": {}}
            elif input_schema:
                # Some other object - try to convert
                try:
                    input_schema = input_schema.model_json_schema()
                    logger.debug(f"🔍 TOOL_SCHEMA: Converted object schema for '{name}'")
                except (AttributeError, TypeError, ValueError):
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
    
    # Extension-to-Prism-language map. Single source of truth — the frontend
    # never needs its own copy.  Used by _infer_syntax_hint() to derive a
    # content-language tag for tool results so the frontend can apply Prism
    # syntax highlighting generically.
    _EXT_TO_LANG = {
        '.py': 'python', '.pyw': 'python',
        '.js': 'javascript', '.mjs': 'javascript', '.cjs': 'javascript',
        '.ts': 'typescript', '.tsx': 'tsx', '.jsx': 'jsx',
        '.json': 'json', '.jsonl': 'json',
        '.md': 'markdown', '.mdx': 'markdown',
        '.yaml': 'yaml', '.yml': 'yaml', '.toml': 'toml',
        '.html': 'html', '.htm': 'html', '.xml': 'xml', '.svg': 'xml',
        '.css': 'css', '.scss': 'scss', '.less': 'less',
        '.sh': 'bash', '.bash': 'bash', '.zsh': 'bash',
        '.rs': 'rust', '.go': 'go', '.java': 'java',
        '.rb': 'ruby', '.php': 'php', '.swift': 'swift',
        '.c': 'c', '.h': 'c', '.cpp': 'cpp', '.hpp': 'cpp', '.cc': 'cpp',
        '.cs': 'csharp', '.sql': 'sql',
        '.r': 'r', '.R': 'r',
        '.tf': 'hcl', '.hcl': 'hcl',
        '.dockerfile': 'docker', '.Dockerfile': 'docker',
        '.makefile': 'makefile', '.mk': 'makefile',
        '.ini': 'ini', '.cfg': 'ini', '.conf': 'ini',
        '.graphql': 'graphql', '.gql': 'graphql',
        '.proto': 'protobuf',
    }

    def _infer_syntax_hint(self, tool_name: str, args: dict) -> str:
        """Infer a Prism language hint from the tool name and arguments."""
        actual = self._normalize_tool_name(tool_name)
        if actual == 'run_shell_command':
            return 'bash'
        # For any tool that operates on a file path, derive from extension
        file_path = args.get('path') or args.get('file_path') or args.get('url') or ''
        if file_path:
            _, ext = os.path.splitext(file_path)
            if ext:
                return self._EXT_TO_LANG.get(ext.lower(), 'text')
        return 'text'

    def _get_tool_header(self, tool_name: str, args: dict) -> str:
        """Get appropriate header for tool display."""
        actual_tool_name = self._normalize_tool_name(tool_name)
        
        if actual_tool_name == 'run_shell_command':
            return 'Shell Command'
        elif actual_tool_name == 'get_current_time':
            return 'Current Time'
        elif actual_tool_name == 'file_read':
            path = args.get('path', '')
            return f'file read: {path}' if path else 'file read'
        elif actual_tool_name == 'file_write':
            path = args.get('path', '')
            return f'file write: {path}' if path else 'file write'
        elif actual_tool_name == 'file_list':
            path = args.get('path', '.')
            return f'file list: {path}'
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

    # Patterns indicating the model is narrating tool execution in prose
    # rather than using the tool_use API. Checked OUTSIDE code fences only.
    _FABRICATION_PATTERNS = [
        # Dollar-sign shell prompt followed by a command
        re.compile(r'(?:^|\n)\$\s+\S+', re.MULTILINE),
        # "Output:" or "Result:" followed by multi-line content
        re.compile(r'(?:^|\n)(?:Output|Result|Results|Response):\s*\n', re.MULTILINE),
        # Lines that look like shell command execution narrative
        re.compile(r'(?:^|\n)```\s*\n\$\s+', re.MULTILINE),
        # Exit code indicators not from a real tool
        re.compile(r'\[Exit code: \d+\]'),
    ]

    def _sanitize_assistant_text(self, text: str) -> str:
        """Remove hallucinated tool-call narratives from assistant text.

        Scans text outside of code fences for patterns that indicate the
        model was narrating command execution instead of using the tool API.
        Truncates at the first fabrication boundary to prevent contamination
        of conversation history.

        Returns cleaned text (may be shorter than input).
        """
        if not text or len(text) < 20:
            return text

        # Split into fenced and unfenced regions
        # We only check unfenced regions for fabrication
        lines = text.split('\n')
        in_fence = False
        fence_pattern = re.compile(r'^`{3,4}')
        first_contaminated_line = None

        for i, line in enumerate(lines):
            if fence_pattern.match(line.strip()):
                in_fence = not in_fence
                continue
            if in_fence:
                continue

            # Check this unfenced line against fabrication patterns
            for pat in self._FABRICATION_PATTERNS:
                if pat.search(line):
                    first_contaminated_line = i
                    logger.warning(
                        f"\U0001f50d SANITIZE: Detected fabricated tool narrative at line {i}: "
                        f"pattern={pat.pattern}, line={line[:80]}"
                    )
                    break
            if first_contaminated_line is not None:
                break

        if first_contaminated_line is not None:
            # Truncate at the paragraph boundary before contamination
            clean_lines = lines[:first_contaminated_line]
            # Strip trailing whitespace lines
            while clean_lines and not clean_lines[-1].strip():
                clean_lines.pop()
            cleaned = '\n'.join(clean_lines)
            removed_chars = len(text) - len(cleaned)
            logger.warning(
                f"\U0001f50d SANITIZE: Removed {removed_chars} chars of fabricated content "
                f"from assistant text (kept {len(cleaned)}/{len(text)})"
            )
            return cleaned

        return text

    @staticmethod
    def _assistant_text_in_conversation(text: str, conversation: list, lookback: int = 3) -> bool:
        """Check if assistant_text is already in recent conversation history.

        Handles both plain string content and structured content blocks
        (list of dicts from build_assistant_message) so the dedup check
        works regardless of whether tools were involved.
        """
        for m in conversation[-lookback:]:
            if m.get('role') != 'assistant':
                continue
            content = m.get('content')
            if content == text:
                return True
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get('type') == 'text':
                        if block.get('text') == text:
                            return True
        return False

    @staticmethod
    def _build_tool_reinforcement_message() -> Dict[str, Any]:
        """Build a corrective user message reminding the model to use tool APIs.

        Injected into the conversation after hallucination detection to break
        the self-reinforcing loop.
        """
        return {
            "role": "user",
            "content": (
                "IMPORTANT: Your previous response contained text that described "
                "running commands or viewing their output, but you did not actually "
                "invoke any tools. You MUST use the tool calling API (tool_use blocks) "
                "to execute commands \u2014 do not write prose describing what a command "
                "would return. If you need to run a command, call the tool. If you "
                "need to read a file, call the tool. Never fabricate or imagine "
                "tool output."
            ),
        }

    @staticmethod
    def _build_tool_reminder_message() -> Dict[str, Any]:
        """Build a neutral reminder about tool API usage for long conversations.

        Unlike the corrective reinforcement message, this does not accuse the
        model of hallucinating \u2014 it is a gentle nudge to maintain good habits.
        """
        return {
            "role": "user",
            "content": (
                "Reminder: when you need to run commands, read files, or perform "
                "actions, always use the provided tool calling API rather than "
                "describing what the output would be."
            ),
        }

    @staticmethod
    def _build_parrot_corrective_message(parrot_match: Dict[str, Any]) -> Dict[str, Any]:
        """Build a targeted corrective message when the shingle index
        detected the model reproducing a specific prior tool result.

        Unlike the generic fabrication scold, this cites the tool and
        invocation being parroted and offers a concrete recovery path:
        re-call the tool if current state might differ, or quote the
        prior result inside a code fence if referencing it analytically.
        """
        tool_name = parrot_match.get('tool_name') or 'a prior tool'
        tool_use_id = parrot_match.get('tool_use_id') or 'unknown'
        shingle_overlap = parrot_match.get('shingle_overlap', 0)
        line_matches = parrot_match.get('line_matches', 0)
        return {
            "role": "user",
            "content": (
                f"Your response started reproducing the output of `{tool_name}` "
                f"from an earlier invocation (tool_use_id {tool_use_id}, "
                f"{shingle_overlap} shingle overlaps, {line_matches} line matches) "
                "as prose in your assistant text. This is a hallucination pattern: "
                "you are echoing content from a prior real tool result instead of "
                "calling the tool again.\n\n"
                "Choose one of:\n"
                "  1. If the current state might differ from that earlier result, "
                "call the tool again now using the tool_use API.\n"
                "  2. If you were referencing the earlier result analytically, "
                "quote it briefly inside a fenced code block instead of reproducing "
                "it as free prose.\n\n"
                "Do not reproduce tool output as narrative text."
            ),
        }

    async def _execute_fake_tool(self, tool_name, command, assistant_text, tool_results, mcp_manager):
        """Execute a fake tool call detected in the text stream."""
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
            except (OSError, RuntimeError, asyncio.TimeoutError, json.JSONDecodeError) as e:
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
            logger.debug(f"📊 EXTRACT: Found 'File: ' in content, starting parse...")
            logger.debug(f"📊 EXTRACT: Content preview (first 500 chars): {content_to_parse[:500]}")
            
            # Find where the first file content starts
            first_file_pos = content_to_parse.find('File: ')
            first_file_pos = max(0, first_file_pos)  # Ensure it's always defined
            if first_file_pos > 0:
                logger.debug(f"📊 EXTRACT: First file starts at position {first_file_pos}")
                logger.debug(f"📊 EXTRACT: First file section preview: {content_to_parse[first_file_pos:first_file_pos+200]}")
            
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
                    logger.debug(f"📊 EXTRACT: Found file marker #{files_found}: '{line}'")
                    
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
                
                logger.debug(f"📊 EXTRACT: Processed {lines_processed:,} lines, found {files_found} files, extracted {len(file_contents)} files, skipped {lines_skipped_as_line_numbers:,} line number annotations")
                
                # Log total extracted content
                total_extracted_chars = sum(len(content) for content in file_contents.values())
                logger.debug(f"📊 EXTRACT: Total extracted content: {total_extracted_chars:,} chars from {len(file_contents)} files")
        
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

    def _build_provider_config(self, iteration: int, consecutive_empty_tool_calls: int = 0) -> "ProviderConfig":
        """Build a ProviderConfig for the current iteration.

        Translates model_config and environment variables into the
        provider-agnostic ProviderConfig that the provider uses to
        build its request body.
        """
        from app.providers.base import ProviderConfig, ThinkingConfig

        thinking = None
        if self.model_config and self.model_config.get('supports_adaptive_thinking'):
            effort = os.environ.get(
                'ZIYA_THINKING_EFFORT',
                self.model_config.get('thinking_effort_default', 'high'),
            )
            thinking = ThinkingConfig(enabled=True, mode="adaptive", effort=effort)
        elif self.model_config and self.model_config.get('supports_thinking'):
            thinking_on = os.environ.get('ZIYA_THINKING_MODE', '0') == '1'
            if thinking_on:
                budget = int(os.environ.get('ZIYA_THINKING_BUDGET', '16000'))

        # Per-request overrides from active skill modelOverrides
        temp_override = getattr(self, 'temperature_override', None)
        max_tokens_override = getattr(self, 'max_tokens_override', None)

        # Resolve max_output_tokens: skill override > env var > model config > default
        if max_tokens_override:
            effective_max_tokens = max_tokens_override
        elif "ZIYA_MAX_OUTPUT_TOKENS" in os.environ:
            effective_max_tokens = int(os.environ["ZIYA_MAX_OUTPUT_TOKENS"])
        else:
            effective_max_tokens = (self.model_config.get('max_output_tokens', 16384) if self.model_config else 16384)

        return ProviderConfig(
            max_output_tokens=effective_max_tokens,
            temperature=temp_override,  # None = provider default
            thinking=thinking,
            enable_cache=True,
            use_extended_context=False,
            suppress_tools=(consecutive_empty_tool_calls >= 5),
            model_config=self.model_config or {},
            iteration=iteration,
        )

    async def _load_and_prepare_tools(self, extra_tools=None):
        """Load MCP tools, convert schemas, deduplicate, and prepare for provider.
        
        Returns:
            tuple: (all_tools, bedrock_tools, builtin_tool_names, internal_tool_names, optional_only_tools)
        """
        from app.mcp.manager import get_mcp_manager
        mcp_manager = get_mcp_manager()
        if not mcp_manager.is_initialized:
            await mcp_manager.initialize()
        from app.mcp.enhanced_tools import DirectMCPTool, create_secure_mcp_tools

        all_tools = create_secure_mcp_tools()
        
        builtin_tool_names = {tool.name for tool in all_tools if isinstance(tool, DirectMCPTool)}
        internal_tool_names = {
            tool.name for tool in all_tools
            if hasattr(tool, 'metadata') and tool.metadata and tool.metadata.get('is_internal')
        }

        # Merge delegate-injected tools
        if extra_tools:
            for tool_instance in extra_tools:
                direct_tool = DirectMCPTool(tool_instance)
                all_tools.append(direct_tool)
                builtin_tool_names.add(direct_tool.name)
                logger.info(f"🔧 Injected extra tool: {direct_tool.name}")
        
        external_count = len(all_tools) - len(builtin_tool_names)
        logger.info(f"🔧 TOOL_LOADING: {len(all_tools)} tools ({external_count} external, {len(builtin_tool_names)} builtin)")
        if external_count > 0:
            external_names = sorted(t.name for t in all_tools if not isinstance(t, DirectMCPTool))
            logger.info(f"🔧 TOOL_LOADING: External tools: {external_names[:10]}{'...' if len(external_names) > 10 else ''}")

        # Build set of tool names where every input param is optional
        optional_only_tools: set = set()
        for _t in all_tools:
            _schema = getattr(_t, 'InputSchema', None)
            if _schema is not None:
                _fields = getattr(_schema, 'model_fields', {})
                if _fields and all(not f.is_required() for f in _fields.values()):
                    optional_only_tools.add(getattr(_t, 'name', ''))

        # Convert and deduplicate tools
        converted_tools = [self._convert_tool_schema(tool) for tool in all_tools]
        seen_names = set()
        bedrock_tools = []
        for tool in converted_tools:
            tool_name = tool.get('name', 'unknown')
            if tool_name not in seen_names:
                seen_names.add(tool_name)
                if not tool_name.startswith('mcp_') and tool_name not in builtin_tool_names:
                    tool['name'] = f'mcp_{tool_name}'
                bedrock_tools.append(tool)

        return all_tools, bedrock_tools, builtin_tool_names, internal_tool_names, optional_only_tools

    def _build_conversation_from_messages(self, messages):
        """Convert input messages (LangChain or dict format) to provider conversation format.
        
        Returns:
            tuple: (conversation, system_content)
        """
        conversation = []
        system_content = None

        for i, msg in enumerate(messages):
            if hasattr(msg, 'type') and hasattr(msg, 'content'):
                role = msg.type if msg.type != 'human' else 'user'
                content = msg.content
            elif isinstance(msg, str):
                role = 'user'
                content = msg
            else:
                role = msg.get('role', '')
                content = msg.get('content', '')
            
            if isinstance(content, list):
                logger.debug(f"🖼️ Message {i} has multi-modal content with {len(content)} blocks")
            
            if role == 'system':
                system_content = content
            elif role in ('user', 'assistant', 'ai'):
                bedrock_role = 'assistant' if role == 'ai' else role
                conversation.append({"role": bedrock_role, "content": content})

        return conversation, system_content

    def _should_continue_or_end_stream(self, assistant_text, tools_executed, iteration,
                                        code_block_tracker, continuation_happened,
                                        last_stop_reason, blocked_tools_count):
        """Determine whether to continue iterating or end the stream.
        
        Returns:
            str: 'continue', 'end', or 'end_no_prefill'
        """
        if tools_executed:
            return 'continue'
        
        if blocked_tools_count >= 3:
            logger.warning(f"🔍 RUNAWAY_LOOP_DETECTED: {blocked_tools_count} tools blocked, ending stream")
            return 'end'
        
        if not assistant_text.strip():
            if iteration >= 100:
                return 'end'
            return 'end'  # No tools, no text — done
        
        # For non-prefill models, check if we need special handling
        supports_prefill = self.model_config.get('supports_assistant_prefill', True)
        if not supports_prefill and not tools_executed:
            if last_stop_reason == 'max_tokens' and iteration < 199:
                return 'continue_no_prefill'  # Inject continuation prompt
            return 'end_no_prefill'
        
        # Short stable responses at iteration >= 1 indicate completion
        if iteration >= 1 and len(assistant_text.strip()) < 50:
            return 'end'
        
        # Still in an incomplete code block — must continue
        if code_block_tracker.get('in_block'):
            return 'continue'
        
        # Continuation just happened — let model respond naturally
        if continuation_happened:
            return 'continue'
        
        # Check if there's substantial commentary after last structured content
        text_after_last_block = self._get_text_after_last_structured_content(assistant_text)
        word_count = len(text_after_last_block.split()) if text_after_last_block else 0
        
        # 20+ words ending with punctuation = complete
        if word_count >= 20 and text_after_last_block.rstrip().endswith(('.', '!', '?')):
            return 'end'
        
        # Check for continuation hints
        text_end = assistant_text[-200:].strip()
        suggests_continuation = (
            text_end.endswith(':') or
            assistant_text.endswith('\x60\x60\x60') or
            (word_count < 20 and not text_after_last_block.rstrip().endswith(('.', '!', '?')))
        )
        
        if suggests_continuation and iteration < 2:
            return 'continue'
        
        return 'end'

    def _classify_and_handle_error(self, error, error_str, iteration, tool_results,
                                    throttle_state, inter_tool_delay, iteration_usages,
                                    provider_config):
        """Classify an error and determine handling strategy.
        
        Returns:
            dict with keys:
                'type': 'throttling'|'read_timeout'|'transient'|'auth'|'generic'
                'should_retry': bool
                'delay': float (seconds to wait before retry)
                'reduced_max_tokens': int or None
                'error_message': str
                'error_chunk': dict (the chunk to yield to frontend)
        """
        from app.plugins import get_active_auth_provider
        from app.utils.custom_exceptions import KnownCredentialException
        
        is_throttling = any(ind in error_str for ind in [
            "ThrottlingException", "Too many tokens", "Too many requests", "Rate exceeded"])
        is_read_timeout = any(ind in error_str for ind in [
            "Read timed out", "ReadTimeoutError", "Read timeout", "request timed out",
            "read operation timed out", "ConnectionResetError", "Connection reset by peer"])
        is_transient = any(ind in error_str for ind in [
            "internalServerException", "ServiceUnavailableException",
            "The system encountered an unexpected error"])
        
        auth_provider = get_active_auth_provider()
        is_auth = (
            isinstance(error, KnownCredentialException) or
            (auth_provider and auth_provider.is_auth_error(error_str))
        )
        
        if is_auth:
            error_message = auth_provider.get_credential_help_message() if auth_provider else "AWS credentials have expired."
            return {
                'type': 'auth',
                'should_retry': False,
                'delay': 0,
                'reduced_max_tokens': None,
                'error_message': error_message,
                'error_chunk': {
                    'type': 'error', 'error': 'authentication_error',
                    'error_type': 'authentication_error',
                    'content': error_message, 'detail': error_str,
                    'can_retry': True, 'retry_message': error_message
                }
            }
        
        if not (is_throttling or is_read_timeout or is_transient):
            return {
                'type': 'generic',
                'should_retry': False,
                'delay': 0,
                'reduced_max_tokens': None,
                'error_message': error_str,
                'error_chunk': {'type': 'error', 'content': f'Error: {error_str}'}
            }
        
        # Update inter-tool delay on throttle
        old_delay = inter_tool_delay['current']
        inter_tool_delay['current'] = min(
            inter_tool_delay['max'],
            inter_tool_delay['current'] * inter_tool_delay['growth_factor'])
        inter_tool_delay['last_was_throttled'] = True
        
        # Track cache health
        if iteration_usages:
            last_usage = iteration_usages[-1]
            total_processed = last_usage.input_tokens + last_usage.cache_read_tokens
            if iteration > 0 and total_processed > 10000 and last_usage.cache_read_tokens == 0:
                throttle_state['cache_working'] = False
            elif last_usage.cache_read_tokens > 0:
                throttle_state['cache_working'] = True
                throttle_state['last_cache_efficiency'] = last_usage.cache_hit_rate
        
        throttle_state['retry_count'] += 1
        
        # Calculate backoff delay
        if is_read_timeout:
            time_delay = min(throttle_state['base_delay'] * (2 ** (throttle_state['retry_count'] - 1)), 15)
        else:
            time_delay = min(throttle_state['base_delay'] * (2 ** throttle_state['retry_count']), 30)
        
        # Calculate token reduction
        current_max = provider_config.max_output_tokens
        if is_read_timeout:
            reduction = 1.0
        elif throttle_state['cache_working'] == False:
            reduction = 0.5
        elif throttle_state['retry_count'] > 2:
            reduction = 0.6
        else:
            reduction = 0.75
        
        reduced_max = max(int(current_max * reduction), 2048)
        throttle_state['max_output_tokens_override'] = reduced_max
        
        should_retry_internally = (
            (is_read_timeout or is_transient) and
            throttle_state['retry_count'] <= throttle_state['max_retries']
        )
        
        # Determine error type for frontend
        if is_transient:
            error_type = 'transient_service_error'
            retry_msg = f"AWS service temporarily unavailable after {len(tool_results)} tool execution(s). Retrying..."
        elif is_read_timeout:
            error_type = 'throttling_error'
            retry_msg = f"Connection timed out. Retrying in {time_delay}s... (attempt {throttle_state['retry_count']}/{throttle_state['max_retries']})"
        else:
            error_type = 'throttling_error'
            retry_msg = f"AWS rate limit exceeded after {len(tool_results)} tool execution(s). Please wait before retrying."
        
        return {
            'type': 'read_timeout' if is_read_timeout else ('transient' if is_transient else 'throttling'),
            'should_retry': should_retry_internally,
            'delay': time_delay,
            'reduced_max_tokens': reduced_max,
            'error_message': retry_msg,
            'error_chunk': {
                'type': error_type, 'error': error_type,
                'detail': error_str,
                'suggested_wait': 60,
                'is_token_throttling': "Too many tokens" in error_str,
                'iteration': iteration,
                'tools_executed': len(tool_results),
                'can_retry': True,
                'retry_message': retry_msg,
                'is_transient': is_transient,
            }
        }

    def _handle_usage_event(
        self,
        stream_event,
        iteration_usage: 'IterationUsage',
        iteration: int,
        conversation_id: Optional[str],
        conversation: List[Dict[str, Any]],
        system_content,
        throttle_state: dict,
    ) -> None:
        """Process a UsageEvent: update iteration_usage, throttle state, and run calibration.

        This is a pure side-effect method — it mutates *iteration_usage* and
        *throttle_state* in place and returns nothing.  All calibration and
        accuracy-tracking logic that previously lived inline in the streaming
        loop is consolidated here.
        """
        # --- Update iteration usage from provider event ---
        iteration_usage.input_tokens = stream_event.input_tokens or iteration_usage.input_tokens
        iteration_usage.output_tokens = stream_event.output_tokens or iteration_usage.output_tokens
        iteration_usage.cache_read_tokens = stream_event.cache_read_tokens or iteration_usage.cache_read_tokens
        iteration_usage.cache_write_tokens = stream_event.cache_write_tokens or iteration_usage.cache_write_tokens

        total_input = iteration_usage.input_tokens + iteration_usage.cache_read_tokens
        fresh = iteration_usage.input_tokens
        cached = iteration_usage.cache_read_tokens

        # --- Logging ---
        if iteration == 0:
            logger.debug("Usage from provider:")
            logger.debug(f"   input_tokens: {stream_event.input_tokens}")
            logger.debug(f"   output_tokens: {stream_event.output_tokens}")
            logger.debug(f"   cache_read_tokens: {stream_event.cache_read_tokens}")
            logger.debug(f"   cache_write_tokens: {stream_event.cache_write_tokens}")
        elif cached > 0:
            throttle_state['cache_working'] = True
            throttle_state['last_cache_efficiency'] = iteration_usage.cache_hit_rate
            logger.debug(f"✅ CACHE WORKING: {cached:,} tokens reused")

            # Warn when total tokens approach the model's context limit
            base_limit = self.model_config.get('token_limit', 200000) if self.model_config else 200000
            effective_limit = (
                self.model_config.get('extended_context_limit', base_limit)
                if self.model_config and self.model_config.get('supports_extended_context')
                else base_limit
            )
            throttle_warn_threshold = int(effective_limit * 0.8)
            if total_input > throttle_warn_threshold:
                logger.warning(
                    f"⚠️  HIGH THROTTLE RISK: Processing {total_input:,} total tokens "
                    f"(limit: {effective_limit:,}, threshold: {throttle_warn_threshold:,})"
                )
                logger.warning(f"   Even though {cached:,} are cached (free),")
                logger.warning("   they STILL count toward 'Too many tokens' rate limits")
                logger.warning("   Consider reducing max_output_tokens on retries")

        # --- Accuracy tracking (iteration 0 only) ---
        if iteration == 0 and conversation_id:
            self._track_estimation_accuracy(
                iteration_usage, conversation, system_content,
                fresh, cached, total_input,
            )

        # --- Calibration recording (iteration 0 only) ---
        logger.debug(
            f"📊 Calibration: iter={iteration}, total_input={total_input:,}, "
            f"cache_write={iteration_usage.cache_write_tokens:,}"
        )

        if iteration == 0:
            self._record_calibration(
                iteration_usage, conversation_id, conversation,
                system_content, fresh, cached, total_input,
            )

    # -- Private helpers for _handle_usage_event --

    def _track_estimation_accuracy(
        self,
        iteration_usage: 'IterationUsage',
        conversation: list,
        system_content,
        fresh: int,
        cached: int,
        total_input: int,
    ) -> None:
        """Compare our token estimate against actual usage from the provider."""
        try:
            try:
                from app.utils.token_calibrator import get_token_calibrator
                calibrator = get_token_calibrator()
                has_calibration = True
            except (ImportError, FileNotFoundError, PermissionError) as e:
                logger.warning(f"📊 CALIBRATION_UNAVAILABLE: {type(e).__name__}: {e}")
                calibrator = None
                has_calibration = False
            except (OSError, RuntimeError, ValueError) as e:
                logger.error(f"📊 CALIBRATION_ERROR: Unexpected error loading calibrator: {e}")
                calibrator = None
                has_calibration = False

            estimated_tokens = 0
            estimation_method = "naive (4.0 chars/token)"
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
                except (ImportError, KeyError, AttributeError) as e:
                    logger.warning(f"📊 ESTIMATE-FAMILY: Failed to get model family: {e}")
                    estimation_model_family = 'claude'

            # Estimate tokens from conversation messages
            for msg in conversation:
                estimated_tokens += self._estimate_message_tokens(
                    msg, calibrator, has_calibration, estimation_model_family
                )

            # Include system content
            if system_content:
                estimated_tokens += self._estimate_content_tokens(
                    system_content, calibrator, has_calibration, estimation_model_family
                )

            # Add baseline overhead
            if has_calibration and estimation_model_family:
                try:
                    baseline_overhead = calibrator.get_baseline_overhead(model_family=estimation_model_family)
                    if baseline_overhead > 0:
                        estimated_tokens += baseline_overhead
                except (KeyError, AttributeError, TypeError):
                    pass

            # Compare to actual
            cache_written = iteration_usage.cache_write_tokens
            actual_tokens = (fresh + cache_written) if cache_written > 0 else total_input
            error_pct = (abs(estimated_tokens - actual_tokens) / actual_tokens * 100) if actual_tokens > 0 else 0

            logger.debug(
                f"📊 Calibration: estimated={estimated_tokens:,} actual={actual_tokens:,} "
                f"error=±{error_pct:.1f}% ({estimation_method}, fresh={fresh:,} cached={cached:,})"
            )
        except (ImportError, KeyError, AttributeError, ValueError, ZeroDivisionError) as e:
            logger.debug(f"Error in accuracy tracking: {e}")

    @staticmethod
    def _estimate_message_tokens(msg: dict, calibrator, has_calibration: bool, model_family) -> int:
        """Estimate token count for a single conversation message."""
        tokens = 0
        content = msg.get('content', '')
        if isinstance(content, str):
            if has_calibration and calibrator:
                tokens += calibrator.estimate_tokens(content, model_family=model_family)
            else:
                tokens += len(content) // 4
        elif isinstance(content, list):
            for block in content:
                if not isinstance(block, dict):
                    continue
                block_type = block.get('type')
                if block_type == 'text':
                    text = block.get('text', '')
                    if has_calibration and calibrator:
                        tokens += calibrator.estimate_tokens(text, model_family=model_family)
                    else:
                        tokens += len(text) // 4
                elif block_type == 'tool_result':
                    tr_content = block.get('content', '')
                    if isinstance(tr_content, str):
                        if has_calibration and calibrator:
                            tokens += calibrator.estimate_tokens(tr_content, model_family=model_family)
                        else:
                            tokens += len(tr_content) // 4
                elif block_type == 'tool_use':
                    input_json = json.dumps(block.get('input', {}))
                    tokens += len(input_json) // 4
        return tokens

    @staticmethod
    def _estimate_content_tokens(content, calibrator, has_calibration: bool, model_family) -> int:
        """Estimate token count for system content (string or list of blocks)."""
        tokens = 0
        if isinstance(content, str):
            if has_calibration and calibrator:
                tokens += calibrator.estimate_tokens(content, model_family=model_family)
            else:
                tokens += len(content) // 4
        elif isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get('type') == 'text':
                    text = block.get('text', '')
                    if has_calibration and calibrator:
                        tokens += calibrator.estimate_tokens(text, model_family=model_family)
                    else:
                        tokens += len(text) // 4
        return tokens

    def _record_calibration(
        self,
        iteration_usage: 'IterationUsage',
        conversation_id: Optional[str],
        conversation: list,
        system_content,
        fresh: int,
        cached: int,
        total_input: int,
    ) -> None:
        """Record actual token usage for future calibration improvement."""
        try:
            from app.utils.token_calibrator import get_token_calibrator
            calibrator = get_token_calibrator()

            file_contents = self._extract_file_contents_from_messages(conversation, system_content)
            if not file_contents or total_input <= 0:
                return

            from app.agents.models import ModelManager
            model_id = ModelManager.get_model_id()
            if isinstance(model_id, dict):
                model_id = list(model_id.values())[0]

            endpoint = os.environ.get("ZIYA_ENDPOINT", "bedrock")
            model_name = os.environ.get("ZIYA_MODEL")
            model_config = ModelManager.get_model_config(endpoint, model_name)
            model_family = model_config.get('family', 'default')

            # Derive file-only tokens by proportional character attribution.
            # Use total_input (actual Bedrock token count for entire request)
            # and estimate file content's share of total input characters.
            file_chars = sum(len(c) for c in file_contents.values())
            system_chars = len(system_content or '')
            conversation_chars = sum(
                len(str(m.get('content', ''))) for m in conversation
            )
            total_input_chars = max(1, system_chars + conversation_chars)
            file_only_tokens = max(1, int(total_input * (file_chars / total_input_chars)))

            calibrator.record_actual_usage(
                conversation_id=conversation_id,
                file_contents=file_contents,
                actual_tokens=file_only_tokens,
                model_id=str(model_id),
                model_family=model_family,
            )
            logger.debug(f"📊 CALIBRATION: Recorded {len(file_contents)} files for {model_family}")
        except (ImportError, KeyError, AttributeError, OSError, ValueError) as calib_error:
            logger.error(f"📊 CALIBRATION ERROR: {calib_error}")

    async def stream_with_tools(self, messages: List[Dict[str, Any]], tools: Optional[List] = None, conversation_id: Optional[str] = None, project_root: Optional[str] = None, is_delegate: bool = False, extra_tools: Optional[List] = None) -> AsyncGenerator[Dict[str, Any], None]:
        # --- Concurrent feedback monitor ---
        # Instead of relying solely on discrete polling points, run a
        # background task that continuously drains the feedback queue and
        # deposits messages into a thread-safe list.  The main loop picks
        # them up on every iteration / tool boundary without risk of
        # missing messages that arrive during long tool executions.
        _pending_feedback: List[dict] = []
        _feedback_monitor_task: Optional[asyncio.Task] = None

        async def _feedback_monitor(conv_id: str):
            """Background coroutine that drains the feedback queue continuously."""
            try:
                from app.server import active_feedback_connections
                while True:
                    try:
                        if conv_id not in active_feedback_connections:
                            await asyncio.sleep(0.3)
                            continue
                        conns = active_feedback_connections[conv_id]
                        if not conns:
                            await asyncio.sleep(0.3)
                            continue
                        queue = conns[0].get('feedback_queue')
                        if not queue:
                            await asyncio.sleep(0.3)
                            continue

                        # Block for up to 1s, then loop to re-check cancellation
                        try:
                            data = await asyncio.wait_for(queue.get(), timeout=1.0)
                        except asyncio.TimeoutError:
                            continue

                        if data.get('type') == 'interrupt':
                            _pending_feedback.append({'type': 'interrupt'})
                            logger.info(f"🔄 FEEDBACK_MONITOR: Captured interrupt for {conv_id}")
                        elif data.get('type') == 'tool_feedback':
                            msg = data.get('message', '')
                            _pending_feedback.append({'type': 'feedback', 'message': msg})
                            logger.info(f"🔄 FEEDBACK_MONITOR: Captured feedback for {conv_id}: {msg[:60]}")

                            # Send acknowledgment back through WebSocket
                            try:
                                ws = conns[0].get('websocket')
                                if ws:
                                    import json as _json
                                    await ws.send_json({
                                        'type': 'feedback_status',
                                        'status': 'queued',
                                        'feedback_id': data.get('feedback_id', ''),
                                        'message': msg[:80],
                                    })
                            except (OSError, RuntimeError, ConnectionError) as ack_err:
                                logger.debug(f"Could not send feedback ack: {ack_err}")
                    except asyncio.CancelledError:
                        raise
                    except (OSError, RuntimeError, asyncio.QueueEmpty, KeyError) as inner_err:
                        logger.debug(f"Feedback monitor inner error: {inner_err}")
                        await asyncio.sleep(0.5)
            except asyncio.CancelledError:
                logger.debug(f"🔄 FEEDBACK_MONITOR: Stopped for {conv_id}")

        def _drain_pending_feedback() -> List[dict]:
            """Atomically drain all pending feedback messages."""
            if not _pending_feedback:
                return []
            drained = _pending_feedback.copy()
            _pending_feedback.clear()
            return drained

        # Initialize streaming metrics
        stream_metrics = {
            'events_sent': 0,
            'bytes_sent': 0,
            'chunk_sizes': [],
            'start_time': time.time()
        }
        
        def track_yield(event_data):
            """Track metrics for yielded events"""
            # SDO-183: Strip hidden characters from model output before sending
            # to the user, preventing exfiltration via Unicode tag smuggling.
            if isinstance(event_data, dict):
                content = event_data.get('content')
                if isinstance(content, str):
                    from app.mcp.response_validator import sanitize_text
                    event_data['content'] = sanitize_text(content)

            chunk_size = len(json.dumps(event_data))
            stream_metrics['events_sent'] += 1
            stream_metrics['bytes_sent'] += chunk_size
            # Keep only the last 100 chunk sizes to avoid unbounded list growth
            chunk_sizes = stream_metrics['chunk_sizes']
            chunk_sizes.append(chunk_size)
            if len(chunk_sizes) > 100:
                del chunk_sizes[:len(chunk_sizes) - 100]
            
            if stream_metrics['events_sent'] % 100 == 0:
                logger.debug(f"📊 Stream metrics: {stream_metrics['events_sent']} events, "
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
            except (ImportError, AttributeError) as e:
                logger.warning(f"🔍 EXTENDED_CONTEXT: Could not set conversation_id: {e}")
        
        # Load and prepare tools
        all_tools, bedrock_tools, builtin_tool_names, internal_tool_names, optional_only_tools = await self._load_and_prepare_tools(extra_tools)
        from app.mcp.enhanced_tools import DirectMCPTool
        from app.mcp.manager import get_mcp_manager
        mcp_manager = get_mcp_manager()

        # Build conversation from messages
        conversation, system_content = self._build_conversation_from_messages(messages)
        logger.debug(f"🔍 STREAMING_TOOL_EXECUTOR: Built conversation with {len(conversation)} messages")

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
            'max_output_tokens_override': None,
        }
        
        # Adaptive inter-tool delay — sliding window that collapses toward
        # 0.1s when things are smooth and grows when throttling is detected.
        inter_tool_delay = {
            'current': 0.1,      # Starting delay (seconds) — minimal, grows on throttle
            'min': 0.1,          # Floor — never go below this
            'max': 3.0,          # Ceiling — cap even under heavy throttling
            'decay_factor': 0.6, # Multiply by this on each success (shrinks fast)
            'growth_factor': 2.5,# Multiply by this on each throttle (grows fast)
            'last_was_throttled': False,
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
                # Also re-establish if the MCP tool count has changed since last measurement
                current_tool_count = len(bedrock_tools) if bedrock_tools else 0
                previous_tool_count = calibrator.baseline_tool_counts.get(model_family, 0)
                should_establish_baseline = (
                    model_family not in calibrator.baselines_measured
                    or current_tool_count != previous_tool_count
                )
            except (ImportError, KeyError, AttributeError) as e:
                logger.debug(f"Could not check baseline status: {e}")
        
        hallucination_retries = 0

        # Start the concurrent feedback monitor
        if conversation_id:
            _feedback_monitor_task = asyncio.create_task(_feedback_monitor(conversation_id))
            logger.debug(f"🔄 FEEDBACK_MONITOR: Started for {conversation_id}")

        # Guard: provider must be available
        if self.provider is None:
            yield {'type': 'error', 'content': 'LLM provider failed to initialize. Check endpoint configuration and credentials.'}
            yield {'type': 'stream_end'}
            if _feedback_monitor_task:
                _feedback_monitor_task.cancel()
            return

        max_iterations = int(os.environ.get('ZIYA_MAX_TOOL_ITERATIONS', '200'))
        for iteration in range(max_iterations):
            logger.debug(f"🔍 ITERATION_START: Beginning iteration {iteration}")
            hallucination_this_iteration = False
            parrot_match_this_iteration: Optional[Dict[str, Any]] = None
            
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

                    # Baseline calibration via invoke_model requires a Bedrock client.
                    # For non-Bedrock endpoints (Anthropic direct, OpenAI, Google),
                    # use the provider's count_tokens API if available, otherwise
                    # estimate from serialized JSON size.
                    if self.bedrock is None:
                        logger.info(f"📊 BASELINE: No Bedrock client — using provider token counting")
                        baseline_tokens = 0
                        
                        # Try provider's count_tokens API (Anthropic direct has this)
                        try:
                            if self.provider and hasattr(self.provider, 'client'):
                                import anthropic as _anthropic
                                sync_client = _anthropic.Anthropic(api_key=self.provider.client.api_key)
                                count_kwargs = {
                                    "model": self.model_id,
                                    "messages": [{"role": "user", "content": "Hello"}],
                                }
                                if system_content:
                                    count_kwargs["system"] = system_content if isinstance(system_content, str) else system_content
                                if bedrock_tools:
                                    count_kwargs["tools"] = bedrock_tools
                                resp = sync_client.messages.count_tokens(**count_kwargs)
                                baseline_tokens = resp.input_tokens
                                logger.info(f"📊 BASELINE: count_tokens API returned {baseline_tokens:,} tokens")
                        except (ImportError, AttributeError, TypeError, RuntimeError) as ct_err:
                            logger.info(f"📊 BASELINE: count_tokens failed ({ct_err}), using JSON size estimate")
                        
                        # Fallback: estimate from JSON size with higher multiplier
                        if baseline_tokens == 0:
                            tool_json_size = len(json.dumps(bedrock_tools)) if bedrock_tools else 0
                            # Anthropic's internal tool formatting adds ~2.5x overhead
                            estimated_tool_tokens = int(tool_json_size * 2.5 / 3.5)
                            sys_size = len(system_content) if isinstance(system_content, str) else 0
                            estimated_sys_tokens = int(sys_size / 3.5)
                            baseline_tokens = estimated_tool_tokens + estimated_sys_tokens

                        calibrator.baseline_overhead_tokens[model_family] = baseline_tokens
                        calibrator.baselines_measured.add(model_family)
                        calibrator._save_calibration_data()
                        logger.info(f"✅ BASELINE (estimated): {baseline_tokens:,} tokens "
                                    f"(tools: {estimated_tool_tokens:,}, system: {estimated_sys_tokens:,})")
                        # Skip the Bedrock invoke_model path below
                        raise RuntimeError("Baseline established via estimation")
                    
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
                    # Unwrap all layers to reach the raw boto3 client
                    if hasattr(self.bedrock, 'unwrap'):
                        raw_client = self.bedrock.unwrap()
                    else:
                        raw_client = self.bedrock
                    
                    logger.info(f"📊 BASELINE: Using raw client type: {type(raw_client).__name__}")
                    
                    baseline_body_json = json.dumps(baseline_body)
                    baseline_response = await asyncio.get_event_loop().run_in_executor(
                        None,
                        lambda: raw_client.invoke_model(
                            modelId=self.model_id,
                            body=baseline_body_json,
                        ),
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
                except RuntimeError:
                    pass  # "Baseline established via estimation" — expected control flow from non-Bedrock path
                except (ImportError, KeyError, AttributeError, OSError, ValueError) as e:
                    logger.debug(f"📊 BASELINE: Establishment failed (will retry next time): {e}")
                    logger.warning(f"📊 BASELINE: Establishment failed (will retry next time): {e}")
            
            # WARNING: Approaching iteration limit - notify model to wrap up
            iterations_remaining = max_iterations - iteration
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
                # Yield to event loop so the feedback monitor task can deposit
                # any items it picked up from the asyncio Queue.
                await asyncio.sleep(0)
                for fb in _drain_pending_feedback():
                    if fb['type'] == 'interrupt':
                        yield track_yield({'type': 'text', 'content': '\n\n**User requested stop.**\n\n'})
                        yield track_yield({'type': 'stream_end'})
                        if _feedback_monitor_task:
                            _feedback_monitor_task.cancel()
                        return
                    feedback_message = fb.get('message', '')
                    if any(w in feedback_message.lower() for w in ['stop', 'halt', 'abort', 'cancel', 'quit']):
                        yield track_yield({'type': 'text', 'content': f"\n\n**User feedback:** {feedback_message}\n**Stopping execution as requested.**\n\n"})
                        yield track_yield({'type': 'stream_end'})
                        if _feedback_monitor_task:
                            _feedback_monitor_task.cancel()
                        return
                    logger.info(f"🔄 FEEDBACK_INTEGRATION: Iteration-level directive: {feedback_message}")
                    conversation.append({
                        "role": "user",
                        "content": f"[User feedback]: {feedback_message}"
                    })
                    yield track_yield({
                        'type': 'text',
                        'content': f"\n\n**📝 Feedback received:** {feedback_message}\n**Adjusting approach...**\n\n"
                    })
                    # Send SSE ack so frontend can update status
                    yield track_yield({
                        'type': 'feedback_delivered',
                        'message': feedback_message[:80],
                    })
            
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
            last_stop_reason = None  # Track whether model was cut off (max_tokens) or finished (end_turn)
            empty_tool_calls_this_iteration = 0  # Track empty tool calls in this iteration
            thinking_text = ""  # Track thinking/reasoning content (DeepSeek R1)
            thinking_tag_opened = False  # Whether we've emitted the opening <thinking-data> tag
            deferred_feedback_messages: List[str] = []  # Feedback caught during tool exec, injected after conversation is built
            continuation_happened = False  # Track if code block continuation occurred

            # Streaming repetition detection state (reset per iteration)
            self._recent_sentences = []
            self._sentence_buffer = ''
            self._repetition_suppressed = False
            
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

            # Build provider-agnostic config for this iteration
            provider_config = self._build_provider_config(iteration, consecutive_empty_tool_calls)

            # Periodic tool-use reinforcement for long conversations.
            # After many iterations the model can drift into narrating tool
            # calls instead of invoking them.  A brief reminder every 15
            # iterations keeps it on track without cluttering short sessions.
            if iteration > 0 and iteration % 15 == 0 and tools:
                # Only inject if the conversation doesn't already end with
                # a reinforcement message (avoid stacking them)
                last_content = conversation[-1].get('content', '') if conversation else ''
                if isinstance(last_content, str) and 'always use the provided tool calling API' not in last_content:
                    conversation.append(self._build_tool_reminder_message())
                    logger.info(
                        f"🔄 REINFORCEMENT: Injected periodic tool-use reminder "
                        f"at iteration {iteration}"
                    )

            # Apply throttle-driven token reduction if active
            if throttle_state.get('max_output_tokens_override'):
                provider_config.max_output_tokens = throttle_state['max_output_tokens_override']

            logger.debug(f"🔍 REQUEST_DEBUG: Iteration {iteration}, provider={self.provider.provider_name}")
            logger.debug(f"   Messages: {len(conversation)}, max_tokens: {provider_config.max_output_tokens}")
            total_chars = sum(
                len(msg.get('content', '')) if isinstance(msg.get('content'), str)
                else sum(
                    len(b.get('text', '')) if b.get('type') == 'text'
                    else len(b.get('content', '')) if b.get('type') == 'tool_result' and isinstance(b.get('content'), str)
                    else len(json.dumps(b.get('input', {}))) if b.get('type') == 'tool_use'
                    else 0
                    for b in msg.get('content', [])
                    if isinstance(b, dict)
                )
                for msg in conversation
                if isinstance(msg, dict)
            )
            logger.debug(f"   Total conversation size: {total_chars:,} chars across {len(conversation)} messages")

            try:
                iteration_start_time = time.time()
                
                # Initialize tool_results early so it's available in exception handlers
                tool_results = []
                tool_use_blocks = []
                
                # Track usage for this specific iteration
                iteration_usage = IterationUsage()
                
                # Process this iteration's stream - collect ALL tool calls first
                assistant_text = ""
                yielded_text_length = 0  # Track how much text we've yielded
                all_tool_calls = []  # Collect all tool calls from this response
                
                active_tools = {}
                expected_tools = set()
                completed_tools = set()
                skipped_tools = set()  # Track tools we're skipping due to limits
                executed_tool_signatures = set()  # Track tool name + args to prevent duplicates
                _feedback_received = False  # When True, skip remaining tools so model sees feedback immediately
                
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
                
                # Text delta processing state — shared across all text_delta
                # events within this iteration.
                from app.text_delta_processor import TextDeltaState
                _td_state = TextDeltaState(
                    code_block_tracker=code_block_tracker,
                    iteration_start_time=iteration_start_time,
                    conversation_id=conversation_id,
                )

                # Track event count for debugging
                event_count = 0
                
                # Periodic feedback check state — check every 50 events
                # or every 2 seconds during text streaming
                _last_feedback_check_time = time.time()

                # Stream via provider — yields normalized StreamEvent objects
                from app.providers.base import (
                    TextDelta, ToolUseStart, ToolUseInput, ToolUseEnd,
                    UsageEvent, ThinkingDelta, ErrorEvent, StreamEnd,
                    ProcessingEvent)
                async for stream_event in self.provider.stream_response(
                    conversation, system_content, bedrock_tools, provider_config
                ):
                    event_count += 1

                    # --- Periodic feedback check during streaming ---
                    # Without this, feedback sent while the model is
                    # streaming text sits in _pending_feedback until
                    # message_stop, which can be minutes later.
                    _now = time.time()
                    if (event_count % 50 == 0 or _now - _last_feedback_check_time > 2.0):
                        _last_feedback_check_time = _now
                        await asyncio.sleep(0)  # let monitor deposit
                        for _mid_stream_fb in _drain_pending_feedback():
                            if _mid_stream_fb['type'] == 'interrupt':
                                yield track_yield({'type': 'text', 'content': '\n\n**User requested stop.**\n\n'})
                                yield track_yield({'type': 'stream_end'})
                                if _feedback_monitor_task:
                                    _feedback_monitor_task.cancel()
                                return
                            _fb_msg = _mid_stream_fb.get('message', '')
                            if any(w in _fb_msg.lower() for w in ['stop', 'halt', 'abort', 'cancel', 'quit']):
                                yield track_yield({'type': 'text', 'content': f"\n\n**User feedback:** {_fb_msg}\n**Stopping as requested.**\n\n"})
                                yield track_yield({'type': 'stream_end'})
                                if _feedback_monitor_task:
                                    _feedback_monitor_task.cancel()
                                return
                            # Non-stop feedback: flag it for injection after this stream completes
                            _pending_feedback.append(_mid_stream_fb)
                            logger.info(f"🔄 MID_STREAM_FEEDBACK: Captured feedback during streaming, will inject after message_stop: {_fb_msg[:60]}")

                    # --- Usage tracking ---
                    if isinstance(stream_event, UsageEvent):
                        self._handle_usage_event(
                            stream_event, iteration_usage, iteration,
                            conversation_id, conversation, system_content,
                            throttle_state,
                        )
                        continue  # UsageEvent fully handled, next stream_event

                    # --- Convert StreamEvent to legacy chunk dict ---
                    # Processing heartbeat — forward to frontend so it can
                    # show a 'thinking' spinner instead of a stall error.
                    if isinstance(stream_event, ProcessingEvent):
                        yield track_yield({
                            'type': 'processing',
                            'phase': stream_event.phase,
                            'elapsed_seconds': stream_event.elapsed_seconds,
                            'timestamp': f"{int((time.time() - iteration_start_time) * 1000)}ms"
                        })
                        continue

                    # Bridge: translate provider events into the chunk format
                    # the existing orchestration code expects.  This avoids
                    # rewriting 800 lines of battle-tested orchestration logic
                    # in a single pass.  A future cleanup pass can replace
                    # the chunk['type'] dispatch with isinstance() dispatch.
                    if isinstance(stream_event, TextDelta):
                        from app.mcp.response_validator import sanitize_text as _sanitize
                        _clean = _sanitize(stream_event.content) if stream_event.content else ''
                        chunk = {'type': 'content_block_delta',
                                 'delta': {'type': 'text_delta', 'text': _clean}}
                    elif isinstance(stream_event, ToolUseStart):
                        chunk = {'type': 'content_block_start',
                                 'index': stream_event.index,
                                 'content_block': {'type': 'tool_use',
                                                   'id': stream_event.id,
                                                   'name': stream_event.name}}
                    elif isinstance(stream_event, ToolUseInput):
                        chunk = {'type': 'content_block_delta',
                                 'index': stream_event.index,
                                 'delta': {'type': 'input_json_delta',
                                           'partial_json': stream_event.partial_json}}
                    elif isinstance(stream_event, ToolUseEnd):
                        chunk = {'type': 'content_block_stop',
                                 'index': stream_event.index}
                    elif isinstance(stream_event, ThinkingDelta):
                        chunk = {'type': 'content_block_delta',
                                 'delta': {'type': 'thinking_delta',
                                           'thinking': stream_event.content}}
                    elif isinstance(stream_event, ErrorEvent):
                        if stream_event.retryable:
                            # Raise so the outer except handler applies
                            # intelligent throttle backoff and retry logic
                            raise Exception(stream_event.message)
                        else:
                            # Non-retryable (e.g. CONTEXT_LIMIT) — surface to user
                            yield {'type': 'error', 'content': stream_event.message}
                            break
                    elif isinstance(stream_event, StreamEnd):
                        chunk = {'type': 'message_stop',
                                 'stop_reason': stream_event.stop_reason}
                    else:
                        logger.info(
                            f"🔍 UNHANDLED_EVENT: {type(stream_event).__name__} "
                            f"— {str(stream_event)[:150]}"
                        )
                        continue
                    
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
                            # Flush block-opening buffer too — text ending
                            # with backticks can get stuck here indefinitely
                            if hasattr(self, '_block_opening_buffer') and self._block_opening_buffer:
                                assistant_text += self._block_opening_buffer
                                self._update_code_block_tracker(self._block_opening_buffer, code_block_tracker)
                                yield track_yield({
                                    'type': 'text',
                                    'content': self._block_opening_buffer,
                                    'timestamp': f"{int((time.time() - iteration_start_time) * 1000)}ms"
                                })
                                self._block_opening_buffer = ""
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
                            # Process text delta via extracted helper
                            from app.text_delta_processor import process_text_delta

                            # Streaming repetition suppression: if we already
                            # detected a degenerate loop, swallow text silently
                            if self._repetition_suppressed:
                                assistant_text += delta.get('text', '')
                                _td_state.assistant_text = assistant_text
                                continue

                            # Close thinking tag if transitioning from thinking to text
                            if thinking_tag_opened:
                                thinking_tag_opened = False
                                closing = '</thinking-data>'
                                assistant_text += closing
                                yield track_yield({
                                    'type': 'text',
                                    'content': closing,
                                    'timestamp': f"{int((time.time() - iteration_start_time) * 1000)}ms",
                                })
                            text = delta.get('text', '')
                            _td_state.assistant_text = assistant_text
                            _td_events = process_text_delta(self, text, _td_state)
                            for _td_evt in _td_events:
                                yield track_yield(_td_evt)
                            # Sync mutable state back to local vars
                            assistant_text = _td_state.assistant_text
                            viz_buffer = _td_state.viz_buffer
                            in_viz_block = _td_state.in_viz_block
                            if _td_state.hallucination_detected:
                                hallucination_this_iteration = True
                                parrot_match_this_iteration = _td_state.parrot_match
                                break

                            # Check for autoregressive degeneration in real time.
                            # Accumulate text into sentences; if the same sentence
                            # appears 3+ times, suppress further output.
                            self._sentence_buffer += delta.get('text', '')
                            while True:
                                m = re.search(r'([.!?])\s', self._sentence_buffer)
                                if not m:
                                    break
                                end = m.end()
                                sentence = self._sentence_buffer[:end].strip()
                                self._sentence_buffer = self._sentence_buffer[end:]
                                if len(sentence) >= 20:
                                    norm = re.sub(r'\s+', ' ', re.sub(r'[^\w\s]', '', sentence.lower())).strip()
                                    count = sum(1 for s in self._recent_sentences if s == norm)
                                    if count >= 2:
                                        self._repetition_suppressed = True
                                        logger.info(f"✂️ STREAM_REPETITION: Suppressing after 3x: {sentence[:80]}")
                                        break
                                    self._recent_sentences.append(norm)
                                    if len(self._recent_sentences) > 50:
                                        self._recent_sentences = self._recent_sentences[-30:]

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
                        elif delta.get('type') == 'thinking_delta':
                            # Native thinking events (e.g. DeepSeek R1, Claude thinking)
                            # Wrap in <thinking-data> tags for frontend presentation
                            thinking_content = delta.get('thinking', '')
                            if thinking_content:
                                output = ''
                                if not thinking_tag_opened:
                                    thinking_tag_opened = True
                                    output = '<thinking-data>'
                                output += thinking_content
                                assistant_text += output
                                yield track_yield({
                                    'type': 'text',
                                    'content': output,
                                    'timestamp': f"{int((time.time() - iteration_start_time) * 1000)}ms",
                                })

                    elif chunk['type'] == 'content_block_stop':
                        # Find and execute tool
                        tool_id = None
                        for tid, tdata in active_tools.items():
                            if tdata.get('index') == chunk.get('index'):
                                tool_id = tid
                                break
                        
                        if tool_id and tool_id not in completed_tools:
                            tool_data = active_tools[tool_id]

                            # If user feedback was injected during this iteration,
                            # skip remaining tools so the model can respond to it
                            # immediately.  Send a stub result to satisfy the API
                            # contract (every tool_use needs a tool_result).
                            if _feedback_received:
                                skip_msg = "Tool execution skipped: user provided real-time feedback that takes priority. Re-evaluate based on the feedback before continuing."
                                tool_results.append({'tool_id': tool_id, 'tool_name': tool_data['name'], 'result': skip_msg})
                                yield {'type': 'tool_result_for_model', 'tool_use_id': tool_id, 'content': skip_msg}
                                completed_tools.add(tool_id)
                                tools_executed_this_iteration = True
                                logger.info(f"🔄 FEEDBACK_SKIP: Skipping tool {tool_data['name']} ({tool_id}) — user feedback takes priority")
                                continue

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
                                    elif isinstance(tool_input, str):
                                        # Model sent bare string as tool_input.
                                        # Find the tool's single required string param and assign it.
                                        mapped = False
                                        actual = self._normalize_tool_name(tool_name)
                                        for t in all_tools:
                                            t_name = getattr(t, 'name', '')
                                            if t_name in (tool_name, actual):
                                                schema = (t.metadata or {}).get('input_schema', {}) if hasattr(t, 'metadata') else {}
                                                required = schema.get('required', [])
                                                props = schema.get('properties', {})
                                                if len(required) == 1 and props.get(required[0], {}).get('type') == 'string':
                                                    args = {required[0]: tool_input}
                                                    mapped = True
                                                break
                                        if not mapped:
                                            # Fallback: most common single-param name
                                            args = {"command": tool_input}
                                        logger.debug(f"🔍 UNWRAP_TOOL_INPUT: Bare string → {list(args.keys())} for {tool_name}")
                                
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
                                if (not args or len(args) == 0) and actual_tool_name not in optional_only_tools:
                                    logger.debug(f"🔍 EMPTY_ARGS: {tool_name} called with no arguments")
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
                                
                                # Execute tool via extracted helper
                                from app.tool_execution import ToolExecContext, execute_single_tool
                                _exec_ctx = ToolExecContext(
                                    tool_id=tool_id,
                                    tool_name=tool_name,
                                    actual_tool_name=actual_tool_name,
                                    args=args,
                                    all_tools=all_tools,
                                    internal_tool_names=internal_tool_names,
                                    mcp_manager=mcp_manager,
                                    project_root=project_root,
                                    conversation_id=conversation_id,
                                    conversation=conversation,
                                    recent_commands=recent_commands,
                                    inter_tool_delay=inter_tool_delay,
                                    iteration_start_time=iteration_start_time,
                                    track_yield_fn=track_yield,
                                    drain_feedback_fn=_drain_pending_feedback,
                                    executor=self,
                                )
                                logger.debug(f"🔍 EXECUTING_TOOL: {actual_tool_name} with args {args}")
                                async for _evt in execute_single_tool(_exec_ctx):
                                    if _evt.get('type') == '_tool_result':
                                        tool_results.append({
                                            'tool_id': _evt['tool_id'],
                                            'tool_name': _evt['tool_name'],
                                            'result': _evt['result'],
                                        })
                                    else:
                                        yield _evt
                                if _exec_ctx.should_stop_stream:
                                    if _feedback_monitor_task:
                                        _feedback_monitor_task.cancel()
                                    return
                                # Collect any deferred feedback from this tool execution
                                deferred_feedback_messages.extend(_exec_ctx.deferred_feedback)
                                if _exec_ctx.feedback_received:
                                    _feedback_received = True
                                tools_executed_this_iteration = True
                                logger.debug(f"🔍 TOOL_EXECUTED_FLAG: Set tools_executed_this_iteration = True for tool {tool_id}")

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
                        # Delegate to extracted handler (Phase 5d)
                        from app.message_stop_handler import handle_message_stop, MessageStopState
                        _ms_state = MessageStopState(
                            assistant_text=assistant_text,
                            viz_buffer=viz_buffer,
                            content_buffer=content_buffer,
                            thinking_tag_opened=thinking_tag_opened,
                        )
                        async for _ms_evt in handle_message_stop(
                            executor=self,
                            state=_ms_state,
                            chunk=chunk,
                            code_block_tracker=code_block_tracker,
                            conversation=conversation,
                            system_content=system_content,
                            mcp_manager=mcp_manager,
                            iteration_start_time=iteration_start_time,
                            conversation_id=conversation_id,
                            iteration_usage=iteration_usage,
                            iteration=iteration,
                            track_yield=track_yield,
                        ):
                            yield _ms_evt
                        # Sync mutable state back
                        assistant_text = _ms_state.assistant_text
                        last_stop_reason = _ms_state.last_stop_reason
                        continuation_happened = _ms_state.continuation_happened
                        thinking_tag_opened = _ms_state.thinking_tag_opened
                        break

                # MOVED: Log usage metrics AFTER processing all chunks
                # This ensures we have all the data before logging
                if iteration_usage.input_tokens > 0 or iteration_usage.output_tokens > 0:
                    # Update cumulative
                    cumulative_usage.input_tokens += iteration_usage.input_tokens
                    cumulative_usage.output_tokens += iteration_usage.output_tokens
                    cumulative_usage.cache_read_tokens += iteration_usage.cache_read_tokens
                    cumulative_usage.cache_write_tokens += iteration_usage.cache_write_tokens
                    
                    iteration_usages.append(iteration_usage)
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
                    # Build tool_uses list for the provider's message builder
                    tool_uses = []
                    for tool_result in tool_results:
                        tool_args = {}
                        for tool_call in all_tool_calls:
                            if tool_call['id'] == tool_result['tool_id']:
                                tool_args = tool_call.get('args', {})
                                break
                        tool_uses.append({
                            "id": tool_result['tool_id'],
                            "name": tool_result['tool_name'],
                            "input": tool_args
                        })
                    
                    # Sanitize assistant_text before it enters conversation history
                    # to prevent hallucinated tool narratives from poisoning future iterations
                    assistant_text = self._sanitize_assistant_text(assistant_text)

                    conversation.append(self.provider.build_assistant_message(assistant_text, tool_uses))
            
                # Add tool results to conversation BEFORE filtering
                logger.debug(f"🔍 ITERATION_END_CHECK: tools_executed_this_iteration = {tools_executed_this_iteration}, tool_results count = {len(tool_results)}")
                if tools_executed_this_iteration:
                    logger.debug(f"🔍 TOOL_RESULTS_PROCESSING: Adding {len(tool_results)} tool results to conversation")
                    provider_tool_results = []
                    for tool_result in tool_results:
                        raw_result = tool_result['result']
                        # Structured image content (list of content blocks with
                        # base64 images) was already shown to the model in this
                        # iteration via tool_result_for_model.  For conversation
                        # history (sent on every subsequent iteration), replace
                        # with just the text summary to avoid bloating context
                        # with hundreds of KB of base64 per diagram.
                        if isinstance(raw_result, list):
                            text_parts = [
                                b.get('text', '') for b in raw_result
                                if isinstance(b, dict) and b.get('type') == 'text'
                            ]
                            raw_result = ' '.join(text_parts) or '[Image result — content delivered inline above]'
                            logger.info(f"🖼️ CONTEXT_COMPACT: Replaced image content blocks with text summary for conversation history")

                        if isinstance(raw_result, list):
                            pass  # already in correct format for provider
                        elif isinstance(raw_result, str) and '$ ' in raw_result:
                            lines = raw_result.split('\n')
                            clean_lines = [line for line in lines if not line.startswith('$ ')]
                            raw_result = '\n'.join(clean_lines).strip()
                        provider_tool_results.append({
                            "tool_use_id": tool_result['tool_id'],
                            "content": raw_result,
                        })
                    tool_msg = self.provider.build_tool_result_message(provider_tool_results)
                    # OpenAI-format providers return multiple messages (one per tool result)
                    if tool_msg.get("role") == "_multi_tool_results":
                        for sub_msg in tool_msg["results"]:
                            conversation.append(sub_msg)
                    else:
                        conversation.append(tool_msg)
                
                # Inject deferred feedback AFTER assistant message + tool results
                # so it appears at the correct position in conversation history.
                # This feedback was caught during tool execution but deferred to
                # avoid being buried before the assistant message.
                if deferred_feedback_messages:
                    for fb_msg in deferred_feedback_messages:
                        conversation.append({
                            "role": "user",
                            "content": f"[User feedback during tool execution]: {fb_msg}"
                        })
                    deferred_feedback_messages.clear()

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

                # Handle hallucination recovery — retry with corrective feedback
                if hallucination_this_iteration:
                    hallucination_retries += 1
                    if hallucination_retries >= 3:
                        logger.error("🚨 HALLUCINATION: Max retries reached, ending stream")
                        yield track_yield({
                            'type': 'text',
                            'content': '\n\n⚠️ Model repeatedly fabricated tool output after 3 attempts. Ending response.\n\n'
                        })
                        yield {'type': 'stream_end'}
                        break
                    if parrot_match_this_iteration:
                        conversation.append(
                            self._build_parrot_corrective_message(
                                parrot_match_this_iteration
                            )
                        )
                        logger.info(
                            f"🔄 HALLUCINATION_RETRY: Attempt {hallucination_retries}/3, "
                            f"parrot-specific corrective (tool={parrot_match_this_iteration.get('tool_name')}, "
                            f"shingle_overlap={parrot_match_this_iteration.get('shingle_overlap')}, "
                            f"line_matches={parrot_match_this_iteration.get('line_matches')})"
                        )
                    else:
                        conversation.append({
                            "role": "user",
                            "content": "STOP. You just tried to fabricate tool output in your text instead of using the tool calling API. "
                                       "Do NOT generate fake tool results, shell prompts, or simulated command output. "
                                       "If you need to run a command, use the run_shell_command tool properly. "
                                       "Continue your response normally without fabricating any tool output."
                        })
                        conversation.append(
                            self._build_tool_reinforcement_message()
                        )
                        logger.info(
                            f"🔄 HALLUCINATION_RETRY: Attempt {hallucination_retries}/3, "
                            "generic fabrication corrective (no parrot_match)"
                        )
                    continue

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
                    
                    # Drain any feedback that arrived during model streaming or
                    # tool execution in this iteration.  Without this, feedback
                    # sits in _pending_feedback until the next iteration's drain,
                    # and the event-loop starvation bug at iteration boundaries
                    # can cause it to be missed entirely.
                    await asyncio.sleep(0)
                    for fb in _drain_pending_feedback():
                        if fb['type'] == 'interrupt':
                            yield track_yield({'type': 'text', 'content': '\n\n**User requested stop.**\n\n'})
                            yield track_yield({'type': 'stream_end'})
                            if _feedback_monitor_task:
                                _feedback_monitor_task.cancel()
                            return
                        fb_msg = fb.get('message', '')
                        if any(w in fb_msg.lower() for w in ['stop', 'halt', 'abort', 'cancel', 'quit']):
                            yield track_yield({'type': 'text', 'content': f"\n\n**User feedback:** {fb_msg}\n**Stopping execution as requested.**\n\n"})
                            yield track_yield({'type': 'stream_end'})
                            if _feedback_monitor_task:
                                _feedback_monitor_task.cancel()
                            return
                        # Ensure assistant's response is in conversation before
                        # injecting the feedback user message.  The tool-results
                        # path above already appended tool_result messages, but
                        # assistant_text (the model's prose) may not be there yet.
                        if assistant_text.strip() and not any(
                            m.get('role') == 'assistant' and m.get('content') == assistant_text
                            for m in conversation[-3:]
                        ):
                            conversation.append({"role": "assistant", "content": assistant_text})
                        logger.info(f"🔄 FEEDBACK_PRE_CONTINUE: Injecting feedback before next iteration: {fb_msg[:60]}")
                        conversation.append({"role": "user", "content": f"[User feedback]: {fb_msg}"})
                        yield track_yield({'type': 'text', 'content': f"\n\n**📝 Feedback received:** {fb_msg}\n\n"})
                        yield track_yield({'type': 'feedback_delivered', 'message': fb_msg[:80]})

                    logger.debug(f"🔍 CONTINUING_ROUND: Tool results added, model will continue in same stream (round {iteration + 1})")
                    # Yield heartbeat to flush stream before next iteration
                    # Notify frontend that we're waiting for the next model response
                    yield {
                        'type': 'processing_state',
                        'state': 'awaiting_model_response',
                        'iteration': iteration + 1,
                    }
                    yield {'type': 'iteration_continue', 'iteration': iteration + 1}
                    continue  # Immediately start next iteration
                else:
                    # Check for pending feedback without blocking.
                    # The feedback monitor runs continuously so any messages
                    # already received are in _pending_feedback right now.
                    # Yield to event loop first so the monitor can deposit
                    # items that are in the asyncio Queue but haven't been
                    # transferred to _pending_feedback yet.
                    await asyncio.sleep(0)
                    pending_feedback_before_end = [
                        fb.get('message', '') for fb in _drain_pending_feedback()
                        if fb['type'] == 'feedback'
                    ]

                    # Second-chance drain: if feedback was in-flight (e.g. the
                    # monitor was mid-await when we yielded above), wait briefly
                    # and try once more before committing to a break decision.
                    if not pending_feedback_before_end:
                        await asyncio.sleep(0.3)
                        pending_feedback_before_end = [
                            fb.get('message', '') for fb in _drain_pending_feedback()
                            if fb['type'] == 'feedback'
                        ]
                    
                    # If we found pending feedback, deliver it before ending
                    if pending_feedback_before_end:
                        combined_feedback = ' '.join(pending_feedback_before_end)
                        logger.info(f"🔄 PRE-END FEEDBACK: Processing {len(pending_feedback_before_end)} feedback message(s) before stream end")
                        
                        # The model just produced assistant_text in this iteration.
                        # We must add it to the conversation BEFORE the feedback
                        # so the model knows what it said and can respond to the
                        # feedback in context.  Without this, two consecutive user
                        # messages appear and the model loses its own output.
                        if assistant_text.strip():
                            conversation.append({"role": "assistant", "content": assistant_text})

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
                        yield track_yield({
                            'type': 'feedback_delivered',
                            'message': combined_feedback[:80],
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
                            if last_stop_reason == 'max_tokens' and iteration < max_iterations - 1:
                                # Model was cut off mid-response — inject a user
                                # message so the non-prefill model can continue.
                                logger.info(
                                    f"🔄 MAX_TOKENS_CONTINUE: Model hit max_tokens on non-prefill model, "
                                    f"injecting continuation prompt (iteration {iteration})"
                                )
                                conversation.append({
                                    "role": "assistant",
                                    "content": assistant_text
                                })
                                conversation.append({
                                    "role": "user",
                                    "content": "[System: Your previous response was cut off due to length limits. Continue exactly where you left off.]"
                                })
                                continue
                            else:
                                logger.info(
                                    f"🛑 NO_PREFILL_END: Model doesn't support prefill, "
                                    f"ending stream after text-only response (continuation={continuation_happened}, stop_reason={last_stop_reason})"
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
                # This ensures feedback that arrived during the last iteration or after completion
                # is not lost and gives the model a chance to respond
                if conversation_id:
                    # Cancel the monitor FIRST to prevent it from competing
                    # with our direct queue.get() below.  Two consumers on the
                    # same asyncio Queue means ~50% of items go to the wrong
                    # reader, silently dropping feedback.
                    if _feedback_monitor_task:
                        _feedback_monitor_task.cancel()
                        try:
                            await _feedback_monitor_task
                        except asyncio.CancelledError:
                            pass
                        _feedback_monitor_task = None

                    # Also drain anything the monitor deposited before cancellation
                    post_cancel_feedback = [fb.get('message', '') for fb in _drain_pending_feedback() if fb['type'] == 'feedback']

                    try:
                        from app.server import active_feedback_connections
                        if conversation_id in active_feedback_connections:
                            conns = active_feedback_connections[conversation_id]
                            feedback_queue = conns[0]['feedback_queue'] if len(conns) > 0 else None
                            if not feedback_queue:
                                raise asyncio.QueueEmpty()
                            
                            # Grace period: wait up to 500ms for in-flight feedback
                            # that was sent during the final iteration but hasn't
                            # arrived in the queue yet.
                            pending_feedback = []
                            try:
                                feedback_data = await asyncio.wait_for(
                                    feedback_queue.get(), timeout=0.5
                                )
                                while feedback_data:
                                    # Also incorporate anything drained from the monitor above
                                    if post_cancel_feedback:
                                        pending_feedback.extend(post_cancel_feedback)
                                        post_cancel_feedback = []  # Only add once

                                    feedback_type = feedback_data.get('type')
                                    if feedback_type == 'tool_feedback':
                                        pending_feedback.append(feedback_data.get('message', ''))
                                        logger.info(f"🔄 POST-LOOP FEEDBACK: Queued tool_feedback: {feedback_data.get('message', '')[:50]}...")
                                    elif feedback_type == 'interrupt':
                                        logger.info(f"🔄 POST-LOOP FEEDBACK: Received interrupt after tool chain")
                                        yield track_yield({'type': 'text', 'content': '\n\n**User requested stop.**\n\n'})
                                        yield track_yield({'type': 'stream_end'})
                                        return
                                    try:
                                        feedback_data = feedback_queue.get_nowait()
                                    except asyncio.QueueEmpty:
                                        break
                            except asyncio.TimeoutError:
                                # No feedback from queue within grace period.
                                # Still incorporate anything drained from the
                                # monitor before cancellation.
                                if post_cancel_feedback:
                                    pending_feedback.extend(post_cancel_feedback)
                                    post_cancel_feedback = []
                            except (asyncio.QueueEmpty, asyncio.TimeoutError, OSError, KeyError) as queue_error:
                                logger.debug(f"Error draining feedback queue: {queue_error}")
                            
                            # If we have pending feedback, send it to the model
                            if pending_feedback:
                                combined_feedback = ' '.join(pending_feedback)
                                logger.info(f"🔄 POST-LOOP FEEDBACK: Processing {len(pending_feedback)} feedback message(s) after tool chain completion")
                                
                                # Add the model's current response to conversation
                                # before the feedback, so the model retains context
                                # of what it just said.  Without this, feedback is
                                # injected as a consecutive user message.
                                if assistant_text.strip():
                                    conversation.append({"role": "assistant", "content": assistant_text})

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
                                    # Use provider abstraction for feedback response
                                    feedback_config = self._build_provider_config(iteration)
                                    feedback_config.suppress_tools = True
                                    
                                    async for fb_event in self.provider.stream_response(
                                        conversation, system_content, bedrock_tools, feedback_config
                                    ):
                                        if isinstance(fb_event, TextDelta):
                                            yield track_yield({
                                                'type': 'text',
                                                'content': fb_event.content,
                                                'timestamp': f"{int((time.time() - iteration_start_time) * 1000)}ms"
                                            })
                                        elif isinstance(fb_event, StreamEnd):
                                            break

                                    # Signal stream completion after feedback response
                                    yield track_yield({'type': 'stream_end'})
                                    return
                                except (OSError, RuntimeError, asyncio.TimeoutError) as feedback_error:
                                    logger.error(f"Error processing post-loop feedback: {feedback_error}")
                    except (KeyError, OSError, RuntimeError, asyncio.QueueEmpty) as e:
                        logger.debug(f"Error checking post-loop feedback: {e}")
                
                # Clean up iteration resources to prevent memory leaks
                self._cleanup_iteration_resources()

            except Exception as e:  # Intentionally broad: delegates to _classify_and_handle_error()
                # which triages throttling, auth, transient, read-timeout, and generic errors
                error_str = str(e)
                logger.error(f"Error in stream_with_tools iteration {iteration}: {error_str}", exc_info=True)
                
                # Classify error and determine handling strategy
                error_info = self._classify_and_handle_error(
                    e, error_str, iteration, tool_results,
                    throttle_state, inter_tool_delay,
                    iteration_usages, provider_config
                )
                
                if error_info['type'] in ('read_timeout', 'transient') and error_info['should_retry']:
                    logger.warning(f"🔄 {error_info['type'].upper()}_RETRY: "
                                  f"Attempt {throttle_state['retry_count']}/{throttle_state['max_retries']}, "
                                  f"waiting {error_info['delay']}s before retry")
                    
                    yield {
                        'type': 'heartbeat', 'heartbeat': True,
                        'timestamp': f"{int((time.time() - iteration_start_time) * 1000)}ms"
                    }
                    
                    if assistant_text.strip():
                        label = 'Service temporarily unavailable' if error_info['type'] == 'transient' else 'Connection timed out'
                        yield {
                            'type': 'text',
                            'content': f'\n\n⏳ *{label}, retrying...*\n\n',
                            'timestamp': f"{int((time.time() - iteration_start_time) * 1000)}ms"
                        }
                    
                    await asyncio.sleep(error_info['delay'])
                    self._cleanup_iteration_resources()
                    continue  # Retry same iteration
                
                # Non-retryable: yield error chunk and return
                yield error_info['error_chunk']
                if error_info['type'] == 'auth':
                    logger.info(f"🔐 AUTH_ERROR: Yielded authentication error chunk")
                return
        # Stop the feedback monitor
        if _feedback_monitor_task and not _feedback_monitor_task.done():
            _feedback_monitor_task.cancel()
        
        # ------------------------------------------------------------------
        # Autocompaction hook: if this conversation is a delegate, compress
        # the full conversation into a MemoryCrystal.  The crystal is yielded
        # as a 'crystal_ready' event so the caller (DelegateManager / server)
        # can store it and unblock downstream delegates.
        #
        # Only runs for delegate conversations (is_delegate=True).  Runs AFTER the stream is complete but BEFORE the usage report,
        # so the crystal tokens are included in the final accounting.
        # ------------------------------------------------------------------
        if is_delegate and conversation_id and conversation:
            try:
                from app.agents.compaction_engine import get_compaction_engine, MIN_COMPACTION_TOKENS
                engine = get_compaction_engine()
                # Convert conversation to simple dicts for the engine
                msgs_for_compaction = [
                    m if isinstance(m, dict) else {"role": getattr(m, "role", ""), "content": getattr(m, "content", "")}
                    for m in conversation
                ]
                crystal = await engine.compact(msgs_for_compaction, conversation_id, conversation_id)
                if crystal:
                    yield {'type': 'crystal_ready', 'crystal': crystal.model_dump(mode='json')}
                    logger.info(f"💎 Autocompaction complete for {conversation_id}")
            except (ImportError, RuntimeError, ValueError, OSError) as exc:
                logger.warning(f"💎 Autocompaction failed (non-fatal): {exc}")

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
                # Count ALL leading backticks (handles ````, `````, etc.)
                backtick_count = len(stripped) - len(stripped.lstrip('`'))
                # Extract language/type AFTER all leading backticks
                lang_or_type = stripped[backtick_count:].strip()
                
                if lang_or_type:
                    # Has a language specifier - this is ALWAYS an opening, even if we're in a block
                    # This handles cases like: ```mermaid\n...\n```vega-lite (no closing ```)
                    if tracker['in_block']:
                        logger.debug(f"🔍 TRACKER: Implicitly closing {tracker['block_type']} block, opening {lang_or_type} block")
                    tracker['in_block'] = True
                    tracker['block_type'] = lang_or_type
                    tracker['backtick_count'] = backtick_count
                    tracker['accumulated_content'] = line + '\n'
                    logger.debug(f"🔍 TRACKER: Opened {lang_or_type} block ({backtick_count} backticks)")
                elif tracker['in_block']:
                    # No language specifier and we're in a block - closing fence candidate.
                    # CommonMark: closing fence must have >= opening backtick count.
                    if backtick_count < tracker.get('backtick_count', 3):
                        continue  # Not enough backticks to close this block
                    tracker['in_block'] = False
                    tracker['block_type'] = None
                    tracker['backtick_count'] = 0
                    logger.debug(f"🔍 TRACKER: Closed block ({backtick_count} backticks)")
        
        # Log state changes for debugging
        if was_in_block != tracker.get('in_block') or was_block_type != tracker.get('block_type'):
            logger.debug(f"🔍 TRACKER_STATE_CHANGE: {was_block_type or 'none'}[{was_in_block}] → {tracker.get('block_type') or 'none'}[{tracker.get('in_block')}]")
            logger.debug(f"🔍 TRACKER_TEXT: Processing text: {repr(text[:100])}")

    # Pre-compiled pattern for fence normalization.
    # Matches 3+ backticks followed by a language identifier (letter then word chars/hyphens)
    # that are preceded by a non-newline character.
    _FENCE_GLUED_RE = re.compile(r'([^\n])(`{3,}[a-zA-Z][\w-]*)')
    # Matches a fence opening preceded by exactly one newline (needs a second).
    _FENCE_SINGLE_NL_RE = re.compile(r'(?<!\n)\n(`{3,}[a-zA-Z][\w-]*)')

    def _normalize_fence_spacing(self, text: str, code_block_tracker: dict) -> str:
        """Ensure fenced code block openings are preceded by a blank line.

        Markdown renderers require a blank line before a fenced code block
        for it to be recognised as a block-level element.  Models sometimes
        omit this, producing output like::

            The failure spans four layers:```mermaid

        This method normalizes such cases to::

            The failure spans four layers:

            ```mermaid

        Only *opening* fences (those carrying a language tag) are touched.
        Text inside an already-open code block is never modified.
        """
        if '`' not in text or code_block_tracker.get('in_block'):
            return text

        # Case 1: non-newline character directly before fence opening
        normalized = self._FENCE_GLUED_RE.sub(r'\1\n\n\2', text)
        # Case 2: single newline before fence opening (promote to blank line)
        normalized = self._FENCE_SINGLE_NL_RE.sub(r'\n\n\1', normalized)

        if normalized != text:
            logger.debug(f"🔍 FENCE_NORMALIZE: Inserted blank line before code fence")

        return normalized

    async def _continue_incomplete_code_block(
        self,
        conversation: List[Dict[str, Any]], 
        code_block_tracker: Dict[str, Any],
        system_content: Optional[str],
        mcp_manager,
        start_time: float,
        assistant_text: str
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """Continue an incomplete code block by making a new API call."""
        try:
            from app.providers.base import TextDelta, StreamEnd

            block_type = code_block_tracker['block_type']
            fence_width = code_block_tracker.get('backtick_count', 3)
            closing_fence = '`' * fence_width
            # Preserve diff context in continuation prompt
            if block_type == 'diff':
                continuation_prompt = f"Continue the incomplete diff block from where it left off. Maintain all + and - line prefixes. Output ONLY the continuation of the diff content, preserving the exact diff format. Close with {closing_fence}"
            else:
                continuation_prompt = f"Continue the incomplete {block_type} code block from where it left off and close it with {closing_fence}. Output ONLY the continuation of the code block, no explanations."
            
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
                context_size = 60
                last_lines = '\n'.join(lines[-context_size:]) if len(lines) > context_size else '\n'.join(lines)
                continuation_prompt = (
                    f"Your previous response was interrupted and ended with:\n```\n{last_lines}\n```\n\n"
                    f"Continue from where you left off. Do NOT repeat any of the content shown above. "
                    f"Begin your output with the very next line after the content above.\n\n{continuation_prompt}"
                )
            
            continuation_conversation.append({"role": "user", "content": continuation_prompt})
            
            logger.info(f"🔄 CONTINUATION: Making API call to continue {block_type} block")
            
            # Yield initial heartbeat
            yield {
                'type': 'heartbeat',
                'heartbeat': True,
                'timestamp': f"{int((time.time() - start_time) * 1000)}ms"
            }
            
            # Use provider abstraction for continuation
            from app.providers.base import ProviderConfig
            continuation_config = ProviderConfig(
                max_output_tokens=self.model_config.get('max_output_tokens', 2000) if self.model_config else 2000,
                temperature=0.1,
                enable_cache=False,
                suppress_tools=True,
                model_config=self.model_config or {},
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
            
            async for stream_event in self.provider.stream_response(
                continuation_conversation, system_content, [], continuation_config
            ):
                # Send heartbeat every 10 chunks to keep connection alive
                chunk_count += 1
                if chunk_count % 10 == 0:
                    yield {
                        'type': 'heartbeat',
                        'heartbeat': True,
                        'timestamp': f"{int((time.time() - start_time) * 1000)}ms"
                    }
                
                if isinstance(stream_event, StreamEnd):
                    break
                if not isinstance(stream_event, TextDelta):
                    continue

                text = stream_event.content
                if text:
                        
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
        
        except (OSError, RuntimeError, asyncio.TimeoutError, ValueError) as e:
            logger.error(f"🔄 CONTINUATION: Error in continuation: {e}")
