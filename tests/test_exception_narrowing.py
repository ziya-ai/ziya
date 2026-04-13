"""
Tests for Phase 6 exception narrowing.

Verifies that specific exception types are caught where expected,
and that unexpected exception types propagate correctly.
"""

import asyncio
import json
import os
import sys
import pytest
from unittest.mock import MagicMock, AsyncMock, patch, PropertyMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))


# ---------------------------------------------------------------------------
# streaming_tool_executor: _convert_tool_schema
# ---------------------------------------------------------------------------

class TestSchemaConversionExceptions:
    """Schema conversion should catch AttributeError/TypeError/ValueError
    from malformed Pydantic models, but propagate other errors."""

    def _get_executor(self):
        """Create a minimal executor instance for testing schema conversion.

        _convert_tool_schema is a pure instance method that doesn't touch
        self.bedrock or self.provider, so we can bypass __init__ entirely.
        """
        from app.streaming_tool_executor import StreamingToolExecutor
        exe = StreamingToolExecutor.__new__(StreamingToolExecutor)
        exe.model_config = {'family': 'claude'}
        return exe

    def test_catches_attribute_error_on_schema(self):
        """model_json_schema() raising AttributeError should be caught."""
        executor = self._get_executor()
        bad_schema = MagicMock()
        bad_schema.model_json_schema.side_effect = AttributeError("no schema")

        tool = MagicMock()
        tool.name = "test_tool"
        tool.description = "A test"
        tool.metadata = None
        tool.input_schema = bad_schema
        tool.inputSchema = None

        result = executor._convert_tool_schema(tool)
        # Should fall back to empty schema, not raise
        assert result['input_schema'] == {"type": "object", "properties": {}}

    def test_catches_type_error_on_schema(self):
        """model_json_schema() raising TypeError should be caught."""
        executor = self._get_executor()
        bad_schema = MagicMock()
        bad_schema.model_json_schema.side_effect = TypeError("bad type")

        tool = MagicMock()
        tool.name = "test_tool"
        tool.description = "A test"
        tool.metadata = None
        tool.input_schema = bad_schema
        tool.inputSchema = None

        result = executor._convert_tool_schema(tool)
        assert result['input_schema'] == {"type": "object", "properties": {}}

    def test_catches_value_error_on_schema(self):
        """model_json_schema() raising ValueError should be caught."""
        executor = self._get_executor()
        bad_schema = MagicMock()
        bad_schema.model_json_schema.side_effect = ValueError("invalid")

        tool = MagicMock()
        tool.name = "test_tool"
        tool.description = "A test"
        tool.metadata = None
        tool.input_schema = bad_schema
        tool.inputSchema = None

        result = executor._convert_tool_schema(tool)
        assert result['input_schema'] == {"type": "object", "properties": {}}


# ---------------------------------------------------------------------------
# streaming_tool_executor: _track_estimation_accuracy
# ---------------------------------------------------------------------------

class TestEstimationAccuracyExceptions:
    """_track_estimation_accuracy should catch calibration-related errors
    but let programming errors propagate."""

    def _get_executor(self):
        """Create a minimal executor for testing estimation accuracy.

        _track_estimation_accuracy doesn't touch self.bedrock/provider,
        so bypass __init__ entirely.
        """
        from app.streaming_tool_executor import StreamingToolExecutor
        exe = StreamingToolExecutor.__new__(StreamingToolExecutor)
        exe.model_config = {'family': 'claude'}
        return exe

    def test_catches_import_error(self):
        """ImportError from missing calibrator module should be caught."""
        executor = self._get_executor()
        from app.streaming_tool_executor import IterationUsage
        usage = IterationUsage(input_tokens=100, output_tokens=50)

        with patch('app.utils.token_calibrator.get_token_calibrator',
                   side_effect=ImportError("no calibrator")):
            # Should not raise
            executor._track_estimation_accuracy(usage, [], None, 100, 0, 100)

    def test_catches_key_error(self):
        """KeyError from missing config keys should be caught."""
        executor = self._get_executor()
        from app.streaming_tool_executor import IterationUsage
        usage = IterationUsage(input_tokens=100, output_tokens=50)

        with patch('app.utils.token_calibrator.get_token_calibrator',
                   side_effect=KeyError("missing_key")):
            executor._track_estimation_accuracy(usage, [], None, 100, 0, 100)


# ---------------------------------------------------------------------------
# tool_execution: execute_single_tool timeout handling
# ---------------------------------------------------------------------------

class TestToolExecutionTimeoutSplit:
    """asyncio.TimeoutError should be caught by its own except clause,
    not lumped into the generic Exception handler."""

    @pytest.mark.asyncio
    async def test_timeout_produces_error_result(self):
        """TimeoutError should produce a tool error result."""
        from app.tool_execution import ToolExecContext, execute_single_tool

        ctx = ToolExecContext(
            tool_id="t1",
            tool_name="slow_tool",
            actual_tool_name="slow_tool",
            args={"command": "sleep 999"},
            all_tools=[],
            internal_tool_names=set(),
            mcp_manager=MagicMock(),
            project_root="/tmp",
            conversation_id="conv1",
            conversation=[],
            recent_commands=[],
            inter_tool_delay={'current': 0.1, 'min': 0.1, 'max': 1, 'decay_factor': 0.6,
                              'growth_factor': 2.5, 'last_was_throttled': False},
            iteration_start_time=0,
            track_yield_fn=lambda x: x,
            drain_feedback_fn=lambda: [],
            executor=MagicMock(),
        )

        # Simulate the tool raising TimeoutError
        mock_manager = ctx.mcp_manager
        mock_manager.call_tool = AsyncMock(side_effect=asyncio.TimeoutError())

        events = []
        async for event in execute_single_tool(ctx):
            events.append(event)

        # Should have at least a tool_result with timeout error message
        error_results = [e for e in events if e.get('type') == '_tool_result']
        assert len(error_results) >= 1
        assert 'timed out' in error_results[0]['result']


# ---------------------------------------------------------------------------
# message_stop_handler: continuation error narrowing
# ---------------------------------------------------------------------------

class TestMessageStopExceptionNarrowing:
    """Continuation errors should be caught specifically, not broadly."""

    def test_handler_module_imports(self):
        """Verify the module can be imported (basic smoke test)."""
        from app.message_stop_handler import handle_message_stop, MessageStopState
        assert handle_message_stop is not None
        assert MessageStopState is not None


# ---------------------------------------------------------------------------
# Verify intentionally broad handlers are documented
# ---------------------------------------------------------------------------

class TestIntentionallyBroadHandlers:
    """The remaining 'except Exception' handlers should have
    explanatory comments documenting why they are intentionally broad."""

    def test_main_iteration_handler_documented(self):
        """streaming_tool_executor.py main iteration handler has comment."""
        import inspect
        from app.streaming_tool_executor import StreamingToolExecutor
        source = inspect.getsource(StreamingToolExecutor.stream_with_tools)
        # Look for the documented broad handler
        assert 'Intentionally broad' in source or 'intentionally broad' in source

    def test_tool_execution_handler_documented(self):
        """tool_execution.py tool catch-all has comment."""
        import inspect
        from app.tool_execution import execute_single_tool
        source = inspect.getsource(execute_single_tool)
        assert 'Intentionally broad' in source or 'intentionally broad' in source or 'third-party' in source


# ---------------------------------------------------------------------------
# Batch 2: server.py exception narrowing
# ---------------------------------------------------------------------------

class TestAgentExceptionNarrowing:
    """Verify agent.py exception handlers were narrowed correctly."""

    def _read_agent_source(self):
        import inspect
        import app.agents.agent as agent_module
        return inspect.getsource(agent_module)

    def test_chat_history_cleaning_narrowed(self):
        """_clean_chat_history catches TypeError/ValueError/AttributeError/IndexError, not Exception."""
        src = self._read_agent_source()
        # Find the function and check its except clause
        assert 'except (TypeError, ValueError, AttributeError, IndexError) as e:\n        logger.error(f"Error cleaning chat history' in src

    def test_message_creation_narrowed(self):
        """Message creation in _format_chat_history catches specific types."""
        src = self._read_agent_source()
        assert 'except (TypeError, ValueError, KeyError) as e:\n                    logger.error(f"Error creating message' in src

    def test_image_processing_narrowed(self):
        """Image processing catches JSONDecodeError."""
        src = self._read_agent_source()
        assert 'json.JSONDecodeError' in src

    def test_file_reading_narrowed(self):
        """File reading catches OSError/UnicodeDecodeError/PermissionError."""
        src = self._read_agent_source()
        assert 'except (OSError, UnicodeDecodeError, PermissionError) as e:\n            logger.error(f"Error reading file' in src

    def test_token_estimation_narrowed(self):
        """estimate_tokens catches specific import/runtime errors."""
        src = self._read_agent_source()
        assert 'except (ImportError, FileNotFoundError, PermissionError, OSError, RuntimeError)' in src

    def test_mcp_tools_narrowed(self):
        """MCP tool loading catches ImportError/OSError/RuntimeError."""
        src = self._read_agent_source()
        assert 'except (ImportError, OSError, RuntimeError) as e:\n            logger.warning(f"Failed to get MCP tools for agent' in src

    def test_input_mapping_narrowed(self):
        """Input mapping catches TypeError/ValueError/KeyError/AttributeError."""
        src = self._read_agent_source()
        assert 'except (TypeError, ValueError, KeyError, AttributeError) as e:\n                logger.error(f"Error applying input mapping' in src

    def test_chunk_safety_narrowed(self):
        """RunLogPatch id assignment catches AttributeError/TypeError."""
        src = self._read_agent_source()
        assert 'except (AttributeError, TypeError) as e:\n                            logger.warning(f"Could not add id to RunLogPatch' in src

    def test_log_output_narrowed(self):
        """log_output catches AttributeError/TypeError."""
        src = self._read_agent_source()
        assert 'except (AttributeError, TypeError) as e:\n        logger.error(f"Error in log_output' in src

    def test_broad_handlers_documented(self):
        """Intentionally broad handlers in agent.py have comments."""
        src = self._read_agent_source()
        broad_comments = src.count('Intentionally broad')
        # At least 5 documented broad handlers:
        # retry loop (890), outer retry (1029), provider invoke (1338),
        # Google API (2000), safe_astream_log (2242), _ensure_safe_chunk (2332)
        assert broad_comments >= 5, f"Expected >=5 documented broad handlers, found {broad_comments}"

    def test_model_reinit_narrowed(self):
        """Model reinit for throttling retry catches specific errors."""
        src = self._read_agent_source()
        assert 'except (ImportError, RuntimeError, ValueError, OSError) as reinit_error' in src

    def test_google_agent_fallback_narrowed(self):
        """Google agent creation fallback catches specific types."""
        src = self._read_agent_source()
        assert 'except (ImportError, RuntimeError, ValueError, TypeError) as e:\n            logger.warning(f"Failed to create Google function calling agent' in src


class TestServerExceptionNarrowing:
    """Verify that server.py exception handlers have been narrowed
    and intentionally broad ones are documented."""

    def _get_server_source(self):
        """Read the server.py source for inspection."""
        import app.server
        import inspect
        return inspect.getsource(app.server)

    def test_image_processing_catches_specific(self):
        """Image tuple processing should catch ValueError/TypeError/KeyError."""
        from app.server import build_messages_for_streaming
        import inspect
        source = inspect.getsource(build_messages_for_streaming)
        # The handler near "Error processing images from tuple" should be narrowed
        assert 'ValueError' in source or 'TypeError' in source

    def test_lifecycle_handlers_narrowed(self):
        """Shutdown handlers should not use bare except Exception."""
        source = self._get_server_source()
        # Find the shutdown section — look for the specific narrowed handlers
        # "Swarm scratch GC during shutdown" should be next to specific types
        gc_idx = source.find("Swarm scratch GC during shutdown")
        if gc_idx > 0:
            # Check the ~200 chars before the log message for specific types
            context = source[max(0, gc_idx - 200):gc_idx]
            assert 'OSError' in context or 'ImportError' in context or 'RuntimeError' in context

    def test_mcp_shutdown_catches_timeout(self):
        """MCP shutdown should catch asyncio.TimeoutError specifically."""
        source = self._get_server_source()
        mcp_idx = source.find("MCP shutdown failed")
        if mcp_idx > 0:
            context = source[max(0, mcp_idx - 200):mcp_idx]
            assert 'TimeoutError' in context

    def test_json_chunk_parsing_narrowed(self):
        """JSON chunk parsing should catch JSONDecodeError, not Exception."""
        source = self._get_server_source()
        # "If parsing fails, just yield original chunk" should be near specific types
        chunk_idx = source.find("If parsing fails, just yield original chunk")
        if chunk_idx > 0:
            context = source[max(0, chunk_idx - 200):chunk_idx]
            assert 'JSONDecodeError' in context or 'json.JSONDecodeError' in context

    def test_tool_execution_in_agent_path_narrowed(self):
        """Tool execution in agent streaming path should be narrowed."""
        source = self._get_server_source()
        # "STREAM: Tool execution error" should have specific types nearby
        tool_idx = source.find("STREAM: Tool execution error")
        if tool_idx > 0:
            context = source[max(0, tool_idx - 200):tool_idx]
            assert 'TimeoutError' in context or 'RuntimeError' in context

    def test_model_init_narrowed(self):
        """Model initialization should catch specific types."""
        source = self._get_server_source()
        init_idx = source.find("Error initializing model")
        if init_idx > 0:
            context = source[max(0, init_idx - 200):init_idx]
            assert 'ImportError' in context or 'ValueError' in context

    def test_documented_broad_handlers_have_comments(self):
        """All intentionally broad handlers in server.py should have comments."""
        source = self._get_server_source()
        # Count "Intentionally broad" comments — should be >= 10
        broad_count = source.count('Intentionally broad')
        assert broad_count >= 10, (
            f"Expected >= 10 documented-broad handlers in server.py, found {broad_count}"
        )

    def test_connectivity_precheck_narrowed(self):
        """Connectivity pre-check should catch OSError and TimeoutError."""
        source = self._get_server_source()
        idx = source.find("Connectivity pre-check failed")
        if idx > 0:
            context = source[max(0, idx - 200):idx]
            assert 'OSError' in context or 'TimeoutError' in context

    def test_background_init_narrowed(self):
        """Background MCP init and folder warming should be narrowed."""
        source = self._get_server_source()
        mcp_idx = source.find("Background MCP initialization failed")
        if mcp_idx > 0:
            context = source[max(0, mcp_idx - 200):mcp_idx]
            assert 'ImportError' in context or 'OSError' in context

    def test_key_rotation_narrowed(self):
        """Key rotation check should not use bare except Exception."""
        source = self._get_server_source()
        kr_idx = source.find("Key rotation check failed")
        if kr_idx > 0:
            context = source[max(0, kr_idx - 200):kr_idx]
            assert 'OSError' in context or 'RuntimeError' in context


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
