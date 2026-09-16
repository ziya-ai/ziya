"""
Regression: back-to-back fake (model-fabricated) non-shell tool fences glue
into one malformed fence after passthrough rewriting.

Observed in conversation df488630 turn 47: the model narrated a file_write
and a memory_propose as ````tool:…|…|markdown / ````tool:…|…|text fences.
``_dispatch_fake_tool_block`` takes the passthrough branch for non-shell
fakes and rewrites each to a plain ````<syntax> block.  The closer regex in
``process_text_delta`` consumes the blank line that followed the fence, so
the trailing text begins directly with the next opener — and the rewrite
carried no trailing newline, so the two blocks concatenated into a run of
eight backticks (````````text) that the renderer treats as one unclosed
fence, absorbing the rest of the response.

The fix appends a blank line to the passthrough rewrite.  These tests
exercise the real accumulator path across chunks rather than calling the
dispatcher in isolation, so the seam (closer regex -> trailing -> next
opener -> dispatch) is what's asserted.
"""

import re

from app.text_delta_processor import process_text_delta
from tests.test_text_delta_processor import _make_executor, _make_state


# Bodies are deliberately NOT result-shaped.  The df488630 originals carried
# {'success': True, ...} dicts; those now route to the hallucination path
# (see test_fake_tool_result_fence_gate.py) and never reach passthrough.
# The glue defect is a property of the passthrough rewrite itself, so it is
# exercised here with the bodies that still take that branch: quoted prose.
FAKE_A = (
    "````tool:mcp_file_write|🔐 file write: Docs/x.md|markdown\n"
    "## Section 6.1\n\nGrant, then both sides type freely.\n"
    "````"
)
FAKE_B = (
    "````tool:mcp_memory_propose|🔐 Memory Propose|text\n"
    "Shared-terminal model chosen over a precedence lock.\n"
    "````"
)


def _stream(chunks):
    """Run chunks through process_text_delta; return joined text-event content."""
    executor = _make_executor()
    state = _make_state()
    out = []
    for c in chunks:
        for ev in process_text_delta(executor, c, state):
            if ev.get('type') == 'text':
                out.append(ev['content'])
    return ''.join(out), state


class TestFakeToolPassthroughGlue:

    def test_back_to_back_fakes_do_not_glue_into_one_fence(self):
        # Chunk 1 opens fence A (buffered); chunk 2 delivers the close of A
        # and all of B; chunk 3 closes B.  Mirrors delta boundaries seen in
        # the field: the accumulator only checks for a closer on the chunk
        # AFTER the one that opened the fence.
        joined, _ = _stream([FAKE_A + "\n\n" + FAKE_B, "\n\nAfter.", "\n"])

        # Negative: no run longer than the 4-backtick fence width anywhere.
        assert not re.search(r"`{5,}", joined), joined
        # Positive: both blocks were rewritten (passthrough ran) and are
        # separated by a blank line, i.e. two sibling fences.
        assert "````markdown\n" in joined
        assert "````text\n" in joined
        assert "````\n\n````text" in joined

    def test_single_fake_followed_by_prose_keeps_separation(self):
        joined, _ = _stream([FAKE_A, "\n\nThen I continued.", "\n"])
        # The rewritten fence must be followed by a blank line before prose
        # so the prose is not appended to the closing fence line.
        assert re.search(r"````\n\n+Then I continued\.", joined), joined
        assert not re.search(r"````Then", joined)

    def test_passthrough_does_not_flag_hallucination(self):
        # Documents current behaviour (2c will change this for result-shaped
        # bodies).  Kept so the change in 2c is a deliberate, visible flip.
        _, state = _stream([FAKE_A + "\n\n" + FAKE_B, "\n\nAfter.", "\n"])
        assert state.hallucination_detected is False
