"""A diff-body fence split across streaming chunks must not corrupt the response.

Field case (chat be3fe493, 2026-10-09): a ```diff patching a markdown file
carried context lines like " ```bash" / " ```".  Streamed, the response came
out with ONE of those lines rewritten to " \\n\\n```bash" (a column-0 fence
the frontend's diff-scoped fence scanner could not pair), followed by a
spurious unclosed-fence continuation ("The code block is already closed;
nothing remains to emit").  Two mechanisms, both reproduced here:

1. StreamingContentOptimizer re-chunks on WHITESPACE, so " ```bash" reaches
   _update_code_block_tracker as "\\n " then "```bash".  Measured alone, the
   second fragment is a column-0 lang-tagged fence; the diff-body guard
   (indent > 0 == content) misses it, the block is implicitly reopened as
   bash, the next " ```" closes it, and the tracker reads the rest of the
   diff as prose — ending on a phantom open bare fence at the diff's real
   closer, which is what fires the continuation prompt.

2. With the tracker wrongly closed, _normalize_fence_spacing runs on the
   raw deltas and its "glued opener" rule treats the single leading space
   of " ```bash" as a non-newline character before a fence, inserting the
   blank line.  An indented fence line is already a valid fence (or a diff
   context line): nothing is glued.

Every assertion below fails against the pre-fix code.
"""
import os

import pytest

os.environ.setdefault("ZIYA_TESTING", "1")

from app.streaming_tool_executor import StreamingToolExecutor
from app.text_delta_processor import TextDeltaState, process_text_delta
from app.utils.streaming_optimizer import StreamingContentOptimizer


def _executor():
    return StreamingToolExecutor.__new__(StreamingToolExecutor)


def _fresh():
    return {'in_block': False, 'block_type': None, 'accumulated_content': ''}


DIFF_WITH_NESTED_FENCES = (
    "Here is the corrected diff.\n\n"
    "```diff\n"
    "--- a/SKILL.md\n"
    "+++ b/SKILL.md\n"
    "@@ -105,4 +105,4 @@\n"
    " ```bash\n"
    "-aws sts get-caller-identity --profile gsg\n"
    "+aws sts get-caller-identity --profile leo-ro\n"
    " ```\n"
    "@@ -132,4 +132,4 @@\n"
    " Template (prodlab):\n"
    " \n"
    " ```bash\n"
    "-aws kuiperatlas query-telemetry --profile gsg\n"
    "+aws kuiperatlas query-telemetry --profile leo-ro\n"
    " ```\n"
    "```\n\n"
    "One caveat on that diff.\n"
)


# ── 1. tracker: a fence line split inside its leading whitespace ────────────

class TestTrackerChunkBoundary:
    def test_space_then_fence_fragment_is_still_diff_content(self):
        ex = _executor()
        t = _fresh()
        ex._update_code_block_tracker("```diff\n@@ -1 +1 @@\n ", t)
        ex._update_code_block_tracker("```bash\n", t)
        assert (t['in_block'], t['block_type']) == (True, 'diff')

    def test_whole_diff_ends_closed_when_split_at_optimizer_boundaries(self):
        ex = _executor()
        t = _fresh()
        # Exactly the splits StreamingContentOptimizer._flush_complete_words
        # produces: every whitespace run is a boundary.
        import re
        for part in re.split(r'(\s+)', DIFF_WITH_NESTED_FENCES):
            if part:
                ex._update_code_block_tracker(part, t)
        assert (t['in_block'], t['block_type']) == (False, None)

    def test_single_call_tail_still_processed_immediately(self):
        # Callers that hand the tracker a whole text in one call rely on the
        # unterminated last line being read now, not on a later chunk.
        ex = _executor()
        t = _fresh()
        ex._update_code_block_tracker("```python\nx = 1\n```", t)
        assert t['in_block'] is False
        t2 = _fresh()
        ex._update_code_block_tracker('```plotly\n{"data": [1,', t2)
        assert (t2['in_block'], t2['block_type']) == (True, 'plotly')

    def test_partial_opener_completed_by_next_chunk(self):
        ex = _executor()
        t = _fresh()
        ex._update_code_block_tracker("```py", t)
        ex._update_code_block_tracker("thon\nx = 1\n", t)
        assert (t['in_block'], t['block_type']) == (True, 'python')
        ex._update_code_block_tracker("```\n", t)
        assert t['in_block'] is False

    def test_external_reset_between_chunks_is_not_undone(self):
        # The hallucination path force-closes the tracker between deltas; the
        # rewind must not resurrect the state it reset.
        ex = _executor()
        t = _fresh()
        ex._update_code_block_tracker("```python\nx = ", t)
        t['in_block'] = False
        t['block_type'] = None
        ex._update_code_block_tracker("1\n", t)
        assert t['in_block'] is False


# ── 2. normalizer: an indented fence line is not a glued opener ─────────────

class TestNormalizerIndentedFence:
    def test_space_indented_fence_in_chunk_is_untouched(self):
        ex = _executor()
        chunk = " Template (prodlab):\n \n ```bash\n-aws x\n"
        assert ex._normalize_fence_spacing(chunk, {'in_block': False}) == chunk

    def test_indent_carried_from_preceding_text_is_untouched(self):
        ex = _executor()
        chunk = " ```bash\n-aws x\n"
        out = ex._normalize_fence_spacing(chunk, {'in_block': False},
                                          preceding="@@ -1 +1 @@\n")
        assert out == chunk

    def test_genuinely_glued_opener_is_still_normalized(self):
        ex = _executor()
        out = ex._normalize_fence_spacing("four layers:```mermaid\ngraph TD\n",
                                          {'in_block': False})
        assert out == "four layers:\n\n```mermaid\ngraph TD\n"


# ── 2b. normalizer: a blank line already completed by the previous chunk ────

class TestNormalizerSingleNewlineAcrossChunks:
    def test_newline_already_supplied_by_preceding_chunk(self):
        # "file.\n" | "\n```diff" is already a blank line; the (?<!\n)
        # lookbehind at offset 0 cannot see the earlier chunk and used to
        # add a third newline.
        ex = _executor()
        chunk = "\n```diff\n--- a/x\n"
        out = ex._normalize_fence_spacing(chunk, {'in_block': False},
                                          preceding="Here is the file.\n")
        assert out == chunk

    def test_single_newline_split_across_chunks_is_still_promoted(self):
        # "file." | "\n```diff": only one newline in total — promote it.
        ex = _executor()
        out = ex._normalize_fence_spacing("\n```diff\n--- a/x\n", {'in_block': False},
                                          preceding="Here is the file.")
        assert out == "\n\n```diff\n--- a/x\n"

    def test_single_newline_within_chunk_is_still_promoted(self):
        ex = _executor()
        out = ex._normalize_fence_spacing("file.\n```diff\n", {'in_block': False})
        assert out == "file.\n\n```diff\n"


def test_streamed_prose_then_fence_is_byte_identical_at_every_split():
    """The seam: for every split point of a blank-line-separated opener, the
    normaliser must neither lose nor add a newline."""
    text = "Here is the file.\n\n```diff\n--- a/x\n+++ b/x\n```\n"
    for cut in range(1, len(text)):
        ex = _executor()
        ex._block_opening_buffer = ""
        ex._content_optimizer = StreamingContentOptimizer()
        ex._fake_tool_buffer = ""
        ex._fake_tool_ticks = 0
        state = TextDeltaState()
        state.native_thinking_seen = True
        for piece in (text[:cut], text[cut:]):
            process_text_delta(ex, piece, state)
        rest = ex._content_optimizer.flush_remaining()
        if rest:
            ex._update_code_block_tracker(rest, state.code_block_tracker)
        assert state.assistant_text == text, f"split at {cut}"
        assert state.code_block_tracker['in_block'] is False, f"split at {cut}"


# ── 3. the seam: raw deltas → normalizer → optimizer → tracker ──────────────

@pytest.mark.parametrize("delta_size", [7, 13, 23, 40, 64, 97, 150])
def test_streamed_diff_text_is_byte_identical_and_ends_closed(delta_size):
    """Drive process_text_delta with the real normalizer, optimizer and
    tracker.  The accumulated text must equal the model's text, and the
    tracker must read the closed diff as closed."""
    ex = _executor()
    ex._block_opening_buffer = ""
    ex._content_optimizer = StreamingContentOptimizer()
    ex._fake_tool_buffer = ""
    ex._fake_tool_ticks = 0
    state = TextDeltaState()
    state.native_thinking_seen = True  # not under test
    emitted = []
    text = DIFF_WITH_NESTED_FENCES
    for i in range(0, len(text), delta_size):
        for ev in process_text_delta(ex, text[i:i + delta_size], state):
            if ev.get('type') == 'text':
                emitted.append(ev['content'])
    rest = ex._content_optimizer.flush_remaining()
    if rest:
        ex._update_code_block_tracker(rest, state.code_block_tracker)
        emitted.append(rest)
    assert state.assistant_text == text
    assert ''.join(emitted) == text
    assert state.code_block_tracker['in_block'] is False, (
        f"phantom open {state.code_block_tracker.get('block_type')!r} block "
        f"at stream end — this is what fired the spurious continuation")
