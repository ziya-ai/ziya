"""
Tests for granular tool-specific processing states.

The StreamingToolExecutor maps tool names to descriptive processing states
so the frontend can display context-aware spinner labels instead of a
generic "Running tools…" message.
"""

import pytest
from unittest.mock import MagicMock


class TestToolProcessingStateMap:
    """Test the _TOOL_STATE_MAP and _get_tool_processing_state method."""

    @pytest.fixture
    def executor(self):
        """Create a StreamingToolExecutor with minimal init."""
        from app.streaming_tool_executor import StreamingToolExecutor
        return StreamingToolExecutor.__new__(StreamingToolExecutor)

    # -- Skill / planning tools --

    def test_get_skill_details_returns_planning_task(self, executor):
        assert executor._get_tool_processing_state('get_skill_details') == 'planning_task'

    def test_sequentialthinking_returns_model_thinking(self, executor):
        assert executor._get_tool_processing_state('sequentialthinking') == 'model_thinking'

    # -- File / AST context tools --

    def test_file_read_returns_reading_context(self, executor):
        assert executor._get_tool_processing_state('file_read') == 'reading_context'

    def test_file_list_returns_reading_context(self, executor):
        assert executor._get_tool_processing_state('file_list') == 'reading_context'

    def test_ast_get_tree_returns_reading_context(self, executor):
        assert executor._get_tool_processing_state('ast_get_tree') == 'reading_context'

    def test_ast_search_returns_reading_context(self, executor):
        assert executor._get_tool_processing_state('ast_search') == 'reading_context'

    def test_ast_references_returns_reading_context(self, executor):
        assert executor._get_tool_processing_state('ast_references') == 'reading_context'

    # -- Write / command tools --

    def test_file_write_returns_writing_files(self, executor):
        assert executor._get_tool_processing_state('file_write') == 'writing_files'

    def test_run_shell_command_returns_running_command(self, executor):
        assert executor._get_tool_processing_state('run_shell_command') == 'running_command'

    # -- Search tools --

    def test_workspace_search_returns_searching_code(self, executor):
        assert executor._get_tool_processing_state('WorkspaceSearch') == 'searching_code'

    def test_internal_code_search_returns_searching_code(self, executor):
        assert executor._get_tool_processing_state('InternalCodeSearch') == 'searching_code'

    def test_brave_web_search_returns_searching_web(self, executor):
        assert executor._get_tool_processing_state('brave_web_search') == 'searching_web'

    def test_nova_web_search_returns_searching_web(self, executor):
        assert executor._get_tool_processing_state('nova_web_search') == 'searching_web'

    def test_fetch_returns_fetching_url(self, executor):
        assert executor._get_tool_processing_state('fetch') == 'fetching_url'

    # -- Diagram tools --

    def test_search_architecture_shapes_returns_generating_diagram(self, executor):
        assert executor._get_tool_processing_state('search_architecture_shapes') == 'generating_diagram'

    def test_get_architecture_diagram_template_returns_generating_diagram(self, executor):
        assert executor._get_tool_processing_state('get_architecture_diagram_template') == 'generating_diagram'

    def test_list_architecture_shape_categories_returns_generating_diagram(self, executor):
        assert executor._get_tool_processing_state('list_architecture_shape_categories') == 'generating_diagram'

    # -- Fallback --

    def test_unknown_tool_returns_processing_tools(self, executor):
        assert executor._get_tool_processing_state('some_random_tool') == 'processing_tools'

    def test_tool_with_search_in_name_returns_searching_code(self, executor):
        """Tools with 'search' in the name should fall back to searching_code."""
        assert executor._get_tool_processing_state('my_custom_search_tool') == 'searching_code'

    # -- MCP-prefixed tool names --
    # In practice, _get_tool_processing_state receives already-normalized
    # names (mcp_ prefix stripped). These tests verify the full pipeline.

    def test_mcp_prefixed_tool_normalized(self, executor):
        """MCP tools have mcp_ prefix stripped before state lookup."""
        normalized = executor._normalize_tool_name('mcp_file_read')
        assert normalized == 'file_read'
        assert executor._get_tool_processing_state(normalized) == 'reading_context'

    def test_mcp_prefixed_shell_command(self, executor):
        normalized = executor._normalize_tool_name('mcp_run_shell_command')
        assert normalized == 'run_shell_command'
        assert executor._get_tool_processing_state(normalized) == 'running_command'


class TestToolStateMapCompleteness:
    """Verify the state map covers the most common built-in tools."""

    @pytest.fixture
    def state_map(self):
        from app.streaming_tool_executor import StreamingToolExecutor
        return StreamingToolExecutor._TOOL_STATE_MAP

    def test_map_is_not_empty(self, state_map):
        assert len(state_map) > 0

    def test_all_states_are_strings(self, state_map):
        for tool, state in state_map.items():
            assert isinstance(tool, str), f"Key {tool!r} is not a string"
            assert isinstance(state, str), f"Value {state!r} for {tool!r} is not a string"

    def test_no_idle_or_error_states_in_map(self, state_map):
        """The map should only contain active processing states, not terminal ones."""
        terminal_states = {'idle', 'error', 'sending'}
        for tool, state in state_map.items():
            assert state not in terminal_states, (
                f"Tool {tool!r} maps to terminal state {state!r}"
            )
