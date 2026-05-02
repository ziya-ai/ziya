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

from app.hallucination import (
    check_for_parroting,
    detect_fake_shell_session,
    scannable_text,
)

logger = logging.getLogger(__name__)

# Patterns indicating hallucinated tool output (checked outside code fences)
_BACKEND_HALLUCINATION_PATTERNS = [
    re.compile(r'SECURITY BLOCK:.{0,200}not allowed', re.DOTALL),
    # Real shell-server denial message starts with bracket commands
    # (sorted alphabetically in get_allowed_commands_description):
    # "Allowed commands: [, [[, ], ]], awk, aws, ...". Prior regex
    # required "awk" immediately after the colon and never matched.
    re.compile(r'Allowed commands:\s*\[, \[\[, \], \]\], awk'),
    # Structural-parrot patterns — the model reproduces Ziya's own UI
    # tool-result format as prose without actually invoking any tool.
    # These signatures are specific to how Ziya renders tool results in
    # the chat UI, so they should never appear in genuine assistant prose.
    # Occasional false-positive on meta-analytical discussion is
    # acceptable; the retry corrective message is the same either way.
    #
    # Match: "file_write|🔐 file write:" / "run_shell_command|🔐 Shell:"
    # and variants. The "|🔐" separator is the strongest signal.
    re.compile(
        r'\b(?:mcp_)?(?:file_write|file_read|file_list|run_shell_command|'
        r'puppeteer_\w+|fetch)\s*\|\s*🔐',
    ),
    # Match: python-dict-shaped fake tool result with the specific keys
    # Ziya's file-tool results use: success/message/path/bytes_written.
    # Requires at least two of these keys co-occurring in a dict literal
    # to avoid flagging legitimate code examples.
    re.compile(
        r"\{\s*'success'\s*:\s*True\s*,\s*'message'\s*:.{0,200}"
        r"'(?:path|bytes_written|error)'\s*:",
        re.DOTALL,
    ),
    # MCP tool-result content-array envelope. Kept in backend-only patterns
    # (fence-protected) so legitimate protocol analysis and code examples
    # quoting this structure do not trigger hallucination detection.
    re.compile(r'"content":\s*\[\s*\{\s*"type":\s*"text",\s*"text":\s*"', re.DOTALL),
]

# Tool-output signatures so unambiguous that fenced context is not an
# excuse — these strings are generated exclusively by Ziya's tool
# plumbing (TOOL_MARKER comments from chatApi.ts / memory_extractor) or
# by the shell server's denial path (tool_execution.py, shell_server.py)
# and should never appear in legitimate assistant output regardless of
# whether they're inside a Markdown fence.
_RAW_HALLUCINATION_PATTERNS = [
    # TOOL_MARKER HTML comments are emitted only by the frontend when
    # rendering a real tool invocation. A model-emitted marker means
    # the model is fabricating a tool-call boundary in its prose.
    re.compile(r'<!--\s*TOOL_MARKER:'),
    # Shell-server denial text, emitted verbatim by
    # tool_execution.py:_process_result when policy_block is set.
    re.compile(r'POLICY BLOCK \(do NOT retry this command\)'),
    # Denial emoji + "BLOCKED:" prefix from shell_server.py line 724.
    re.compile(r'🚫 (?:WRITE )?BLOCKED:'),
]

_CONTAMINATION_RE = re.compile(
    r'(\$ |ERROR:|SECURITY BLOCK|Allowed commands:|```+tool:)'
)


# Block types the frontend renders as visualizations. When the model nests
# one of these inside an outer fence, the outer block swallows it as verbatim
# text and the diagram never renders. We resolve this by injecting a synthetic
# close of the outer block immediately before the inner viz fence, flattening
# the nesting into two sequential sibling blocks.
_VIZ_BLOCK_TYPES = frozenset({
    'mermaid', 'graphviz', 'vega-lite', 'vega', 'drawio',
    'designinspector', 'packet', 'html-mockup', 'plotly',
})

# Matches a named fence opening line (3+ backticks followed by a language tag).
# The colon in tags like "thinking:step-1" is intentionally excluded so those
# outer blocks are not themselves treated as viz targets.
_FENCE_OPEN_RE = re.compile(r'^(`{3,})\s*([a-zA-Z][\w.-]*)(\s*)$')


def _resolve_nested_viz_fence(text: str, tracker: dict) -> str:
    """Flatten a nested viz fence into a sequential sibling block.

    When a model places a renderable fence (mermaid, graphviz, etc.) inside
    an outer fence, the Markdown parser treats everything as verbatim text.
    This is common late in context when the model loses structural awareness.

    Strategy: scan each line of the incoming delta. When a named viz fence
    opener is found while ``tracker['in_block']`` is True, prepend a synthetic
    closing fence (using the outer block's backtick count) and a blank line
    before it. This closes the outer block cleanly so the inner viz fence
    renders as a top-level block.

    The orphaned outer closing fence that arrives later is a bare ````` with no
    matching opener; ``repairUnbalancedFences`` in the frontend ignores it.
    """
    if not tracker.get('in_block'):
        return text

    lines = text.split('\n')
    result = []
    for line in lines:
        m = _FENCE_OPEN_RE.match(line)
        if m and tracker.get('in_block'):
            lang = m.group(2).lower()
            if lang in _VIZ_BLOCK_TYPES:
                outer_backticks = '`' * tracker.get('backtick_count', 3)
                result.append(outer_backticks)  # synthetic close of outer block
                result.append('')               # blank line for Markdown separation
                logger.debug(
                    "🔧 NESTED_VIZ: synthetic close of '%s' before inner '%s'",
                    tracker.get('block_type'), lang,
                )
        result.append(line)
    return '\n'.join(result)


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
    # Session key for shingle index lookups. When None, the shingle
    # check short-circuits (legitimate: no conversation_id means we
    # never registered anything to match against).
    conversation_id: Optional[str] = None

    # Output flags — checked by caller after each call
    hallucination_detected: bool = False
    # Populated when a shingle match fires. Consumed by caller to
    # Position in assistant_text where we last ran the shingle probe.
    # We probe only the new slice since here, preventing cumulative
    # re-scanning from building false overlap against prior tool results.
    last_shingle_probe_pos: int = 0

    # build a targeted corrective message citing the parroted tool.
    parrot_match: Optional[Dict[str, Any]] = None


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

    # --- Resolve nested viz fences ---
    text = _resolve_nested_viz_fence(text, state.code_block_tracker)

    # --- Accumulate ---
    state.assistant_text += text

    # --- Hallucination detection ---
    # Match only against scannable regions of the assistant text: outside
    # Markdown fences, inline backticks, blockquotes, and indented blocks.
    # This prevents false positives when the model legitimately quotes
    # pattern literals while analyzing the detection system itself.
    # First pass: raw-text patterns that are unambiguous tool-output
    # signatures. These fire even inside fences because the model is
    # sometimes observed wrapping fabricated tool output in ```` fences
    # to evade the scannable-region filter.
    _tail_raw = (
        state.assistant_text[-500:]
        if len(state.assistant_text) > 500
        else state.assistant_text
    )
    _match = next(
        (p for p in _RAW_HALLUCINATION_PATTERNS if p.search(_tail_raw)),
        None,
    )
    if _match is None and not state.code_block_tracker.get('in_block'):
        _scan = scannable_text(state.assistant_text)
        _tail = _scan[-500:] if len(_scan) > 500 else _scan
        _match = next(
            (p for p in _BACKEND_HALLUCINATION_PATTERNS if p.search(_tail)),
            None,
        )

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

    # --- Layer A: shingle-index parroting detection ---
    # Checks whether the assistant text is reproducing content from a
    # previously-registered real tool result. Runs at a coarse cadence
    # (roughly every 256 chars of accumulated text) to keep cost bounded
    # on long streams. Session-scoped — no conversation_id means nothing
    # to match against. Probes inside fences too: the model is observed
    # wrapping fabricated tool output in JSON/code fences to bypass the
    # scannable-text filter.
    if state.conversation_id:
        _total = len(state.assistant_text)
        _delta = len(text)
        # Fires when the latest delta carries the cumulative length
        # across a 256-char boundary. This runs roughly once per 256
        # chars without needing additional state.
        if _total >= 256 and (_total // 256) != ((_total - _delta) // 256):
            try:
                if state.code_block_tracker.get('in_block'):
                    _block_type = (
                        state.code_block_tracker.get('block_type') or ''
                    ).lower()
                    if _block_type in ('diff', 'patch'):
                        # Diff/patch context lines reproduce file content
                        # by design — shingle matching against file_read
                        # results would always fire here.
                        _probe = None
                    else:
                        # Other fenced blocks: probe raw tail to catch
                        # fabricated tool output wrapped in a code fence.
                        _probe = state.assistant_text[state.last_shingle_probe_pos:] or None
                else:
                    _scan_full = scannable_text(state.assistant_text)
                    _probe_slice = _scan_full[state.last_shingle_probe_pos:]
                    _probe = _probe_slice if _probe_slice else None
                if _probe is None:
                    _match = None
                else:
                    # Skip fingerprints registered within the current
                    # iteration: the model legitimately summarizes tool
                    # results it just received. Fingerprints from prior
                    # iterations are still checked.
                    _match = check_for_parroting(
                        state.conversation_id,
                        _probe,
                        skip_after_timestamp=state.iteration_start_time or None,
                    )
            except Exception as _e:
                logger.debug(f"🔐 SHINGLE_CHECK: skipped: {_e}")
                _match = None
            # Advance the probe position on a clean pass so we never
            # re-scan text we already checked. On a match we leave the
            # position unchanged — the retry will re-probe the same region.
            if _match is None:
                state.last_shingle_probe_pos = len(state.assistant_text)

            if _match is not None:
                logger.warning(
                    f"🚨 HALLUCINATION_SHINGLE: confidence={_match.confidence} "
                    f"tool={_match.matched_tool_name} "
                    f"tool_use_id={_match.matched_tool_use_id} "
                    f"shingle_overlap={_match.shingle_overlap} "
                    f"line_matches={_match.line_matches}"
                )
                # Only abort on high-confidence matches. Low-confidence
                # matches are logged for observability but allowed to
                # continue — surfacing them early would produce too
                # many false-positive aborts while the thresholds are
                # being tuned against real traffic.
                if _match.confidence == 'high':
                    state.hallucination_detected = True
                    state.parrot_match = {
                        'tool_name': _match.matched_tool_name,
                        'tool_use_id': _match.matched_tool_use_id,
                        'shingle_overlap': _match.shingle_overlap,
                        'line_matches': _match.line_matches,
                    }
                    events.append({
                        'type': 'text',
                        'content': (
                            '\n\n⚠️ Model appears to be reproducing prior '
                            f'`{_match.matched_tool_name}` output rather than '
                            'calling the tool — retrying…\n\n'
                        ),
                    })
                    return events  # caller will break

    # --- Layer B: structural fake-shell-session detection ---
    # Fires when the accumulated text contains a completed code fence
    # whose body looks like real shell output (grep -n numbered lines,
    # or a $ prompt followed by output lines) but no actual tool call
    # produced that output.  Runs at the same 256-char cadence as the
    # shingle check; only completed fences are examined so the check
    # never fires on a fence still being streamed in.
    if state.conversation_id:
        _total = len(state.assistant_text)
        _delta = len(text)
        if _total >= 256 and (_total // 256) != ((_total - _delta) // 256):
            try:
                _shell_match = detect_fake_shell_session(state.assistant_text)
            except Exception as _e:
                logger.debug(f"🔐 FAKE_SHELL_CHECK: skipped: {_e}")
                _shell_match = None

            if _shell_match is not None:
                logger.warning(
                    f"🚨 HALLUCINATION_FAKE_SHELL: signal={_shell_match.signal} "
                    f"reason={_shell_match.reason}"
                )
                state.hallucination_detected = True
                events.append({
                    'type': 'text',
                    'content': (
                        '\n\n⚠️ Model wrote a fabricated shell session rather than '
                        'calling the tool — retrying…\n\n'
                    ),
                })
                return events

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
