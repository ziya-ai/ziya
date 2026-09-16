"""
2c: a model-fabricated tool fence whose body is a *tool result* must go to
the hallucination path, not the passthrough.

Background (conversation df488630 turn 47): the model wrote

    ````tool:mcp_file_write|🔐 file write: Docs/x.md|markdown
    {'success': True, 'message': 'Patched Docs/x.md ...', 'bytes_written': 46520}
    ````

as prose — a result for a call it never made.  ``_dispatch_fake_tool_block``
took the non-shell passthrough branch and rewrote it to a plain ````markdown
block, which (a) showed the user a fabricated "success" as if it were a
legitimate code sample and (b) hid it from Layer C, whose result-shape
detector deliberately does not look inside fences.

The gate is body-shape only: ``detect_fake_tool_result`` (the same
classifier Layer C uses) on the fence body.  A fake fence with a prose or
code body still passes through, so the model can quote the format when
discussing it.  Shell fakes with a ``$`` line still dispatch for real.

These drive ``process_text_delta`` across chunks so the accumulator ->
closer -> dispatch -> state seam is what's asserted.
"""
from __future__ import annotations

from app.text_delta_processor import process_text_delta
from tests.test_text_delta_processor import _make_executor, _make_state


def _stream(chunks):
    ex = _make_executor()
    st = _make_state()
    events = []
    for c in chunks:
        events.extend(process_text_delta(ex, c, st))
        # Mirror the caller: streaming_tool_executor breaks out of the
        # stream loop as soon as hallucination_detected is set, so later
        # chunks never reach process_text_delta.
        if st.hallucination_detected:
            break
    return events, st


def _texts(events):
    return "".join(e.get("content", "") for e in events if e.get("type") == "text")


FAKE_WRITE_RESULT = [
    "````tool:mcp_file_write|🔐 file write: Docs/x.md|markdown\n",
    "{'success': True, 'message': 'Patched Docs/x.md successfully', ",
    "'path': 'Docs/x.md', 'bytes_written': 46520}\n",
    # Closer and trailing prose in ONE chunk: this is the case the
    # dispatcher's early return must handle itself, since the caller's
    # break only prevents *later* chunks from being processed.
    "````\n\nDesign doc now updated. Proceeding.\n",
]

FAKE_PROPOSE_RESULT = [
    "````tool:mcp_memory_propose|🔐 Memory Propose|text\n",
    "{'success': True, 'message': 'Memory proposed for review (17 pending).', 'proposal_id': 'prop_5dc1b430'}\n",
    "````\n\n",
]


class TestResultShapedFakeFence:

    def test_fabricated_file_write_result_goes_to_hallucination_path(self):
        events, st = _stream(FAKE_WRITE_RESULT)
        assert st.hallucination_detected is True
        kinds = [e["type"] for e in events]
        assert "hallucination_recovery" in kinds
        rec = next(e for e in events if e["type"] == "hallucination_recovery")
        assert rec["reason"] == "fabricated_tool_result_fence"
        # The fabricated outcome is never laundered into a code block.
        assert "success" not in _texts(events)
        assert "bytes_written" not in _texts(events)

    def test_fabricated_memory_propose_result_caught(self):
        events, st = _stream(FAKE_PROPOSE_RESULT)
        assert st.hallucination_detected is True
        assert "17 pending" not in _texts(events)

    def test_trailing_prose_after_flagged_fence_not_emitted(self):
        # The turn is being aborted for retry; text after the fabrication
        # would be a fragment of a response that is about to be discarded.
        events, st = _stream(FAKE_WRITE_RESULT)
        assert "Proceeding" not in _texts(events)

    def test_fabrication_never_entered_assistant_text(self):
        _, st = _stream(FAKE_WRITE_RESULT)
        assert "success" not in st.assistant_text


class TestNonResultFakeFencesStillPassThrough:

    def test_prose_body_passes_through_as_plain_fence(self):
        events, st = _stream([
            "````tool:mcp_file_read|🔐 file read: notes.md|markdown\n",
            "# Notes\n\nJust some markdown the model is quoting.\n",
            "````\n\n",
            "After.\n",
        ])
        assert st.hallucination_detected is False
        out = _texts(events)
        assert out.startswith("````markdown\n# Notes")
        assert "After." in out

    def test_code_body_passes_through(self):
        events, st = _stream([
            "````tool:mcp_file_read|🔐 file read: a.py|python\n",
            "def f():\n    return {'not': 'a tool result'}\n",
            "````\n\n",
        ])
        assert st.hallucination_detected is False
        assert "def f():" in _texts(events)

    def test_shell_fake_with_prompt_still_dispatches(self):
        events, st = _stream([
            "````tool:mcp_run_shell_command|🔐 Shell: ls|bash\n",
            "$ ls\n",
            "````\n\n",
        ])
        assert st.hallucination_detected is False
        assert any(e["type"] == "fake_tool_detected" for e in events)
        assert st.fake_tool_dispatch_count == 1
