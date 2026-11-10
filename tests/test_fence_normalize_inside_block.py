"""_normalize_fence_spacing must not rewrite text inside a block opened in the same chunk.

Field case (chat 32dfc463, 2026-11-04): a mermaid sequence diagram had the
message ``M->>M: emits ```diff fences (text)``.  The stored response read
``emits \\n\\n```diff fences (text)`` -- the label split onto its own line,
which mermaid rejects ("Parse error on line 9 ... got 'NEWLINE'").

The normaliser returns early only when the tracker is ALREADY in a block at
the start of the chunk.  When one chunk carried the ```mermaid opener AND,
past a blank line, the label quoting ```diff, it treated the label as a glued
opener: the inline-span guard is paragraph-scoped, so the opener's backtick
run (which had saved the no-blank-line variant by accident) was out of view.
"""
import os

import pytest

os.environ.setdefault("ZIYA_TESTING", "1")

from app.streaming_tool_executor import StreamingToolExecutor
from app.text_delta_processor import TextDeltaState, process_text_delta
from app.utils.streaming_optimizer import StreamingContentOptimizer

FIELD = (
    "I have enough to lay out the design:\n\n"
    "```mermaid\n"
    "sequenceDiagram\n"
    "    participant M as Model\n"
    "    participant UI as Browser\n"
    "\n"
    "    M->>M: emits ```diff fences (text)\n"
    "    UI->>UI: fences close\n"
    "```\n\n"
    "Key facts follow.\n"
)


def _executor():
    ex = StreamingToolExecutor.__new__(StreamingToolExecutor)
    ex._block_opening_buffer = ""
    ex._content_optimizer = StreamingContentOptimizer()
    ex._fake_tool_buffer = ""
    ex._fake_tool_ticks = 0
    return ex


def test_label_quoting_a_fence_inside_same_chunk_block_is_untouched():
    ex = _executor()
    out = ex._normalize_fence_spacing(FIELD, {'in_block': False})
    assert "    M->>M: emits ```diff fences (text)\n" in out
    assert out == FIELD


def test_glued_opener_after_a_closed_block_in_same_chunk_is_still_normalized():
    # Positive: normalisation still runs outside the block, after its close.
    ex = _executor()
    chunk = FIELD + "Then:```python\nx = 1\n"
    out = ex._normalize_fence_spacing(chunk, {'in_block': False})
    assert out.startswith(FIELD)
    assert out.endswith("Then:\n\n```python\nx = 1\n")


def test_glued_opener_then_body_quoting_a_fence():
    # The opener the normaliser itself creates also starts a block.
    ex = _executor()
    chunk = "layers:```mermaid\ngraph TD\n\n    A[\"see ```diff here\"]\n```\n"
    out = ex._normalize_fence_spacing(chunk, {'in_block': False})
    assert out == "layers:\n\n```mermaid\ngraph TD\n\n    A[\"see ```diff here\"]\n```\n"


def test_unclosed_block_in_chunk_leaves_remainder_verbatim():
    ex = _executor()
    chunk = "```mermaid\nsequenceDiagram\n\n    M->>M: emits ```diff x\n"
    assert ex._normalize_fence_spacing(chunk, {'in_block': False}) == chunk


@pytest.mark.parametrize("delta_size", [5, 17, 40, 97, 400])
def test_streamed_field_text_is_byte_identical(delta_size):
    """The seam: raw deltas through process_text_delta reach assistant_text
    unchanged, whatever the chunking."""
    ex = _executor()
    state = TextDeltaState()
    state.native_thinking_seen = True
    for i in range(0, len(FIELD), delta_size):
        process_text_delta(ex, FIELD[i:i + delta_size], state)
    rest = ex._content_optimizer.flush_remaining()
    if rest:
        ex._update_code_block_tracker(rest, state.code_block_tracker)
    assert "emits ```diff fences (text)" in state.assistant_text
    assert state.assistant_text == FIELD
    assert state.code_block_tracker['in_block'] is False


def test_streamed_field_text_at_every_split_point():
    for cut in range(1, len(FIELD)):
        ex = _executor()
        state = TextDeltaState()
        state.native_thinking_seen = True
        for piece in (FIELD[:cut], FIELD[cut:]):
            process_text_delta(ex, piece, state)
        rest = ex._content_optimizer.flush_remaining()
        if rest:
            ex._update_code_block_tracker(rest, state.code_block_tracker)
        assert state.assistant_text == FIELD, f"split at {cut}"
