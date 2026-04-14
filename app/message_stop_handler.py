"""
Message-stop handler for streaming tool executor.

Handles the message_stop event: flushes all pending buffers (block-opening,
viz, optimizer, content), runs the code-block continuation loop when an
incomplete fenced block is detected, and records iteration usage.

Extracted in Phase 5d of the refactoring plan.
"""

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Any, AsyncGenerator, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class MessageStopState:
    """Mutable state bag for handle_message_stop.

    The caller sets fields before calling, and reads updated values after
    the async generator is exhausted.
    """
    assistant_text: str = ""
    viz_buffer: str = ""
    content_buffer: str = ""
    last_stop_reason: str = "end_turn"
    continuation_happened: bool = False


async def handle_message_stop(
    executor: Any,
    state: MessageStopState,
    chunk: dict,
    code_block_tracker: dict,
    conversation: List[Dict[str, Any]],
    system_content: Any,
    mcp_manager: Any,
    iteration_start_time: float,
    conversation_id: Optional[str],
    iteration_usage: Any,
    iteration: int,
    track_yield: Callable,
) -> AsyncGenerator[Dict[str, Any], None]:
    """Process a message_stop event.

    Yields UI events (text flushes, rewind markers, heartbeats,
    continuation chunks).  Mutates *state* in place so the caller
    can read updated ``assistant_text``, ``last_stop_reason``, and
    ``continuation_happened`` after exhausting this generator.
    """
    ts = lambda: f"{int((time.time() - iteration_start_time) * 1000)}ms"

    # --- 1. Record stop reason ---
    state.last_stop_reason = chunk.get('stop_reason', 'end_turn')

    # --- 2. Flush content optimizer FIRST ---
    # The optimizer may hold earlier content that arrived before the
    # block_opening_buffer captured a backtick-ending chunk.  Flushing
    # the optimizer first preserves chronological ordering.
    if hasattr(executor, '_content_optimizer'):
        remaining = executor._content_optimizer.flush_remaining()
        if remaining:
            executor._update_code_block_tracker(remaining, code_block_tracker)
            yield track_yield({
                'type': 'text',
                'content': remaining,
                'timestamp': ts(),
            })

    # --- 3. Flush block-opening buffer ---
    if hasattr(executor, '_block_opening_buffer') and executor._block_opening_buffer:
        state.assistant_text += executor._block_opening_buffer
        executor._update_code_block_tracker(executor._block_opening_buffer, code_block_tracker)
        yield track_yield({
            'type': 'text',
            'content': executor._block_opening_buffer,
            'timestamp': ts(),
        })
        executor._block_opening_buffer = ""

    # --- 4. Flush viz buffer ---
    if state.viz_buffer.strip():
        executor._update_code_block_tracker(state.viz_buffer, code_block_tracker)
        yield track_yield({
            'type': 'text',
            'content': state.viz_buffer,
            'timestamp': ts(),
        })

    # --- 5. Flush content buffer ---
    if state.content_buffer.strip():
        executor._update_code_block_tracker(state.content_buffer, code_block_tracker)
        yield track_yield({
            'type': 'text',
            'content': state.content_buffer,
            'timestamp': ts(),
        })

    # --- 6. Code-block continuation loop ---
    logger.debug(
        f"🔍 COMPLETION_CHECK: tracker_in_block="
        f"{code_block_tracker.get('in_block', False)}"
    )

    continuation_count = 0
    max_continuations = 10
    state.continuation_happened = False

    continuation_marker_id = f"continuation_{time.time_ns()}"

    # Embed the rewind marker before the first continuation so the
    # frontend can locate it when a rewind event references it.
    if code_block_tracker.get('in_block'):
        yield track_yield({
            'type': 'text',
            'content': f"",
            'timestamp': ts(),
        })

    while code_block_tracker.get('in_block') and continuation_count < max_continuations:
        continuation_count += 1
        block_type = code_block_tracker.get('block_type', 'code')
        logger.info(
            f"🔄 INCOMPLETE_BLOCK: Detected incomplete {block_type} block, "
            f"auto-continuing (attempt {continuation_count})"
        )

        # Rewind to last complete line
        assistant_lines = state.assistant_text.split('\n')
        if assistant_lines and assistant_lines[-1].strip():
            assistant_lines = assistant_lines[:-1]
            logger.info(
                f"🔄 REWIND: Removed incomplete last line, "
                f"rewinding to line {len(assistant_lines)}"
            )

        last_complete_line = len(assistant_lines)

        yield track_yield({'rewind': True, 'to_marker': continuation_marker_id})
        logger.info(f"🔄 YIELDING_REWIND: Rewinding to line {last_complete_line}")

        await asyncio.sleep(0.1)

        yield {
            'type': 'heartbeat',
            'heartbeat': True,
            'timestamp': ts(),
        }

        await asyncio.sleep(0.1)

        continuation_had_content = False
        state.continuation_happened = True
        try:
            async for continuation_chunk in executor._continue_incomplete_code_block(
                conversation, code_block_tracker, system_content,
                mcp_manager, iteration_start_time, state.assistant_text
            ):
                if continuation_chunk.get('content'):
                    continuation_had_content = True
                    logger.info(
                        f"🔄 YIELDING_CONTINUATION: "
                        f"{repr(continuation_chunk.get('content', '')[:50])}"
                    )
                    executor._update_code_block_tracker(
                        continuation_chunk['content'], code_block_tracker
                    )
                    state.assistant_text += continuation_chunk['content']

                    if code_block_tracker['in_block']:
                        continuation_chunk['code_block_continuation'] = True
                        continuation_chunk['block_type'] = code_block_tracker['block_type']

                yield continuation_chunk
        except (OSError, RuntimeError, asyncio.TimeoutError, ValueError) as continuation_error:
            logger.error(f"Continuation failed: {continuation_error}")
            yield {
                'type': 'continuation_failed',
                'reason': str(continuation_error),
                'can_retry': 'ThrottlingException' in str(continuation_error),
                'timestamp': ts(),
            }
            break

        if not continuation_had_content:
            logger.info("🔄 CONTINUATION: No content generated, stopping continuation attempts")
            break

        logger.info(
            f"🔄 CONTINUATION_RESULT: After attempt {continuation_count}, "
            f"in_block={code_block_tracker['in_block']}, "
            f"had_content={continuation_had_content}"
        )

    # --- 7. Record iteration usage ---
    if conversation_id and iteration_usage.input_tokens > 0:
        try:
            from app.streaming_tool_executor import get_global_usage_tracker
            tracker = get_global_usage_tracker()
            tracker.record_usage(conversation_id, iteration_usage)
            logger.debug(
                f"📊 Recorded usage for iteration {iteration}: "
                f"{iteration_usage.input_tokens:,} fresh, "
                f"{iteration_usage.cache_read_tokens:,} cached"
            )
        except (ImportError, KeyError, AttributeError, OSError) as tracking_error:
            logger.error(f"Error recording usage: {tracking_error}")
    elif conversation_id and iteration_usage.input_tokens == 0:
        logger.warning(f"⚠️ No usage metrics captured for iteration {iteration}")
    elif not conversation_id:
        logger.debug(f"No conversation_id, skipping usage tracking")
