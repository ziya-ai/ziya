"""
A long reply followed by tool calls must not be written twice.

Observed (conversation 2ae03e67, Claude Opus 5.5, 2026-10-07): iteration 7
streamed a 10.5k-char answer ending "I'll add this to the handoff draft.",
called handoff_write, got {ok, draft, note}; iteration 8 then rewrote the
whole answer (10.4k chars, reworded) because nothing told the model the
first copy was on screen.  Two seams fix it:

1. The executor appends an "already shown" note to the tool_result turn of
   any iteration that streamed substantial prose before its tool calls.
2. The living-draft prelude asks for handoff_write BEFORE the reply, not
   "before finishing", so the shape that triggers (1) is rarer.
"""

import inspect

from app.streaming_tool_executor import StreamingToolExecutor
from app.utils.handoff_prelude import render_draft_prelude

LONG = "x" * StreamingToolExecutor.ALREADY_SHOWN_MIN_CHARS
SHORT = "Let me read that file."


class TestDecision:
    def test_long_prose_then_tools_fires(self):
        assert StreamingToolExecutor._should_note_already_shown(LONG, True)

    def test_short_prose_then_tools_does_not_fire(self):
        # "Let me check…" + a read is the normal working shape.
        assert not StreamingToolExecutor._should_note_already_shown(SHORT, True)

    def test_no_tools_does_not_fire(self):
        # Nothing follows the text; the no-tool decider owns this case.
        assert not StreamingToolExecutor._should_note_already_shown(LONG, False)

    def test_whitespace_padding_does_not_count(self):
        assert not StreamingToolExecutor._should_note_already_shown(
            SHORT + "\n" * 5000, True)


class TestNote:
    def test_note_names_the_size_and_forbids_repeating(self):
        note = StreamingToolExecutor._already_shown_note(10546)
        assert "10,546" in note
        assert "already displayed" in note
        assert "Do not repeat" in note
        # It must not forbid continuing when the task is unfinished.
        assert "Continue working" in note

    def test_note_lands_inside_the_tool_result_turn(self):
        # Anthropic shape: user turn whose content is a list of tool_result
        # blocks.  The note must join that turn, not become a second
        # consecutive user message (some models answer those with an empty
        # completion).  tool_result blocks stay first.
        conv = [
            {"role": "assistant", "content": [{"type": "text", "text": LONG},
                                              {"type": "tool_use", "id": "t1"}]},
            {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t1",
                                          "content": "{\"ok\": true}"}]},
        ]
        StreamingToolExecutor._append_to_last_user_turn(
            conv, StreamingToolExecutor._already_shown_note(len(LONG)))
        assert len(conv) == 2
        blocks = conv[-1]["content"]
        assert blocks[0]["type"] == "tool_result"
        assert blocks[-1]["type"] == "text"
        assert "already displayed" in blocks[-1]["text"]


class TestSeam:
    """The helpers exist; this pins that the loop actually calls them at the
    point where the tool_result turn has just been appended."""

    def test_executor_appends_note_after_tool_results(self):
        src = inspect.getsource(StreamingToolExecutor)
        anchor = src.index("tool_msg = self.provider.build_tool_result_message(")
        # The next repeat-block recovery comment bounds the region.
        end = src.index("Cross-iteration repeat-block recovery", anchor)
        region = src[anchor:end]
        assert "_should_note_already_shown(assistant_text, tools_executed_this_iteration)" in region
        assert "_append_to_last_user_turn(" in region
        assert "_already_shown_note(" in region


class TestPreludeWording:
    def test_draft_prelude_asks_for_the_write_before_the_reply(self):
        text = render_draft_prelude({"document": "## State\nthings"})
        assert "before finishing" not in text
        assert "BEFORE you write your reply" in text
        assert "handoff_write(" in text
