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


# Stop reasons that indicate the model was genuinely cut off mid-output
# (so an unclosed fence is a real truncation worth continuing). Anything
# else — end_turn, stop, stop_sequence, None — means the model chose to
# stop, in which case an `in_block=True` flag is almost always the fence
# tracker mis-reading backticks quoted in narrative prose, not a real
# open block. Continuing those fabricates user-invisible repeat turns.
_CUTOFF_STOP_REASONS = frozenset({'max_tokens', 'length'})


def should_continue_incomplete_block(in_block: bool, stop_reason) -> bool:
    """Decide whether to auto-continue an apparently-unclosed code block.

    Pure and table-testable (no I/O), mirroring _decide_no_tool_outcome
    and _exceeds_session_ceiling. The continuation loop is only honored
    when BOTH:
      - the fence tracker says we're inside a block, AND
      - the model was actually cut off (stop_reason in _CUTOFF_STOP_REASONS).

    A clean stop (end_turn/stop/None) with in_block=True is treated as a
    tracker false-positive on quoted/inline backticks and is NOT continued.
    This is the stop-reason guard (step 1); the tracker itself is made
    CommonMark-aware separately (step 2) so in_block stops mis-firing at
    the source — after which this guard remains as cheap insurance.
    """
    return bool(in_block) and stop_reason in _CUTOFF_STOP_REASONS


@dataclass
class MessageStopState:
    """Mutable state bag for handle_message_stop.

    The caller sets fields before calling, and reads updated values after
    the async generator is exhausted.
    """
    assistant_text: str = ""
    viz_buffer: str = ""
    content_buffer: str = ""
    thinking_tag_opened: bool = False
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

    # Close unclosed <thinking-data> tag (DeepSeek R1 thinking-only responses).
    if state.thinking_tag_opened:
        state.thinking_tag_opened = False
        closing = '</thinking-data>'
        state.assistant_text += closing
        yield track_yield({
            'type': 'text',
            'content': closing,
            'timestamp': ts(),
        })

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
    # Capped at 2 (was 10).  This loop only fires when the stream ends
    # with an *unclosed* code fence — it re-prompts the model with
    # "close this fence" and counts the retries.  It is NOT the long-
    # output continuation path (that's MAX_CONTINUATIONS in
    # app/agents/streaming_loop.py, unaffected here).  In practice if
    # the model hasn't closed a real fence after 2 attempts, further
    # attempts don't converge — they typically mean the fence tracker
    # mis-read backticks in narrative prose, and each extra loop costs
    # an API call.
    max_continuations = 2
    state.continuation_happened = False

    continuation_marker_id = f"continuation_{time.time_ns()}"

    # Embed the rewind marker before the first continuation so the
    # frontend can locate it when a rewind event references it.
    if should_continue_incomplete_block(code_block_tracker.get('in_block'), state.last_stop_reason):
        yield track_yield({
            'type': 'text',
            'content': f"",
            'timestamp': ts(),
        })

    while (should_continue_incomplete_block(code_block_tracker.get('in_block'), state.last_stop_reason)
           and continuation_count < max_continuations):
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
            # Persist the trim back into state. Without this reassignment the
            # orphaned partial line (e.g. a diff body truncated mid-token like
            # `content = content .. "L`) survives in assistant_text, and the
            # continuation below is concatenated directly onto it via
            # `state.assistant_text += ...`, fusing the dangling text onto the
            # continuation's opening fence. _continue_incomplete_code_block
            # already trims its prefill copy to this same boundary, so the
            # model continues from the clean line — we must match it here.
            state.assistant_text = '\n'.join(assistant_lines)
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
        # No usage is expected when the iteration produced no output
        # (early break, error before message_stop, or the provider
        # simply didn't emit UsageEvent). Only warn when output *was*
        # produced but usage wasn't recorded — a real attribution gap.
        _produced_output = bool(state.assistant_text.strip())
        if _produced_output:
            logger.warning(
                f"⚠️ Output produced but no usage metrics captured "
                f"for iteration {iteration} "
                f"(possible provider UsageEvent gap)"
            )
        else:
            logger.info(f"No usage metrics for iteration {iteration} (no output)")
    elif not conversation_id:
        logger.debug(f"No conversation_id, skipping usage tracking")
