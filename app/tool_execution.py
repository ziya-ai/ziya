"""
Single-tool execution logic extracted from StreamingToolExecutor.

This module contains the `execute_single_tool()` async generator that
handles the complete lifecycle of executing one tool call:

1. Emit tool_start / processing_state events
2. Check for pre-execution user feedback
3. Route to builtin (DirectMCPTool) or external (MCP manager)
4. Verify cryptographic signature on the result
5. Audit-log the execution
6. Process and sanitize the result
7. Emit tool_display / tool_result_for_model events
8. Check for post-execution feedback
9. Apply adaptive inter-tool delay

Extracted in Phase 5 of the refactoring plan.
"""

import asyncio
import json
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any, AsyncGenerator, Callable, Dict, List, Optional, Set

logger = logging.getLogger(__name__)


@dataclass
class ToolExecContext:
    """Bundles all state needed to execute a single tool call."""

    # Tool identity
    tool_id: str
    tool_name: str
    actual_tool_name: str
    args: dict

    # Tool registry
    all_tools: list
    internal_tool_names: Set[str]

    # Execution context
    mcp_manager: Any
    project_root: Optional[str]
    conversation_id: Optional[str]

    # Mutable conversation state
    conversation: list
    recent_commands: list
    inter_tool_delay: dict

    # Timing
    iteration_start_time: float

    # Callables from the parent stream_with_tools scope
    track_yield_fn: Callable
    drain_feedback_fn: Callable

    # Reference to the executor for helper methods
    executor: Any = None

    # --- Mutable output flags (written by execute_single_tool) ---
    deferred_feedback: List[str] = field(default_factory=list)
    feedback_received: bool = False
    should_stop_stream: bool = False


async def execute_single_tool(ctx: ToolExecContext) -> AsyncGenerator[Dict[str, Any], None]:
    """Execute a single tool and yield streaming events.

    The caller iterates the returned async generator, forwarding events
    to the client.  After exhaustion, check ``ctx.feedback_received``
    and ``ctx.should_stop_stream`` for control-flow side effects.
    """

    executor = ctx.executor  # StreamingToolExecutor instance

    # --- Notify frontend ---
    yield {
        'type': 'processing_state',
        'state': 'processing_tools',
        'tool_name': ctx.actual_tool_name,
    }
    yield {
        'type': 'tool_start',
        'tool_id': ctx.tool_id,
        'tool_name': ctx.tool_name,
        'display_header': executor._get_tool_header(ctx.tool_name, ctx.args),
        'args': ctx.args,
        'syntax': executor._infer_syntax_hint(ctx.tool_name, ctx.args),
        'timestamp': f"{int((time.time() - ctx.iteration_start_time) * 1000)}ms",
        'is_internal': ctx.actual_tool_name in ctx.internal_tool_names,
    }

    # --- Pre-execution feedback check ---
    skip_due_to_feedback = False
    if ctx.conversation_id:
        # Use the shared drain function instead of reading from the queue
        # directly.  The _feedback_monitor background task is the sole
        # consumer of the asyncio Queue; reading here would race with it
        # and cause ~50% of messages to be silently dropped.
        await asyncio.sleep(0)  # yield to let monitor deposit items
        for fb in ctx.drain_feedback_fn():
            fb_msg = fb.get('message', '')
            if fb['type'] == 'interrupt' or any(w in fb_msg.lower() for w in ['stop', 'halt', 'abort', 'cancel', 'quit']):
                logger.info(f"🔄 FEEDBACK_INTEGRATION: Stop requested: {fb_msg}")
                yield ctx.track_yield_fn({'type': 'text', 'content': f"\n\n**User feedback received:** {fb_msg}\n**Stopping tool execution as requested.**\n\n"})
                await asyncio.sleep(0.1)
                yield ctx.track_yield_fn({'type': 'stream_end'})
                ctx.should_stop_stream = True
                return
            else:
                logger.info(f"🔄 FEEDBACK_INTEGRATION: Adding directive feedback: {fb_msg}")
                # Defer conversation injection — append AFTER assistant msg + tool results
                ctx.deferred_feedback.append(fb_msg)
                yield ctx.track_yield_fn({
                    'type': 'text',
                    'content': f"\n\n**Feedback received:** {fb_msg}\n\n"
                })
                yield ctx.track_yield_fn({
                    'type': 'feedback_delivered',
                    'message': fb_msg[:80],
                })
                ctx.feedback_received = True
                skip_due_to_feedback = True

    if skip_due_to_feedback:
        # Yield a stub tool_result so the API contract is satisfied —
        # every tool_use in the assistant message needs a corresponding
        # tool_result in the user message.
        skip_msg = "Tool execution skipped: user provided real-time feedback that takes priority."
        yield {
            'type': '_tool_result',
            'tool_id': ctx.tool_id,
            'tool_name': ctx.tool_name,
            'result': skip_msg,
        }
        return

    # --- Execute the tool ---
    try:
        TOOL_EXEC_TIMEOUT = int(os.environ.get('TOOL_EXEC_TIMEOUT', '300'))

        from app.utils.tool_audit_log import log_tool_execution
        from app.mcp.signing import verify_tool_result, strip_signature_metadata, sign_tool_result
        from app.mcp.enhanced_tools import DirectMCPTool

        _tool_start_time = time.time()

        # Resolve builtin vs external
        builtin_tool = None
        if ctx.all_tools:
            for tool in ctx.all_tools:
                if isinstance(tool, DirectMCPTool) and tool.name == ctx.actual_tool_name:
                    builtin_tool = tool
                    logger.info(f"🔧 BUILTIN_FOUND: Found builtin tool {ctx.actual_tool_name}")
                    break

        if builtin_tool:
            logger.info(f"🔧 Calling builtin tool directly: {ctx.actual_tool_name}")
            if ctx.project_root:
                ctx.args['_workspace_path'] = ctx.project_root
            result = await asyncio.wait_for(
                builtin_tool.tool_instance.execute(**ctx.args),
                timeout=TOOL_EXEC_TIMEOUT,
            )
            # Sign builtin results (external tools are signed in MCPClient)
            if result and not isinstance(result, dict):
                result = {"content": [{"type": "text", "text": str(result)}]}
            if result and isinstance(result, dict) and not result.get("error"):
                cid = ctx.args.get('conversation_id', 'default')
                result = sign_tool_result(ctx.actual_tool_name, ctx.args, result, cid)
                logger.debug(f"🔐 Signed builtin tool result for {ctx.actual_tool_name}")
        else:
            # Route through MCP manager
            target_server_name = None
            for tool in ctx.all_tools:
                t_name = getattr(tool, 'name', '')
                if t_name in (ctx.actual_tool_name, f"mcp_{ctx.actual_tool_name}"):
                    if hasattr(tool, 'metadata') and tool.metadata:
                        target_server_name = tool.metadata.get('server_name')
                        if target_server_name:
                            logger.debug(f"🔍 ROUTING: Tool {ctx.actual_tool_name} → server '{target_server_name}'")
                            break

            if not target_server_name:
                logger.warning(f"🔍 ROUTING: Could not determine server for {ctx.actual_tool_name}")

            if ctx.project_root:
                ctx.args['_workspace_path'] = ctx.project_root
            result = await asyncio.wait_for(
                ctx.mcp_manager.call_tool(ctx.actual_tool_name, ctx.args, server_name=target_server_name),
                timeout=TOOL_EXEC_TIMEOUT,
            )

        # --- Signature verification ---
        is_verified = False
        verification_error = None

        if result and isinstance(result, dict) and not result.get("error"):
            is_valid, error_message = verify_tool_result(result, ctx.actual_tool_name, ctx.args)
            if not is_valid:
                logger.error(f"🔐 SECURITY: Verification failed for {ctx.actual_tool_name}: {error_message}")
                from app.server import record_verification_result
                record_verification_result(ctx.actual_tool_name, False, error_message)
                result = {
                    "error": True,
                    "message": f"🚨 TOOL CALL REJECTED - SECURITY VERIFICATION FAILED\n\n"
                               f"Tool: {ctx.actual_tool_name}\n"
                               f"Reason: {error_message}\n\n"
                               "This tool call did not execute successfully. "
                               "The result could not be cryptographically verified.\n\n"
                               "DO NOT proceed as if this tool executed.\n"
                               "DO NOT use or reference results from this tool call.\n\n"
                               "Please try again or proceed without this tool."
                }
            else:
                is_verified = True
                from app.server import record_verification_result
                record_verification_result(ctx.actual_tool_name, True)
                logger.debug(f"🔐 Verified tool result for {ctx.actual_tool_name}")
                result = strip_signature_metadata(result)

        # --- Audit log ---
        _tool_elapsed = (time.time() - _tool_start_time) * 1000
        log_tool_execution(
            tool_name=ctx.actual_tool_name,
            args={k: v for k, v in ctx.args.items() if not k.startswith('_')},
            result_status="error" if (isinstance(result, dict) and result.get('error')) else "ok",
            conversation_id=ctx.conversation_id or "",
            verified=is_verified,
            error_message=str(result.get('message', ''))[:200] if isinstance(result, dict) and result.get('error') else "",
            duration_ms=_tool_elapsed,
        )

        # Track recent commands for deduplication
        if ctx.actual_tool_name == 'run_shell_command' and ctx.args.get('command'):
            ctx.recent_commands.append(ctx.args['command'])
            # Keep only last 20 commands
            del ctx.recent_commands[:-20]

        # --- Process result ---
        result_text = _process_result(result, ctx.tool_name, ctx.actual_tool_name)

        # Sanitize text results for context efficiency
        if isinstance(result_text, str):
            from app.utils.tool_result_sanitizer import sanitize_for_context
            result_text = sanitize_for_context(result_text, tool_name=ctx.actual_tool_name, args=ctx.args)

        yield {
            'type': '_tool_result',
            'tool_id': ctx.tool_id,
            'tool_name': ctx.tool_name,
            'result': result_text,
        }

        # --- Display to user (only if verified or no verification error) ---
        should_display = is_verified or (not verification_error)
        if should_display:
            _image_data_uri = None
            if isinstance(result_text, list):
                _display_parts = [b.get('text', '') for b in result_text if b.get('type') == 'text']
                _display_str = ' '.join(_display_parts) or f"[Image from {ctx.tool_name}]"
                for _block in result_text:
                    if isinstance(_block, dict) and _block.get('type') == 'image':
                        _src = _block.get('source', {})
                        if _src.get('type') == 'base64' and _src.get('data'):
                            _media = _src.get('media_type', 'image/png')
                            _image_data_uri = f"data:{_media};base64,{_src['data']}"
                            break
            else:
                _display_str = result_text

            yield {
                'type': 'tool_display',
                'tool_id': ctx.tool_id,
                'tool_name': ctx.tool_name,
                'result': executor._format_tool_result(ctx.tool_name, _display_str, ctx.args),
                'args': ctx.args,
                'syntax': executor._infer_syntax_hint(ctx.tool_name, ctx.args),
                'verified': is_verified,
                'verification_error': verification_error,
                'timestamp': f"{int((time.time() - ctx.iteration_start_time) * 1000)}ms",
                'is_internal': ctx.actual_tool_name in ctx.internal_tool_names,
                **({'image_data': _image_data_uri} if isinstance(result_text, list) and _image_data_uri else {}),
            }
        else:
            logger.warning(f"🔐 SECURITY: Suppressed unverified result from display: {ctx.actual_tool_name}")

        # --- Register result fingerprint for hallucination detection ---
        # Only fingerprint verified, substantive results. Server-constructed
        # error/blocked messages are skipped so the model can legitimately
        # echo phrases like "please try a different approach" without being
        # flagged as parroting tool output.
        if is_verified and ctx.conversation_id:
            try:
                if isinstance(result_text, list):
                    _fp_text = '\n'.join(
                        b.get('text', '') for b in result_text
                        if isinstance(b, dict) and b.get('type') == 'text'
                    )
                elif isinstance(result_text, str):
                    _fp_text = result_text
                else:
                    _fp_text = ''
                if _fp_text and not _fp_text.startswith(('ERROR:', 'BLOCKED:')):
                    from app.hallucination import register_tool_result
                    register_tool_result(
                        conversation_id=ctx.conversation_id,
                        tool_use_id=ctx.tool_id,
                        tool_name=ctx.actual_tool_name,
                        result_text=_fp_text,
                    )
            except Exception as _e:
                logger.debug(f"🔐 SHINGLE_INDEX: registration skipped: {_e}")

        # Send result to model
        yield {
            'type': 'tool_result_for_model',
            'tool_use_id': ctx.tool_id,
            'content': result_text,
        }

        # --- Adaptive inter-tool delay ---
        delay = ctx.inter_tool_delay['current']
        await asyncio.sleep(delay)
        ctx.inter_tool_delay['current'] = max(
            ctx.inter_tool_delay['min'],
            ctx.inter_tool_delay['current'] * ctx.inter_tool_delay['decay_factor'],
        )

        # --- Post-execution feedback drain ---
        await asyncio.sleep(0)
        for fb in ctx.drain_feedback_fn():
            if fb['type'] == 'interrupt':
                yield ctx.track_yield_fn({'type': 'text', 'content': '\n\n**User requested stop.**\n\n'})
                yield ctx.track_yield_fn({'type': 'stream_end'})
                ctx.should_stop_stream = True
                return
            fb_msg = fb.get('message', '')
            if any(w in fb_msg.lower() for w in ['stop', 'halt', 'abort', 'cancel', 'quit']):
                yield ctx.track_yield_fn({'type': 'text', 'content': f"\n\n**User feedback:** {fb_msg}\n**Stopping execution as requested.**\n\n"})
                yield ctx.track_yield_fn({'type': 'stream_end'})
                ctx.should_stop_stream = True
                return
            logger.info(f"🔄 FEEDBACK_POST_TOOL: Injecting feedback: {fb_msg[:60]}")
            # Defer conversation injection — append AFTER assistant msg + tool results
            ctx.deferred_feedback.append(fb_msg)
            yield ctx.track_yield_fn({'type': 'text', 'content': f"\n\n**📝 Feedback received:** {fb_msg}\n\n"})
            yield ctx.track_yield_fn({
                'type': 'feedback_delivered',
                'message': fb_msg[:80],
            })
            ctx.feedback_received = True

    except asyncio.TimeoutError:
        TOOL_EXEC_TIMEOUT = int(os.environ.get('TOOL_EXEC_TIMEOUT', '300'))
        error_msg = f"Tool '{ctx.actual_tool_name}' timed out after {TOOL_EXEC_TIMEOUT}s. The tool may be unresponsive."
        logger.error(f"⏰ TOOL_TIMEOUT: {ctx.actual_tool_name} exceeded {TOOL_EXEC_TIMEOUT}s")
        logger.error(f"🔍 TOOL_EXECUTION_ERROR: {error_msg}")
        yield {
            'type': '_tool_result',
            'tool_id': ctx.tool_id,
            'tool_name': ctx.tool_name,
            'result': f"ERROR: {error_msg}. Please try a different approach or fix the command.",
        }
        yield {'type': 'tool_display', 'tool_name': ctx.tool_name, 'result': f"ERROR: {error_msg}"}
        yield {
            'type': 'tool_result_for_model',
            'tool_use_id': ctx.tool_id,
            'content': f"ERROR: {error_msg}. Please try a different approach or fix the command.",
        }

    except Exception as e:  # Intentionally broad: MCP tools are third-party code
        if 'cannot schedule new futures after shutdown' in str(e):
            error_msg = "Tool execution interrupted (server shutting down)"
        else:
            error_msg = f"Tool error: {str(e)}"

        logger.error(f"🔍 TOOL_EXECUTION_ERROR: {error_msg}")

        yield {
            'type': '_tool_result',
            'tool_id': ctx.tool_id,
            'tool_name': ctx.tool_name,
            'result': f"ERROR: {error_msg}. Please try a different approach or fix the command.",
        }
        yield {'type': 'tool_display', 'tool_name': ctx.tool_name, 'result': f"ERROR: {error_msg}"}
        yield {
            'type': 'tool_result_for_model',
            'tool_use_id': ctx.tool_id,
            'content': f"ERROR: {error_msg}. Please try a different approach or fix the command.",
        }


def _process_result(result: Any, tool_name: str, actual_tool_name: str) -> Any:
    """Convert a raw tool result into the text/structured form for the model."""
    if isinstance(result, dict) and result.get('error') and result.get('error') is not False:
        error_msg = result.get('message', 'Unknown error')
        if 'SECURITY VERIFICATION FAILED' in error_msg:
            return error_msg
        elif 'repetitive execution' in error_msg:
            return f"BLOCKED: {error_msg} Previous attempts may have succeeded - check the results above before retrying."
        elif result.get('policy_block') or '🚫 BLOCKED' in error_msg or '🚫 WRITE BLOCKED' in error_msg:
            return (f"POLICY BLOCK (do NOT retry this command): {error_msg}\n"
                    "This command is blocked by shell security policy. "
                    "Use a different approach or an allowed command.")
        elif 'non-zero exit status' in error_msg:
            return f"COMMAND FAILED: {error_msg}. The external tool encountered an error."
        elif 'Content truncated' in error_msg:
            return f"PARTIAL RESULT: {error_msg}. Use start_index parameter to get more content."
        elif 'validation error' in error_msg.lower():
            return f"PARAMETER ERROR: {error_msg}. Check the tool's parameter requirements."
        else:
            return f"ERROR: {error_msg}. Please try a different approach or fix the command."

    elif isinstance(result, dict) and 'content' in result:
        content = result['content']

        # file_read / file_write / file_list pattern: the tool returns
        # {content: "file text", metadata: "N lines", path: "a/b.py"}.
        # Return just the file text — path and metadata are already in
        # the display header that the frontend renders above the body.
        # Serialising the whole wrapper via json.dumps() produced a giant
        # JSON string that downstream processing (sanitiser, MCP content-
        # block wrapping) frequently corrupted, causing the frontend to
        # display the raw Python dict repr instead of highlighted code.
        if isinstance(content, str) and 'path' in result:
            return content

        _has_image = isinstance(content, list) and any(
            isinstance(b, dict) and b.get('type') == 'image' for b in content)
        if _has_image:
            text_parts = [b.get('text', '') for b in content if b.get('type') == 'text']
            logger.info(f"🖼️ TOOL_IMAGE_RESULT: Preserving image content blocks for {tool_name}")
            return content  # keep as structured list
        elif isinstance(content, list) and len(content) > 0:
            return content[0].get('text', str(result))
        elif isinstance(content, str):
            # Structured dict with string content (e.g. file_read returns
            # {content, metadata, path}).  JSON-serialize so the frontend
            # can parse all fields; str() would produce Python repr with
            # single quotes that JSON.parse rejects.
            return json.dumps(result)
        else:
            return str(result)

    else:
        return str(result)
