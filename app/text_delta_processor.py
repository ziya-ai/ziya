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
    detect_fake_tool_result,
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

# Matches the opening line of a fake tool-call fence as emitted by the model
# mimicking the frontend's tool-display format from chatApi.ts:2243.
# Format: ` `tool:TOOLNAME|HEADER|SYNTAX\n...\n` `
# We capture the backtick run so we can match a same-width closer.
_FAKE_TOOL_OPEN_RE = re.compile(r'(`{3,})tool:[^\s|`]+\|')
# Full block parser used when we have the closing fence in hand.
_FAKE_TOOL_BLOCK_RE = re.compile(
    r'^(`{3,})tool:([^|]+)\|([^|]*)\|([^\n]*)\n+(.*?)\n+(`{3,})\s*$',
    re.DOTALL,
)

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

# Cue phrases that, when they appear in the ~120 chars immediately preceding
# a raw-pattern match, indicate the model is *describing* the pattern rather
# than fabricating tool output.  Examples: "the regex matches <!-- TOOL_MARKER",
# "Pattern: <!-- TOOL_MARKER", "looks for <!-- TOOL_MARKER".  This narrow
# escape hatch lets the model discuss the detection system without the
# raw-pattern check retry-looping.  Fabrications never have these cues — the
# model emits the marker as if it were real output, with no explanatory framing.
_META_DISCUSSION_CUES = re.compile(
    r'(?:'
    r'pattern[s]?\s*[:=]?|regex(?:es)?|matches?\b|match(?:ing|ed)\b|'
    r'marker[s]?|literal|string|comment|expression|signature|'
    r'looks?\s+for|fires?\s+on|detects?|triggers?|catches?|'
    r'discuss(?:ing|es|ed)?|explain(?:ing|s|ed)?|describ(?:ing|es|ed)?|'
    r'mention(?:ing|s|ed)?|quote[ds]?|example[s]?|such\s+as|like'
    r')\b',
    re.IGNORECASE,
)


def _is_meta_discussion(text: str, match_start: int) -> bool:
    """True when the ~120 chars before ``match_start`` look like the model
    is talking *about* the pattern rather than emitting it as tool output.
    """
    window_start = max(0, match_start - 120)
    window = text[window_start:match_start]
    # Inline code span immediately wrapping the marker is also a strong
    # meta-discussion signal — the model is quoting the pattern verbatim.
    if window.endswith('`') or '``' in window[-8:]:
        return True
    return bool(_META_DISCUSSION_CUES.search(window))


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


def _dispatch_fake_tool_block(block_text: str, ts: str) -> List[Dict[str, Any]]:
    """Parse a complete fake tool-call fence and decide execute vs passthrough.

    Returns events.  When the heuristic indicates a real intended tool call,
    emits ``{'type': 'fake_tool_detected', ...}`` for the caller in
    ``streaming_tool_executor`` to dispatch to ``_execute_fake_tool``.
    Otherwise emits a sanitized text passthrough so conversational references
    to tool calls still render as a normal code block.
    """
    events: List[Dict[str, Any]] = []

    m = _FAKE_TOOL_BLOCK_RE.match(block_text)
    if not m:
        logger.warning(
            "🔧 FAKE_TOOL_PARSE_FAIL: could not parse %d-char block; "
            "preview=%r",
            len(block_text), block_text[:200],
        )
        events.append({'type': 'text', 'content': block_text, 'timestamp': ts})
        return events

    open_ticks, tool_name, label, syntax, body, _close_ticks = m.groups()
    body = body.strip('\n')
    syntax = syntax.strip() or 'text'

    # Normalize tool name (matches StreamingToolExecutor._normalize_tool_name)
    normalized = tool_name
    while normalized.startswith('mcp_') or '_mcp_' in normalized:
        normalized = normalized.replace('mcp_', '', 1).lstrip('$_')

    # Strip the frontend's loading sentinel if the model copied it
    body_no_sentinel = '\n'.join(
        ln for ln in body.split('\n') if ln.strip() != '⏳ Running...'
    ).strip('\n')
    nonempty_lines = [ln for ln in body_no_sentinel.split('\n') if ln.strip()]

    is_shell = (normalized == 'run_shell_command')
    has_shell_prompt = bool(nonempty_lines) and nonempty_lines[0].startswith('$ ')

    if is_shell and has_shell_prompt:
        # Real intent: extract everything from the $ line through the end of
        # the pre-output region.  Heuristic: the command may span multiple
        # lines if continued with backslash; output begins after a blank
        # line or an obvious result marker.  Start simple: take the first
        # $-prefixed line stripped of its prompt.
        command = nonempty_lines[0][2:].rstrip()
        logger.info(
            "🔧 FAKE_TOOL_DETECTED: tool=%r normalized=%r command_len=%d "
            "body_lines=%d label=%r syntax=%r",
            tool_name, normalized, len(command),
            len(nonempty_lines), label, syntax,
        )
        events.append({
            'type': 'fake_tool_detected',
            'tool_name': tool_name,
            'normalized_tool_name': normalized,
            'command': command,
            'label': label,
            'syntax': syntax,
            'raw_block': block_text,
            'timestamp': ts,
        })
        return events

    # Heuristic failed — passthrough as plain code block so the user still
    # sees the content but no fake widget is rendered.
    logger.info(
        "🔧 FAKE_TOOL_PASSTHROUGH: tool=%r normalized=%r is_shell=%s "
        "has_prompt=%s body_lines=%d body_preview=%r",
        tool_name, normalized, is_shell, has_shell_prompt,
        len(nonempty_lines), body[:160],
    )
    fence = open_ticks
    rewritten = f"{fence}{syntax}\n{body_no_sentinel}\n{fence}"
    events.append({'type': 'text', 'content': rewritten, 'timestamp': ts})
    return events


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

    # Layer C: per-fence dedup so we don't relog the same fabricated
    # tool-result block on every subsequent chunk.  Keyed by
    # ``(fence_start_offset, fence_lang)``.
    fake_result_logged_keys: set = field(default_factory=set)

    # Layer B suppression: count of fake-tool fences dispatched this
    # iteration.  Each dispatch ran a real tool, so the assistant text
    # legitimately contains shell-output-shaped content (the dispatched
    # command + its returned output).  Layer B's "no shell tool was
    # called" check must not fire on those.
    fake_tool_dispatch_count: int = 0


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

    # --- Fake tool-call fence accumulator ---
    # When the model mimics the frontend's tool-display format (e.g. opens a
    # ``` ``tool:NAME|...|bash`` `` fence), we buffer the entire block until
    # we see the matching closer, then dispatch it to either real execution
    # (via a ``fake_tool_detected`` event) or passthrough rendering.
    # State lives on the executor so it survives across chunks.
    if not hasattr(executor, '_fake_tool_buffer'):
        executor._fake_tool_buffer = ""
        executor._fake_tool_ticks = 0

    if executor._fake_tool_ticks > 0:
        # Already inside a fake tool block — keep eating until closer arrives.
        executor._fake_tool_buffer += text
        close_re = re.compile(
            r'(?:^|\n)' + ('`' * executor._fake_tool_ticks) + r'`*\s*(?:\n|$)'
        )
        m = close_re.search(executor._fake_tool_buffer)
        if not m:
            return events  # still accumulating
        # Found closer — split into block + trailing text.
        block_end = m.end()
        block_text = executor._fake_tool_buffer[:block_end].rstrip()
        trailing = executor._fake_tool_buffer[block_end:]
        executor._fake_tool_buffer = ""
        executor._fake_tool_ticks = 0
        logger.debug(
            "🔧 FAKE_TOOL_CLOSED: block_len=%d trailing_len=%d",
            len(block_text), len(trailing),
        )
        events.extend(_dispatch_fake_tool_block(block_text, ts))
        # Track whether a real dispatch fired.  ``_dispatch_fake_tool_block``
        # emits a ``fake_tool_detected`` event only when the heuristic
        # decides to execute (vs. passthrough as documentation).
        for _ev in events:
            if _ev.get('type') == 'fake_tool_detected':
                state.fake_tool_dispatch_count += 1
        if not trailing.strip():
            return events
        text = trailing  # fall through to process anything after the close

    # Detect a *new* opening fence in this chunk.  We need it to match
    # against the full accumulated text including any block-opening buffer.
    probe = (executor._block_opening_buffer or "") + text
    open_m = _FAKE_TOOL_OPEN_RE.search(probe)
    if open_m:
        ticks = len(open_m.group(1))
        # Drain any normal text that precedes the fake fence so it still
        # streams to the user before we start buffering.
        prefix = probe[:open_m.start()]
        executor._block_opening_buffer = ""
        executor._fake_tool_buffer = probe[open_m.start():]
        executor._fake_tool_ticks = ticks
        logger.info(
            "🔧 FAKE_TOOL_OPENED: ticks=%d prefix_len=%d buffer_preview=%r",
            ticks, len(prefix), executor._fake_tool_buffer[:120],
        )
        # Replace `text` with just the safe prefix so the rest of the
        # pipeline (optimizer, code-block tracker) sees clean content.
        text = prefix
        if not text:
            return events

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
    _match = None
    for _p in _RAW_HALLUCINATION_PATTERNS:
        _m = _p.search(_tail_raw)
        if _m is None:
            continue
        if _is_meta_discussion(_tail_raw, _m.start()):
            logger.info(
                "🔐 HALLUCINATION_BACKEND_SKIP: meta-discussion context "
                "for pattern=%r at offset=%d", _p.pattern, _m.start(),
            )
            continue
        _match = _p
        break
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

        # Yield a structured recovery event rather than an inline text
        # chunk.  The previous text-channel write made the warning part
        # of ``assistant_text``, which then entered conversation history
        # and was fed back to the model on the next turn — the warning
        # itself becoming a contamination vector.  As a structured event
        # it is surfaced by the frontend as a transient banner, never
        # persisted, and never visible to the model on subsequent turns.
        events.append({
            'type': 'hallucination_recovery',
            'reason': 'fabricated_tool_output',
            'pattern': str(_pat)[:120],
            'message': 'Model attempted to fabricate tool output — retrying.',
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
                    # Inside any fenced block: skip shingle probing.
                    # Conversational code fences (plans, snippets,
                    # discussion of code the model has read) routinely
                    # share line-hashes with prior file_read output —
                    # that is legitimate authoring, not parroting.
                    # Fabricated tool output wrapped in a fence is
                    # caught by Layer B (fake shell sessions) and
                    # Layer C (dict/JSON tool-result echoes) when the
                    # fence closes, so disabling the in-fence probe
                    # here does not weaken fake-result detection.
                    _probe = None
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
                # Low-confidence matches are non-actionable (allowed to
                # continue) and fire routinely when the model discusses
                # code it has legitimately read. Emit those at DEBUG to
                # avoid swamping the WARNING channel. High-confidence
                # matches abort the stream and stay at WARNING.
                _log = (
                    logger.warning if _match.confidence == 'high' else logger.debug
                )
                _log(
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
        # Skip Layer B entirely if any fake-tool dispatches fired this
        # iteration.  Each dispatch ran a real tool and its command +
        # output legitimately appears in the assistant text — Layer B's
        # "no shell tool was called" attribution check has no way to
        # distinguish that from a fabricated session, and would
        # false-positive on the dispatched command's own body.
        if state.fake_tool_dispatch_count > 0:
            logger.debug(
                "🔍 LAYER_B_SKIP: %d fake-tool dispatch(es) this iteration "
                "— skipping fake-shell detection",
                state.fake_tool_dispatch_count,
            )
        elif _total >= 256 and (_total // 256) != ((_total - _delta) // 256):
            try:
                _shell_match = detect_fake_shell_session(state.assistant_text)
            except Exception as _e:
                logger.debug(f"🔐 FAKE_SHELL_CHECK: skipped: {_e}")
                _shell_match = None

            if _shell_match is not None:
                # Two-stage gate to drop the false-positive rate.
                #
                # 1. Parroting check (deterministic): probe the fence
                #    body against the shingle index.  Layer A's normal
                #    probe skips inside-fence content because legitimate
                #    code authoring shares vocabulary with prior reads —
                #    but the body of a Layer-B-flagged fence is the one
                #    place where in-fence parroting is exactly the
                #    thing we want to catch.  A match here means the
                #    model is reproducing real prior output instead of
                #    re-running the tool.  Abort regardless of structure.
                #
                # 2. Structure check (heuristic): if no parroting match,
                #    look for `\n\n` paragraph breaks anywhere in the
                #    streamed text.  Real tool output never enters
                #    `state.assistant_text` (it goes through the
                #    `tool_display` channel as an atomic event), so the
                #    only way `\n\n` appears here is if the model wrote
                #    structured prose.  Tutorial / documentation content
                #    has paragraph breaks; dense fabricated tool output
                #    does not.  When breaks are present, suppress the
                #    abort (DEBUG log only).  When breaks are absent —
                #    the entire response is dense, structureless output
                #    — abort: that is the unambiguous fabrication shape.
                _body = _shell_match.fence_body_full or _shell_match.fence_body
                _parroting = None
                try:
                    _parroting = check_for_parroting(
                        state.conversation_id, _body,
                        skip_after_timestamp=state.iteration_start_time or None,
                    )
                except Exception as _e:  # noqa: BLE001
                    logger.debug(f"🔐 FAKE_SHELL_PARROT_PROBE_SKIPPED: {_e}")

                if _parroting is not None:
                    logger.warning(
                        f"🚨 HALLUCINATION_FAKE_SHELL: signal={_shell_match.signal} "
                        f"reason={_shell_match.reason} "
                        f"parroted_tool={_parroting.matched_tool_name} "
                        f"shingle_overlap={_parroting.shingle_overlap} "
                        f"line_matches={_parroting.line_matches}"
                    )
                    state.hallucination_detected = True
                    state.parrot_match = {
                        'tool_name': _parroting.matched_tool_name,
                        'tool_use_id': _parroting.matched_tool_use_id,
                        'shingle_overlap': _parroting.shingle_overlap,
                        'line_matches': _parroting.line_matches,
                    }
                    events.append({
                        'type': 'text',
                        'content': (
                            f'\n\n⚠️ Model reproducing prior '
                            f'`{_parroting.matched_tool_name}` output rather '
                            'than calling the tool — retrying…\n\n'
                        ),
                    })
                    return events
                elif '\n\n' in state.assistant_text:
                    # Structured prose — suppress.  The model is in
                    # "writing markdown" mode, not "pretending to be a
                    # tool" mode.  Log only.
                    logger.debug(
                        f"🔐 FAKE_SHELL_SUPPRESSED_BY_STRUCTURE: "
                        f"signal={_shell_match.signal} "
                        f"reason={_shell_match.reason}"
                    )
                else:
                    # Dense, unstructured response — fire abort.
                    logger.warning(
                        f"🚨 HALLUCINATION_FAKE_SHELL: signal={_shell_match.signal} "
                        f"reason={_shell_match.reason}"
                    )
                    state.hallucination_detected = True
                    events.append({
                        'type': 'text',
                        'content': (
                            '\n\n⚠️ Model wrote a fabricated shell session '
                            'rather than calling the tool — retrying…\n\n'
                        ),
                    })
                    return events

    # --- Layer C: fabricated tool-result echo detection ---
    # Fires when accumulated text contains a closed fenced block whose body
    # opens with a Python-dict / JSON literal whose first key is one of our
    # canonical Ziya tool-result keys (``success``, ``path``, ...).  Walks
    # only closed fences so partially-streamed legitimate code never trips
    # it.  Matches Layer B's cadence and dedup pattern; logs at WARNING
    # without aborting — Layer A handles abort decisions when a real result
    # has been registered.
    if state.conversation_id:
        _total = len(state.assistant_text)
        _delta = len(text)
        if _total >= 256 and (_total // 256) != ((_total - _delta) // 256):
            _accum = state.assistant_text
            _pos = 0
            _fence_open_re = re.compile(r'^(`{3,})([^\n`]*)\n', re.MULTILINE)
            while _pos < len(_accum):
                _om = _fence_open_re.search(_accum, _pos)
                if _om is None:
                    break
                _open_ticks = _om.group(1)
                _lang = (_om.group(2) or '').strip()
                _body_start = _om.end()
                _close_re = re.compile(
                    rf'^`{{{len(_open_ticks)},}}\s*$', re.MULTILINE,
                )
                _cm = _close_re.search(_accum, _body_start)
                if _cm is None:
                    break  # fence still streaming — skip until closed
                _body = _accum[_body_start:_cm.start()]
                _pos = _cm.end()
                _key = (_om.start(), _lang)
                if _key in state.fake_result_logged_keys:
                    continue
                try:
                    _ftr = detect_fake_tool_result(_lang, _body)
                except Exception as _e:  # noqa: BLE001
                    logger.debug(f"🔐 FAKE_RESULT_CHECK: skipped: {_e}")
                    _ftr = None
                if _ftr is not None:
                    state.fake_result_logged_keys.add(_key)
                    logger.warning(
                        "🚨 HALLUCINATION_FAKE_TOOL_RESULT: "
                        "confidence=%s fence_lang=%r matched_keys=%r "
                        "snippet=%r reason=%s",
                        _ftr.confidence, _ftr.fence_lang,
                        _ftr.matched_keys, _ftr.snippet, _ftr.reason,
                    )

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
