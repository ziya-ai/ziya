"""
Regression test for variable scoping in stream_chunks().

Root-cause bug: `mcp_tools` was only assigned inside the `if question:` block
(Bedrock streaming path, ~line 1758) but referenced unconditionally in the
LangChain fallback iteration loop (~line 2681), causing:

    UnboundLocalError: cannot access local variable 'mcp_tools'
    where it is not associated with a value

The fix initializes `mcp_tools = []` alongside other iteration-loop variables
before the loop starts.  These tests verify that fix and guard against similar
scoping regressions.
"""

import inspect
import textwrap

import pytest


def _get_stream_chunks_source() -> str:
    """Import stream_chunks and return its source code."""
    from app.server import stream_chunks
    return inspect.getsource(stream_chunks)


class TestStreamChunksVariableScoping:
    """
    Verify that variables referenced in the iteration loop body are
    initialized before the loop, not only inside conditional branches.
    """

    def _get_init_block(self) -> str:
        """
        Extract the initialization block: text between the marker comment
        and the while loop start.
        """
        source = _get_stream_chunks_source()
        marker = "# Initialize variables for agent iteration loop"
        assert marker in source, f"Cannot find marker: '{marker}'"

        marker_pos = source.index(marker)
        loop_pos = source.index("while iteration < max_iterations", marker_pos)
        return source[marker_pos:loop_pos]

    def test_mcp_tools_initialized_before_iteration_loop(self):
        """
        Regression: mcp_tools must be assigned before the
        `while iteration < max_iterations` loop, not only inside
        `if question:`.  Without this, the LangChain fallback path
        hits UnboundLocalError.
        """
        init_block = self._get_init_block()
        assert "mcp_tools" in init_block, (
            "mcp_tools is not initialized in the iteration variable "
            "block before the while loop. The LangChain fallback path "
            "will hit UnboundLocalError."
        )

    def test_mcp_tools_defaults_to_empty_list(self):
        """The default value of mcp_tools must be an empty list, not None."""
        init_block = self._get_init_block()
        assert "mcp_tools = []" in init_block, (
            "mcp_tools should default to [] (empty list), not None or other. "
            f"Init block content: {init_block[:300]}"
        )

    def test_all_iteration_variables_initialized(self):
        """
        Guard against similar scoping bugs: all variables that are used
        inside the iteration loop and assigned conditionally elsewhere
        should have a default initialization before the loop.
        """
        init_block = self._get_init_block()

        # These variables are referenced inside the while loop and MUST
        # be initialized before it starts.
        required_inits = [
            "mcp_tools",
            "processed_tool_calls",
            "max_iterations",
            "iteration",
            "messages_for_model",
            "all_tool_results",
        ]

        for var_name in required_inits:
            assert f"{var_name} =" in init_block or f"{var_name}=" in init_block, (
                f"Variable '{var_name}' is not initialized in the iteration "
                f"variable block before the while loop. This will cause "
                f"UnboundLocalError if the Bedrock streaming path is skipped."
            )

    def test_mcp_tools_not_only_in_conditional(self):
        """
        Verify mcp_tools isn't ONLY assigned inside `if question:`.
        There must be an unconditional assignment in the init block.
        """
        source = _get_stream_chunks_source()

        # Scope the search to after the init block marker
        marker = "# Initialize variables for agent iteration loop"
        marker_pos = source.index(marker)
        loop_pos = source.index("while iteration < max_iterations", marker_pos)
        init_block = source[marker_pos:loop_pos]

        # Find the first assignment of mcp_tools within the init block
        assert "mcp_tools =" in init_block, (
            "No mcp_tools assignment found in the init block between "
            "the marker comment and the while loop."
        )

        # Also verify this assignment is NOT indented inside an `if` block
        # by checking that the line starts at the same indentation as other
        # init variables like `processed_tool_calls`
        for line in init_block.splitlines():
            stripped = line.lstrip()
            if stripped.startswith("mcp_tools ="):
                mcp_indent = len(line) - len(stripped)
                break
        else:
            pytest.fail("mcp_tools assignment line not found")

        for line in init_block.splitlines():
            stripped = line.lstrip()
            if stripped.startswith("processed_tool_calls ="):
                ref_indent = len(line) - len(stripped)
                break
        else:
            pytest.fail("processed_tool_calls assignment line not found")

        assert mcp_indent == ref_indent, (
            f"mcp_tools is indented at {mcp_indent} spaces but "
            f"processed_tool_calls is at {ref_indent}. "
            f"mcp_tools may be inside a conditional block."
        )
