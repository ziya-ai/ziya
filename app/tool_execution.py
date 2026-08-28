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
from app.utils.feedback_directives import is_stop_directive, is_stop_feedback

logger = logging.getLogger(__name__)


class ToolStopRequested(Exception):
    """Raised when a stop directive arrives while a tool is still running.

    Distinct from asyncio.CancelledError so the broad handler in
    execute_single_tool does not report it as a tool failure: nothing failed,
    the user asked to stop.
    """

    def __init__(self, message: str = ""):
        super().__init__(message or "user requested stop")
        self.feedback_message = message


async def _await_tool_result(coro, timeout: float, ctx) -> Any:
    """Await *coro* with a timeout, aborting early on a stop directive.

    Previously a bare ``asyncio.wait_for``, which meant a single tool call was
    a total blind spot: the only feedback checks are before execution and
    after it, so a stop sent 5 seconds into a 5-minute command was not acted
    on until the command finished on its own.

    PEEKS rather than drains.  Draining here would pull non-stop feedback out
    of the shared pending list at a point where it cannot be injected — a
    user text message may not sit between an assistant ``tool_use`` block and
    its matching ``tool_result`` — so it would have to be re-appended,
    scrambling arrival order against newly arriving items.  Leaving it pending
    lets the post-execution drain handle it a moment later with correct
    framing.  Only the stop decision is made here, and that needs no framing.

    Falls back to plain ``wait_for`` when no peek function is supplied, so
    callers that never wired one up are unaffected.
    """
    if getattr(ctx, "peek_feedback_fn", None) is None:
        return await asyncio.wait_for(coro, timeout=timeout)

    from app.utils.feedback_directives import is_stop_feedback

    task = asyncio.ensure_future(coro)
    deadline = time.monotonic() + timeout
    try:
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise asyncio.TimeoutError()
            done, _ = await asyncio.wait({task}, timeout=min(0.3, remaining))
            if task in done:
                return task.result()
            try:
                pending = list(ctx.peek_feedback_fn() or ())
            except Exception as peek_err:  # noqa: BLE001 — never fail a tool on the peek
                logger.debug(f"Feedback peek during tool await failed: {peek_err}")
                continue
            for fb in pending:
                if is_stop_feedback(fb):
                    logger.info(
                        f"🛑 TOOL_STOP_MIDFLIGHT: stop directive during "
                        f"{ctx.actual_tool_name}; abandoning the call"
                    )
                    raise ToolStopRequested(fb.get("message", ""))
    finally:
        if not task.done():
            task.cancel()


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

    # Non-destructive view of the shared pending-feedback list.  Used only to
    # spot a stop directive while a tool is mid-flight; draining there would
    # remove items at a point where they cannot legally be injected into the
    # conversation.  Optional so callers that don't supply it keep the old
    # (blind-during-execution) behaviour rather than breaking.
    peek_feedback_fn: Optional[Callable] = None

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

        # If a task scope is currently active (Task Card with explicit
        # ``paths`` permissions), build the side-channel envelope once
        # and inject it into the tool args.  The shell-server path
        # consumes ``_task_scope`` to apply an additive write grant;
        # builtin tools (e.g. ``file_write``) ignore it and read the
        # ContextVar directly.  Slice B will extend the same envelope
        # with a per-task command allowlist.
        try:
            from app.context import (
                get_task_writable_paths, get_task_readable_paths,
                get_task_shell_commands, get_task_shell_timeout,
            )
            _twp = get_task_writable_paths()
            _trp = get_task_readable_paths()
            _tsc = get_task_shell_commands()
            _tst = get_task_shell_timeout()
        except Exception:
            _twp, _trp, _tsc, _tst = None, None, None, None
        task_scope_payload: Optional[Dict[str, Any]] = None
        if _twp or _trp or _tsc or _tst:
            task_scope_payload = {
                "writable": _twp or [],
                "readable": _trp or [],
                "shell_commands": list(_tsc) if _tsc else [],
                "shell_timeout_secs": _tst,
                "project_root": ctx.project_root or "",
            }
        # A grant must also lift the OUTERMOST guard.  Without this the
        # shell server would honour a 1200 s command while
        # asyncio.wait_for below cancelled the call at 300 s — the
        # build would be killed mid-flight and the failure would
        # surface as an opaque tool timeout rather than as anything the
        # card could act on.  Buffered so the inner layer always fires
        # first and produces the descriptive error.
        if _tst:
            TOOL_EXEC_TIMEOUT = max(TOOL_EXEC_TIMEOUT, int(_tst) + 30)

        if builtin_tool:
            logger.info(f"🔧 Calling builtin tool directly: {ctx.actual_tool_name}")
            # Builtin tools bypass MCPManager.call_tool() entirely (they're
            # invoked directly on tool_instance below), so the anti-loop
            # circuit breakers that call_tool() applies to external tools
            # — the per-turn ceiling and the identical-call blocker — never
            # ran for this path. That let a builtin tool (e.g.
            # context_add_file) retry an unrecoverable error indefinitely
            # within a single turn. Apply the same two guards here.
            if ctx.mcp_manager is not None:
                if ctx.mcp_manager._exceeds_turn_ceiling(ctx.conversation_id):
                    result = {
                        "error": True,
                        "message": (
                            f"Tool call refused: this turn reached the per-turn "
                            f"tool-call ceiling ({ctx.mcp_manager._turn_limit()}). "
                            f"Send a new message to continue, or raise "
                            f"ZIYA_MAX_TOOLS_PER_TURN."
                        ),
                        "code": -32001,
                    }
                elif ctx.mcp_manager._is_repetitive_call(ctx.actual_tool_name, ctx.args, ctx.conversation_id):
                    result = {
                        "error": True,
                        "message": (
                            f"Tool call blocked: {ctx.actual_tool_name} has been "
                            f"called repeatedly with similar arguments. Please try "
                            f"a different approach or check if the previous "
                            f"results contain what you need."
                        ),
                        "code": -32001,
                    }
                else:
                    result = None
            else:
                result = None
            if result is not None:
                # Blocked by a circuit breaker above: skip the actual
                # execute() call, but DO NOT return early here — this
                # function is an async generator that still needs to run
                # the verification/audit-log/yield pipeline below so the
                # model actually receives a tool_result event explaining
                # why the call was refused. Returning early left the
                # generator exhausted with no result ever yielded back.
                if not isinstance(result, dict):
                    result = {"content": [{"type": "text", "text": str(result)}]}
            else:
                if ctx.project_root:
                    ctx.args['_workspace_path'] = ctx.project_root
                if ctx.conversation_id:
                    ctx.args['conversation_id'] = ctx.conversation_id
                if task_scope_payload is not None:
                    ctx.args['_task_scope'] = task_scope_payload
                # Normalize JSON-string object/array args for builtins (they
                # bypass the MCP manager's normalize/coerce path).
                from app.mcp.tools.base import coerce_json_string_args
                ctx.args = coerce_json_string_args(builtin_tool.tool_instance, ctx.args)
                result = await _await_tool_result(
                    builtin_tool.tool_instance.execute(**ctx.args),
                    TOOL_EXEC_TIMEOUT, ctx,
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
            # Propagate the conversation id the same way the builtin branch
            # above does. MCPManager.call_tool reads the conversation from
            # arguments and uses it as the session_id keying workspace-scoped
            # client instances (f"{workspace_path}::{session_id}"). Omitted,
            # session_id is None, the key collapses to the bare workspace
            # path, and every conversation shares ONE shell subprocess whose
            # request loop is serial -- so a slow command in one conversation
            # blocks all others. The manager strips this key before dispatch.
            if ctx.conversation_id:
                ctx.args['conversation_id'] = ctx.conversation_id
            if task_scope_payload is not None:
                ctx.args['_task_scope'] = task_scope_payload
            result = await _await_tool_result(
                ctx.mcp_manager.call_tool(ctx.actual_tool_name, ctx.args, server_name=target_server_name),
                TOOL_EXEC_TIMEOUT, ctx,
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
        # NF-006/008: capture what was co-present in context at decision time
        # (recent tool-result ids + active memory ids).  Returns None unless
        # capture is enabled (env flag or enterprise config policy), so this is
        # a no-op for the community default.
        _ctx_snap = None
        try:
            from app.utils.audit_context import build_context_snapshot
            _ctx_snap = build_context_snapshot(ctx.conversation_id, ctx.conversation)
        except Exception:
            _ctx_snap = None
        log_tool_execution(
            tool_name=ctx.actual_tool_name,
            args={k: v for k, v in ctx.args.items() if not k.startswith('_')},
            result_status="error" if (isinstance(result, dict) and result.get('error')) else "ok",
            conversation_id=ctx.conversation_id or "",
            verified=is_verified,
            error_message=str(result.get('message', ''))[:200] if isinstance(result, dict) and result.get('error') else "",
            duration_ms=_tool_elapsed,
            context_snapshot=_ctx_snap,
        )

        # Track recent commands for deduplication
        if ctx.actual_tool_name == 'run_shell_command' and ctx.args.get('command'):
            ctx.recent_commands.append(ctx.args['command'])
            # Keep only last 20 commands
            del ctx.recent_commands[:-20]

        # --- Process result ---
        result_text = _process_result(result, ctx.tool_name, ctx.actual_tool_name)

        # Fetched PDFs arrive from the external fetch server as a lossy
        # "cannot be simplified to markdown" raw-bytes dump. Re-fetch the
        # URL and run the bytes through the local PDF extractor so the model
        # receives real text instead of mojibake.
        if isinstance(result_text, str):
            result_text = await _maybe_extract_fetched_pdf(result_text, ctx.args)

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
            fb_msg = fb.get('message', '')
            if fb.get('type') == 'interrupt':
                yield ctx.track_yield_fn({'type': 'text', 'content': '\n\n**User requested stop.**\n\n'})
                yield ctx.track_yield_fn({'type': 'stream_end'})
                ctx.should_stop_stream = True
                return
            if is_stop_directive(fb_msg):
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

    except ToolStopRequested as stop_req:
        # A stop arrived while the tool was still running.  Handled before the
        # broad handler below so it is not reported to the model as a tool
        # failure — the call was abandoned deliberately, and telling the model
        # the tool "errored" would prompt a retry of work the user just
        # cancelled.  No _tool_result is emitted: should_stop_stream ends the
        # turn before any assistant/tool_result message is assembled, matching
        # the existing post-execution stop paths above.
        _sm = stop_req.feedback_message
        yield ctx.track_yield_fn({
            'type': 'text',
            'content': (f"\n\n**User feedback:** {_sm}\n**Stopping as requested.**\n\n"
                        if _sm else "\n\n**User requested stop.**\n\n"),
        })
        yield ctx.track_yield_fn({'type': 'stream_end'})
        ctx.should_stop_stream = True
        return
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


# Args keys (across fetch-style tools) that may carry the source URL we
# re-fetch from. Checked in order; first non-empty string wins.
_URL_ARG_KEYS = ('url', 'uri', 'link', 'href', 'source_url')


# SSRF hardening (PenPal #76/#126, CWE-918). This is the one outbound-fetch
# site that fires automatically (not an explicit agent tool call) AND that
# followed redirects — so a spec/doc-injected URL that starts at a public
# host could be 302-bounced to an internal target (IMDS, loopback) after the
# PDF-signature gate had already passed. The finding is blind (the response
# is discarded unless it parses as a PDF) and the report notes the agent's
# by-design fetch/curl tools already reach internal URLs directly, so the
# heavy post-DNS-resolution allowlist is disproportionate here. What IS
# incremental — and cheap to remove — is the silent redirect hop and a
# direct literal-IP internal fetch; both are closed below.
#
# The range list and host check moved to app.utils.net_guard so the remote-MCP
# connect path (ASR EGR-03) enforces the same definition. Re-exported under the
# original private names: they are the documented import surface for
# tests/test_fetched_pdf_ssrf.py.
from app.utils.net_guard import (  # noqa: F401  (re-export)
    BLOCKED_NETWORKS as _SSRF_BLOCKED_NETWORKS,
    host_is_blocked_literal_ip as _url_host_is_blocked_literal_ip,
)


def _extract_url_from_args(args: dict) -> Optional[str]:
    """Return the first URL-like string in args, or None."""
    if not isinstance(args, dict):
        return None
    for key in _URL_ARG_KEYS:
        val = args.get(key)
        if isinstance(val, str) and val.strip().lower().startswith(('http://', 'https://')):
            return val.strip()
    return None


async def _maybe_extract_fetched_pdf(result_text: str, args: dict) -> str:
    """Re-extract a fetched PDF that an upstream fetch tool could not parse.

    Tool-agnostic: any tool whose result carries the unparsed-PDF signature
    (the "cannot be simplified to markdown" + "application/pdf" dump emitted
    by mcp-server-fetch, or a literal %PDF- magic header) and whose args
    carry a re-fetchable URL is recovered. The local document extractor
    (used for file uploads and file_read) otherwise never sees fetched
    content. This detects the signature, re-fetches the URL, and runs the
    bytes through extract_pdf_text_from_bytes.

    Returns the extracted text on success, otherwise the original result_text
    unchanged (non-destructive — any failure falls back to the raw dump).
    """
    head = result_text[:600].lower()
    looks_like_pdf = (
        ('cannot be simplified to markdown' in head and 'application/pdf' in head)
        or '%pdf-' in result_text[:2000]
    )
    if not looks_like_pdf:
        return result_text

    url = _extract_url_from_args(args)
    if not url:
        return result_text

    # Refuse a direct literal-IP fetch to an internal range (IMDS/loopback/
    # RFC-1918) — cheap, no DNS round-trip. See _url_host_is_blocked_literal_ip.
    if _url_host_is_blocked_literal_ip(url):
        logger.warning(f"📄 FETCH_PDF: refusing internal-range URL {url}")
        return result_text

    try:
        import httpx
        # follow_redirects=False: the incremental SSRF risk here is our code
        # silently chasing a 302 to an internal target after the PDF gate
        # passed. A legitimate PDF URL resolves in one hop.
        async with httpx.AsyncClient(follow_redirects=False, timeout=30.0) as client:
            resp = await client.get(
                url, headers={'User-Agent': 'Mozilla/5.0 (compatible; Ziya)'}
            )
            resp.raise_for_status()
            data = resp.content
    except Exception as e:
        logger.warning(f"📄 FETCH_PDF: re-fetch failed for {url}: {e}")
        return result_text

    try:
        from app.utils.document_extractor import extract_pdf_text_from_bytes
        text = await asyncio.to_thread(extract_pdf_text_from_bytes, data)
    except Exception as e:
        logger.warning(f"📄 FETCH_PDF: extraction failed for {url}: {e}")
        return result_text

    if text and text.strip():
        logger.info(f"📄 FETCH_PDF: extracted {len(text)} chars from fetched PDF {url}")
        return f"[PDF text extracted from {url}]\n\n{text}"

    logger.warning(f"📄 FETCH_PDF: no extractable text in fetched PDF {url} (likely scanned)")
    return result_text


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
