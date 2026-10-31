"""
Fence tracker vs. content-optimizer flush seam (2026-10-17, claude-fable-5-1).

A complete response ending in prose was judged "open fence on clean stop"
(NO_PREFILL_BLOCK_CONTINUE, transitions=1) and the model was asked to
"finish the code block" it had already closed.  Reproduced from the log:

  * Fable streams tiny deltas, so the tracker's last feed before a
    ```mermaid block was "\\n\\n## What " -- it recorded "## What " as an
    unterminated partial line, to be re-read when the rest arrives.
  * "happened\\n\\n" then sat in the content optimizer.  The delta that
    opened the viz block called flush_remaining() and YIELDED the leftover
    as text, but never fed it to _update_code_block_tracker.
  * The viz buffer was then fed whole; the tracker rewound and glued it to
    the stale partial -- "## What ```mermaid" -- which is not a fence.  The
    opener was missed, the closer opened an untyped bare fence, and the
    stream ended with in_block=True.

Invariant under test: every byte the optimizer releases reaches the
tracker, in order, including the two out-of-band flushes (before a viz
block in process_text_delta; before a tool_use block in the executor).
Chunk size 5 reproduces the fault; 13+ did not, which is why it was only
seen on fine-grained streams.
"""
import logging

import pytest

from app.streaming_tool_executor import StreamingToolExecutor
from app.text_delta_processor import TextDeltaState, process_text_delta

BT = "`"
RESPONSE = (
    "I pulled the three diffs and reproduced all three outcomes exactly.\n\n"
    "## What happened\n\n"
    f"{BT*3}mermaid\n"
    "flowchart LR\n"
    f"    M[\"header <code>@@ -63 +63 @@</code>, escaped as <code>\\{BT}</code>\"] --> P[\"patch\"]\n"
    "    P --> G[\"git apply\"]\n"
    f"{BT*3}\n\n"
    f"**1. Count-less headers** ({BT}pipeline_manager.py{BT}). "
    f"**2. {BT}\\{BT}{BT} transport escaping.** Done; it may elsewhere.\n"
)


def _fresh():
    ex = StreamingToolExecutor.__new__(StreamingToolExecutor)
    ex._block_opening_buffer = ""
    tracker = {'in_block': False, 'block_type': None, 'fence_transitions': 0,
               'fence_indent': 0, 'accumulated_content': ''}
    return ex, TextDeltaState(code_block_tracker=tracker), tracker


def _stream(text, n):
    ex, st, tracker = _fresh()
    events = []
    for i in range(0, len(text), n):
        events.extend(process_text_delta(ex, text[i:i + n], st))
    # Drain in the order message_stop_handler does: optimizer, block-opening
    # buffer, viz buffer -- each fed to the tracker before it is emitted.
    rest = ''
    if hasattr(ex, '_content_optimizer'):
        rest += ex._content_optimizer.flush_remaining() or ''
    rest += ex._block_opening_buffer or ''
    rest += st.viz_buffer if st.viz_buffer.strip() else ''
    st.viz_buffer, st.in_viz_block = '', False
    if rest:
        StreamingToolExecutor._update_code_block_tracker(ex, rest, tracker)
    streamed = "".join(e['content'] for e in events if e.get('type') == 'text') + rest
    return tracker, streamed, st


@pytest.mark.parametrize("n", [1, 3, 5, 8, 13, 50, len(RESPONSE)])
def test_closed_viz_block_is_closed_for_the_tracker_at_any_delta_size(n):
    logging.disable(logging.CRITICAL)
    try:
        tracker, streamed, st = _stream(RESPONSE, n)
    finally:
        logging.disable(logging.NOTSET)
    # Positive: the block was seen -- opened AND closed.
    assert tracker['fence_transitions'] == 2, (n, tracker)
    assert tracker['in_block'] is False, (n, tracker)
    assert st.in_viz_block is False
    # Nothing was dropped on the way to the user either.
    assert streamed == RESPONSE


def test_optimizer_flush_before_viz_block_reaches_tracker():
    """The seam itself: the text released by flush_remaining() when a viz
    block opens must be fed to the tracker before the viz buffer is."""
    logging.disable(logging.CRITICAL)
    try:
        ex, st, tracker = _fresh()
        fed = []
        orig = StreamingToolExecutor._update_code_block_tracker

        def spy(self, t, tr):
            fed.append(t)
            return orig(self, t, tr)

        StreamingToolExecutor._update_code_block_tracker = spy
        try:
            for i in range(0, len(RESPONSE), 3):
                process_text_delta(ex, RESPONSE[i:i + 3], st)
        finally:
            StreamingToolExecutor._update_code_block_tracker = orig
    finally:
        logging.disable(logging.NOTSET)
    joined = "".join(fed)
    assert "## What happened\n\n" + BT * 3 + "mermaid" in joined
    # And the opener was recognised as such, not glued to a stale partial.
    assert tracker['fence_transitions'] == 2 and tracker['in_block'] is False
