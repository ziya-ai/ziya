"""A whitespace-only delta must not be displaced across a viz block.

Field case (conversation d03b1d5b, 2026-10-11): the model streamed
``lives:`` | ``\\n\\n`` | ```` ```mermaid … ``` \\n\\nGuard ```` | ``ian is not the ``
and the user saw ``lives:```mermaid`` with no blank line before the fence
and, after the block, ``Guard\\n\\nian``.

``StreamingContentOptimizer.flush_remaining`` returned None for a buffer
holding only ``\\n\\n`` and left it in place.  The viz path drains the
optimizer with that call before emitting the block from its own buffer, so
the ``\\n\\n`` sat there while the whole mermaid block streamed past it and
was prepended to the next word.  The same drain runs at a tool_use start,
so the displaced whitespace could land after a tool block too.

Driven through the real process_text_delta, normaliser, optimizer and
tracker; assertions are on the emitted chunks (what the frontend renders
and persists), compared with the model's text.
"""
import os

import pytest

os.environ.setdefault("ZIYA_TESTING", "1")

from app.streaming_tool_executor import StreamingToolExecutor
from app.text_delta_processor import TextDeltaState, process_text_delta
from app.utils.streaming_optimizer import StreamingContentOptimizer

# The block spans two deltas, as it did live: the viz buffer opens on the
# first and closes on the second.  (A block that opens AND closes in one
# delta is held until the following delta arrives, which puts the stray
# whitespace after that delta instead -- same displacement, other spot.)
MERMAID_OPEN = "```mermaid\nflowchart LR\n"
MERMAID_CLOSE = "  G[\"Cloud Guardian\"] --> B[\"PIT bridge\"]\n```\n\nGuard"
# The deltas exactly as captured.
DELTAS = ["Where the ", "bug actually ", "lives:", "\n\n", MERMAID_OPEN,
          MERMAID_CLOSE, "ian is not the ", "right place.\n"]
MODEL_TEXT = "".join(DELTAS)


def _drive(deltas):
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
            if ev.get("type") == "text":
                emitted.append(ev["content"])
    rest = ex._content_optimizer.flush_remaining()
    if rest:
        emitted.append(rest)
    return state, emitted


def test_blank_line_before_a_mermaid_block_stays_there_and_the_word_after_it_is_whole():
    state, emitted = _drive(DELTAS)
    assert state.assistant_text == MODEL_TEXT      # accumulation was never wrong
    joined = "".join(emitted)
    assert "Guard\n\nian" not in joined, "paragraph break displaced into the word"
    assert "Guardian is not" in joined
    assert "lives:\n\n```mermaid" in joined
    assert joined == MODEL_TEXT


@pytest.mark.parametrize("delta_size", [1, 3, 7, 16, 40])
def test_streamed_text_is_byte_identical_at_arbitrary_splits(delta_size):
    deltas = [MODEL_TEXT[i:i + delta_size] for i in range(0, len(MODEL_TEXT), delta_size)]
    _, emitted = _drive(deltas)
    assert "".join(emitted) == MODEL_TEXT, f"delta_size={delta_size}"


def test_flush_remaining_returns_whitespace_and_clears_the_buffer():
    opt = StreamingContentOptimizer()
    list(opt.add_content("\n\n"))
    assert opt.flush_remaining() == "\n\n"
    assert opt.buffer == ""
    assert opt.flush_remaining() is None


def test_regression_is_not_vacuous(monkeypatch):
    """Positive control: with the old strip() guard the field shape reproduces."""
    def old_flush(self):
        if self.buffer.strip():
            c, self.buffer = self.buffer, ""
            return c
        return None
    monkeypatch.setattr(StreamingContentOptimizer, "flush_remaining", old_flush)
    _, emitted = _drive(DELTAS)
    joined = "".join(emitted)
    assert "Guard\n\nian" in joined
    assert "lives:```mermaid" in joined
