"""
Text delta processing for streaming tool executor.

Handles the text_delta event from the provider stream, including:
- Incomplete code fence buffering
- Fake tool-call syntax suppression
- Fence spacing normalization
- Hallucination detection (backend defense-in-depth)
- Visualization block buffering
- Content optimization (anti-mid-word-split)

Extracted in Phase 5c of the refactoring plan.
"""

import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any, AsyncGenerator, Dict, List, Optional

logger = logging.getLogger(__name__)

# Patterns indicating hallucinated tool output (checked outside code fences)
_BACKEND_HALLUCINATION_PATTERNS = [
    re.compile(r'SECURITY BLOCK:.{0,200}not allowed', re.DOTALL),
    re.compile(r'Allowed commands:\s*awk'),
]

_CONTAMINATION_RE = re.compile(
    r'(\$ |ERROR:|SECURITY BLOCK|Allowed commands:|```+tool:)'
)


@dataclass
class TextDeltaState:
    """Mutable state for text delta processing within a single iteration."""

    assistant_text: str = ""
    viz_buffer: str = ""
    in_viz_block: bool = False
    code_block_tracker: dict = field(default_factory=lambda: {
        'in_block': False, 'block_type': None, 'accumulated_content': ''
    })
    iteration_start_time: float = 0.0

    # Output flags — checked by caller after each call
    hallucination_detected: bool = False


def process_text_delta(
    executor: Any,
    text: str,
    state: TextDeltaState,
) -> List[Dict[str, Any]]:
    """Process a single text_delta chunk synchronously.

    Returns a list of event dicts to yield.  The caller is responsible
    for wrapping each with ``track_yield`` and yielding to the stream.

    Mutates ``state`` in place (assistant_text, viz_buffer, etc.).
    If hallucination is detected, sets ``state.hallucination_detected``
    and the caller should ``break`` out of the stream loop.
    """
    events: List[Dict[str, Any]] = []
    ts = f"{int((time.time() - state.iteration_start_time) * 1000)}ms"

    # --- Convert <reasoning> tags to <thinking-data> for presentation ---
    # OpenAI-compatible models (GLM, Qwen, etc.) emit reasoning content
    # inline in <reasoning> tags; map them to the thinking UI.
    text = text.replace('<reasoning>', '<thinking-data>').replace('</reasoning>', '</thinking-data>')

    # --- Fence buffering ---
    if not hasattr(executor, '_block_opening_buffer'):
        executor._block_opening_buffer = ""

    if executor._block_opening_buffer:
        text = executor._block_opening_buffer + text
        executor._block_opening_buffer = ""

    # Buffer incomplete code fence openings (only outside code blocks)
    if not state.code_block_tracker.get('in_block') and (
        text.endswith('```') or (text.endswith('`') and text[-3:] != '```')
    ):
        executor._block_opening_buffer = text
        return events  # skip — buffered
    elif '```' in text:
        lines = text.split('\n')
        last_line = lines[-1]
        if last_line.strip().startswith('```') and not last_line.strip().endswith('```'):
            if state.code_block_tracker.get('in_block'):
                pass  # inside a block — let it through
            elif len(lines) > 1:
                text = '\n'.join(lines[:-1]) + '\n'
                executor._block_opening_buffer = last_line
            else:
                executor._block_opening_buffer = text
                return events  # skip — buffered

    # --- Suppress fake tool-call syntax ---
    if '\x60\x60\x60tool:' in text or '\x60tool:' in text:
        if hasattr(executor, '_content_optimizer'):
            remaining = executor._content_optimizer.flush_remaining()
            if remaining:
                events.append({'type': 'text', 'content': remaining, 'timestamp': ts})
        return events  # skip the fake tool text

    # --- Fence spacing normalization ---
    text = executor._normalize_fence_spacing(text, state.code_block_tracker)

    # --- Accumulate ---
    state.assistant_text += text

    # --- Hallucination detection ---
    _tail = state.assistant_text[-500:] if len(state.assistant_text) > 500 else state.assistant_text
    _match = next(
        (p for p in _BACKEND_HALLUCINATION_PATTERNS if p.search(_tail)), None
    )
    if state.code_block_tracker.get('in_block'):
        _match = None  # legitimate quoting inside code fences

    if _match:
        _pat = getattr(_match, 'pattern', 'fake_tool_call')
        logger.warning(
            f"🚨 HALLUCINATION_BACKEND: Model generating fake tool output! "
            f"Pattern: {_pat}, will retry"
        )
        _last_para = state.assistant_text.rfind('\n\n')
        if _last_para > 0:
            _section = state.assistant_text[_last_para:]
            if _CONTAMINATION_RE.search(_section):
                state.assistant_text = state.assistant_text[:_last_para].rstrip()

        state.code_block_tracker['in_block'] = False
        state.code_block_tracker['depth'] = 0
        state.code_block_tracker['block_type'] = None
        state.hallucination_detected = True

        events.append({
            'type': 'text',
            'content': '\n\n⚠️ Model attempted to fabricate tool output — retrying…\n\n'
        })
        return events  # caller will break

    # --- Content optimizer init ---
    if not hasattr(executor, '_content_optimizer'):
        from app.utils.streaming_optimizer import StreamingContentOptimizer
        executor._content_optimizer = StreamingContentOptimizer()

    # --- Visualization block buffering ---
    viz_patterns = ['\x60\x60\x60vega-lite', '\x60\x60\x60mermaid', '\x60\x60\x60graphviz', '\x60\x60\x60d3']
    has_viz = (
        any(p in text for p in viz_patterns) or
        (state.viz_buffer and any(p in state.viz_buffer + text for p in viz_patterns))
    )

    if has_viz:
        if state.in_viz_block and any(p in text for p in viz_patterns):
            # New viz block — flush previous
            if state.viz_buffer.strip():
                executor._update_code_block_tracker(state.viz_buffer, state.code_block_tracker)
                events.append({'type': 'text', 'content': state.viz_buffer, 'timestamp': ts})
            state.viz_buffer = text
            state.in_viz_block = True
        elif not state.in_viz_block:
            # Flush optimizer before starting viz block
            if hasattr(executor, '_content_optimizer'):
                remaining = executor._content_optimizer.flush_remaining()
                if remaining:
                    events.append({'type': 'text', 'content': remaining, 'timestamp': ts})
            state.in_viz_block = True
            state.viz_buffer = text
        else:
            state.viz_buffer += text
            # Check for closing fence even within the has_viz branch
            has_closing = any(
                line.strip() == '```' for line in state.viz_buffer.split('\n')
                if not any(p.lstrip('`') in line for p in viz_patterns)
            )
            if has_closing:
                executor._update_code_block_tracker(state.viz_buffer, state.code_block_tracker)
                events.append({'type': 'text', 'content': state.viz_buffer, 'timestamp': ts})
                state.viz_buffer = ""
                state.in_viz_block = False
        return events  # continue — viz buffered

    if state.in_viz_block:
        state.viz_buffer += text
        has_closing = any(line.strip() == '```' for line in state.viz_buffer.split('\n'))
        if has_closing:
            executor._update_code_block_tracker(state.viz_buffer, state.code_block_tracker)
            events.append({'type': 'text', 'content': state.viz_buffer, 'timestamp': ts})
            state.viz_buffer = ""
            state.in_viz_block = False
        return events  # continue — viz buffered

    # --- Optimized text output ---
    for chunk in executor._content_optimizer.add_content(text):
        executor._update_code_block_tracker(chunk, state.code_block_tracker)
        events.append({'type': 'text', 'content': chunk, 'timestamp': ts})

    # Force-flush at natural breaks so text is visible before the next tool block
    stripped_tail = state.assistant_text.rstrip()
    if stripped_tail and stripped_tail[-1] in '.!?:\n':
        leftover = executor._content_optimizer.flush_remaining()
        if leftover:
            executor._update_code_block_tracker(leftover, state.code_block_tracker)
            events.append({'type': 'text', 'content': leftover, 'timestamp': ts})

    return events
