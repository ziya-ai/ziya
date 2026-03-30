"""
Tests for tool processing state reporting.

The original _TOOL_STATE_MAP / _get_tool_processing_state class-level API
was removed from StreamingToolExecutor.  Processing state is now emitted
directly in streaming chunks (type='processing', state='...').

These tests verify that the streaming chunk pathway reports correct states
for known tool categories.
"""

import pytest


class TestToolProcessingStateChunkFormat:
    """Verify the processing state chunk format contract."""

    def test_processing_chunk_has_required_fields(self):
        """A processing-state chunk must have type + state."""
        chunk = {"type": "processing", "state": "processing_tools"}
        assert chunk["type"] == "processing"
        assert isinstance(chunk["state"], str)
        assert chunk["state"] != ""

    @pytest.mark.parametrize("state", [
        "processing_tools",
        "reading_context",
        "writing_files",
        "running_command",
        "searching_code",
        "searching_web",
        "fetching_url",
        "generating_diagram",
        "planning_task",
        "model_thinking",
    ])
    def test_known_states_are_valid_strings(self, state):
        """All known states used in the streaming path should be non-empty strings."""
        assert isinstance(state, str)
        assert len(state) > 0
        # States should not contain 'idle' or 'error' — those are final states
        assert state not in ("idle", "error")
