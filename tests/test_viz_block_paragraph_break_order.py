"""A blank line before a viz fence must not be replayed after the fence.

Field case (SSE capture, 2026-10-11): the stream carried

    "lives:" | "\\n\\n" | "```mermaid\\n...\\n```\\n\\nGuard" | "ian is not the "

and came out as

    "lives:" "```mermaid...```\\n\\nGuard" "\\n\\nian is not the "

The "\\n\\n" delta sat alone in StreamingContentOptimizer's buffer (too
short to flush).  When the ```mermaid delta arrived, process_text_delta
drained the optimizer with flush_remaining() before buffering the viz block
-- but flush_remaining only returned a buffer with non-whitespace content, so
the paragraph break stayed behind, the whole viz block streamed past it, and
it was finally emitted glued to the front of "ian".  Visible result: the
fence lost its separating blank line and "Guardian" rendered as "Guard

ian".

The seam test drives the real process_text_delta with the exact delta
boundaries from the capture and asserts on what the client receives.
It fails against `if self.buffer.strip():` in flush_remaining.
"""
import os

os.environ.setdefault("ZIYA_TESTING", "1")

from app.streaming_tool_executor import StreamingToolExecutor
from app.text_delta_processor import TextDeltaState, process_text_delta
from app.utils.streaming_optimizer import StreamingContentOptimizer


DELTAS = [
    "lives:",
    "\n\n",
    "```mermaid\nflowchart LR\n  G[\"Cloud Guardian\"] --> B[\"PIT bridge\"]\n```\n\nGuard",
    "ian is not the ",
    "right place.",
]


def _stream(deltas):
    ex = StreamingToolExecutor.__new__(StreamingToolExecutor)
    ex._block_opening_buffer = ""
    ex._content_optimizer = StreamingContentOptimizer()
    ex._fake_tool_buffer = ""
    ex._fake_tool_ticks = 0
    state = TextDeltaState()
    state.native_thinking_seen = True
    emitted = []
    for d in deltas:
        for ev in process_text_delta(ex, d, state):
            if ev.get('type') == 'text':
                emitted.append(ev['content'])
    rest = ex._content_optimizer.flush_remaining()
    if rest:
        emitted.append(rest)
    return emitted, state


def test_paragraph_break_is_emitted_before_the_viz_fence_not_after():
    emitted, state = _stream(DELTAS)
    joined = ''.join(emitted)
    assert joined == ''.join(DELTAS)
    assert state.assistant_text == ''.join(DELTAS)
    # Positive: the blank line precedes the fence in the client stream.
    assert "lives:\n\n```mermaid" in joined
    # Negative: the word is intact and no chunk re-injects the break mid-word.
    assert "Guard\n\nian" not in joined
    assert not any(c.startswith("\n\nian") for c in emitted), emitted


def test_flush_remaining_returns_whitespace_only_buffer():
    """The unit of the fix: a pending whitespace-only buffer is drained,
    not silently kept for the next word."""
    opt = StreamingContentOptimizer()
    list(opt.add_content("\n\n"))
    assert opt.flush_remaining() == "\n\n"
    assert opt.flush_remaining() is None
