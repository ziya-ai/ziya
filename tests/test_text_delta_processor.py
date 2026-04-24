"""
Tests for app.text_delta_processor — the extracted text delta processing logic.

These test process_text_delta() in isolation without needing the full
streaming loop, verifying fence buffering, hallucination detection,
visualization block handling, and content optimization.
"""

import pytest
from unittest.mock import MagicMock, patch
from app.text_delta_processor import process_text_delta, TextDeltaState


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_executor():
    """Build a minimal mock executor with the methods process_text_delta needs."""
    executor = MagicMock()
    # _normalize_fence_spacing returns text unchanged by default
    executor._normalize_fence_spacing.side_effect = lambda text, tracker: text
    # _update_code_block_tracker is a no-op by default
    executor._update_code_block_tracker.return_value = None
    # No block opening buffer initially
    executor._block_opening_buffer = ""
    # Content optimizer mock
    optimizer = MagicMock()
    optimizer.add_content.side_effect = lambda t: [t] if t else []
    optimizer.flush_remaining.return_value = ""
    executor._content_optimizer = optimizer
    return executor


def _make_state(**overrides) -> TextDeltaState:
    """Build a TextDeltaState with sane defaults."""
    defaults = dict(
        assistant_text="",
        viz_buffer="",
        in_viz_block=False,
        code_block_tracker={
            'in_block': False, 'block_type': None, 'accumulated_content': ''
        },
        iteration_start_time=0.0,
    )
    defaults.update(overrides)
    return TextDeltaState(**defaults)


# ---------------------------------------------------------------------------
# Fence buffering
# ---------------------------------------------------------------------------

class TestFenceBuffering:
    """Tests for incomplete code fence buffering."""

    def test_incomplete_fence_at_end_is_buffered(self):
        """Text ending with ``` outside a code block should be buffered."""
        executor = _make_executor()
        state = _make_state()

        events = process_text_delta(executor, "some text```", state)

        assert events == []  # Nothing yielded — buffered
        assert executor._block_opening_buffer == "some text```"
        assert state.assistant_text == ""  # Not accumulated yet

    def test_buffered_text_prepended_to_next_chunk(self):
        """Buffered fence text should be joined with the next chunk."""
        executor = _make_executor()
        executor._block_opening_buffer = "```python"
        state = _make_state()

        events = process_text_delta(executor, "\nprint('hi')\n", state)

        assert len(events) > 0
        # The combined text should have been accumulated
        assert "```python" in state.assistant_text
        assert "print('hi')" in state.assistant_text

    def test_fence_inside_code_block_not_buffered(self):
        """``` at end of text inside a code block is a closing fence, not buffered."""
        executor = _make_executor()
        state = _make_state(code_block_tracker={
            'in_block': True, 'block_type': 'python', 'accumulated_content': ''
        })

        events = process_text_delta(executor, "return x\n```", state)

        # Should NOT be buffered — it's a closing fence
        assert executor._block_opening_buffer == ""
        assert "return x" in state.assistant_text


# ---------------------------------------------------------------------------
# Fake tool syntax suppression
# ---------------------------------------------------------------------------

class TestFakeToolSuppression:
    """Tests for per-chunk fake tool-call syntax filtering."""

    def test_tool_colon_syntax_suppressed(self):
        """Text containing ```tool: should be suppressed."""
        executor = _make_executor()
        state = _make_state()

        events = process_text_delta(executor, "```tool:mcp_run_shell_command\n$ ls\n```", state)

        # Should be suppressed — not accumulated
        assert state.assistant_text == ""

    def test_backtick_tool_syntax_suppressed(self):
        """Text containing `tool: should be suppressed."""
        executor = _make_executor()
        state = _make_state()

        events = process_text_delta(executor, "use `tool:fetch` to get data", state)

        assert state.assistant_text == ""


# ---------------------------------------------------------------------------
# Hallucination detection
# ---------------------------------------------------------------------------

class TestHallucinationDetection:
    """Tests for backend hallucination detection."""

    def test_security_block_detected(self):
        """SECURITY BLOCK pattern should trigger hallucination flag."""
        executor = _make_executor()
        state = _make_state()

        events = process_text_delta(
            executor,
            "SECURITY BLOCK: Command 'rm -rf' is not allowed by policy",
            state,
        )

        assert state.hallucination_detected is True
        assert any("fabricate" in e.get('content', '') for e in events)

    def test_allowed_commands_pattern_detected(self):
        """'Allowed commands: awk' pattern should trigger hallucination."""
        executor = _make_executor()
        state = _make_state()

        events = process_text_delta(
            executor,
            "Allowed commands: awk, grep, sed, find",
            state,
        )

        assert state.hallucination_detected is True

    def test_hallucination_suppressed_inside_code_block(self):
        """Hallucination patterns inside code blocks should be ignored."""
        executor = _make_executor()
        state = _make_state(code_block_tracker={
            'in_block': True, 'block_type': 'text', 'accumulated_content': ''
        })

        events = process_text_delta(
            executor,
            "SECURITY BLOCK: this is just a quote in a code example",
            state,
        )

        assert state.hallucination_detected is False

    def test_no_false_positive_on_normal_text(self):
        """Normal text should not trigger hallucination detection."""
        executor = _make_executor()
        state = _make_state()

        events = process_text_delta(executor, "Here is my analysis of the code.", state)

        assert state.hallucination_detected is False
        assert state.assistant_text == "Here is my analysis of the code."

    # -- Structural-parrot patterns (observed in production session fabrication) --

    def test_fake_tool_invocation_header_file_write(self):
        """Fabricated 'file_write|🔐' UI-format header should trigger detection."""
        executor = _make_executor()
        state = _make_state()

        process_text_delta(
            executor,
            "file_write|🔐 file write: /tmp/patch.py|python\n"
            "{'success': True, 'message': 'Created /tmp/patch.py', 'path': '/tmp/patch.py'}",
            state,
        )

        assert state.hallucination_detected is True

    def test_fake_tool_invocation_header_shell(self):
        """Fabricated 'run_shell_command|🔐 Shell:' header should trigger detection."""
        executor = _make_executor()
        state = _make_state()

        process_text_delta(
            executor,
            "run_shell_command|🔐 Shell: python3 patch.py|bash\npatched 14 flags",
            state,
        )

        assert state.hallucination_detected is True

    def test_fake_tool_invocation_with_mcp_prefix(self):
        """The 'mcp_' prefix variant should also trigger detection."""
        executor = _make_executor()
        state = _make_state()

        process_text_delta(
            executor,
            "mcp_file_write|🔐 file write: .ziya/note.md|markdown",
            state,
        )

        assert state.hallucination_detected is True

    def test_fake_file_write_result_dict(self):
        """Fabricated file_write result dict shape should trigger detection."""
        executor = _make_executor()
        state = _make_state()

        process_text_delta(
            executor,
            "The result was {'success': True, 'message': 'Created foo.py (842 bytes)', "
            "'path': 'foo.py', 'bytes_written': 842}",
            state,
        )

        assert state.hallucination_detected is True

    def test_structural_patterns_suppressed_inside_code_block(self):
        """Same structural patterns inside a code fence must NOT trigger."""
        executor = _make_executor()
        state = _make_state(code_block_tracker={
            'in_block': True, 'block_type': 'text', 'accumulated_content': ''
        })

        process_text_delta(
            executor,
            "run_shell_command|🔐 Shell: example\n"
            "{'success': True, 'message': 'x', 'path': 'y'}",
            state,
        )

        assert state.hallucination_detected is False

    def test_legitimate_success_dict_no_false_positive(self):
        """A result dict without the file-tool key combination should not trigger."""
        executor = _make_executor()
        state = _make_state()

        process_text_delta(
            executor,
            "The API returned {'success': True, 'data': [1, 2, 3], 'count': 3}",
            state,
        )

        assert state.hallucination_detected is False


# ---------------------------------------------------------------------------
# Visualization block buffering
# ---------------------------------------------------------------------------

class TestVisualizationBuffering:
    """Tests for visualization block buffering (mermaid, vega-lite, etc.)."""

    def test_mermaid_opening_starts_buffer(self):
        """```mermaid should start viz buffering."""
        executor = _make_executor()
        state = _make_state()

        events = process_text_delta(executor, "```mermaid\ngraph TD;", state)

        assert state.in_viz_block is True
        assert "```mermaid" in state.viz_buffer

    def test_viz_closing_flushes_buffer(self):
        """Closing ``` inside a viz block should flush it."""
        executor = _make_executor()
        state = _make_state(
            in_viz_block=True,
            viz_buffer="```mermaid\ngraph TD;\nA-->B;",
        )

        events = process_text_delta(executor, "\n```\n", state)

        assert state.in_viz_block is False
        assert state.viz_buffer == ""
        # Should have yielded the complete viz block
        assert len(events) == 1
        assert "```mermaid" in events[0]['content']

    def test_non_viz_text_not_buffered(self):
        """Regular text should not trigger viz buffering."""
        executor = _make_executor()
        state = _make_state()

        events = process_text_delta(executor, "Just normal text here.", state)

        assert state.in_viz_block is False
        assert state.viz_buffer == ""


# ---------------------------------------------------------------------------
# Content optimization and output
# ---------------------------------------------------------------------------

class TestContentOutput:
    """Tests for the content optimizer path."""

    def test_normal_text_yields_event(self):
        """Normal text should produce a text event."""
        executor = _make_executor()
        state = _make_state()

        events = process_text_delta(executor, "Hello world.", state)

        text_events = [e for e in events if e.get('type') == 'text']
        assert len(text_events) >= 1
        # Combined text should contain our input
        combined = ''.join(e['content'] for e in text_events)
        assert "Hello world." in combined

    def test_natural_break_flushes_optimizer(self):
        """Text ending with punctuation should force-flush the optimizer."""
        executor = _make_executor()
        leftover_text = "leftover"
        executor._content_optimizer.flush_remaining.return_value = leftover_text
        state = _make_state()

        events = process_text_delta(executor, "End of sentence.", state)

        # Should have flushed — the leftover appears
        contents = [e.get('content', '') for e in events]
        assert leftover_text in contents

    def test_accumulates_assistant_text(self):
        """Each call should append to state.assistant_text."""
        executor = _make_executor()
        state = _make_state(assistant_text="Previous. ")

        process_text_delta(executor, "New text.", state)

        assert state.assistant_text == "Previous. New text."
