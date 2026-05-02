"""
Tests for StreamingToolExecutor._handle_usage_event and its sub-methods.

These test the extracted usage tracking, accuracy estimation, and
calibration recording logic (Phase 5b of the refactoring plan).
"""

import json
import pytest
from unittest.mock import MagicMock, patch, PropertyMock
from app.streaming_tool_executor import StreamingToolExecutor, IterationUsage


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_usage_event(**overrides):
    """Create a mock UsageEvent with sensible defaults."""
    evt = MagicMock()
    evt.input_tokens = overrides.get('input_tokens', 1000)
    evt.output_tokens = overrides.get('output_tokens', 200)
    evt.cache_read_tokens = overrides.get('cache_read_tokens', 5000)
    evt.cache_write_tokens = overrides.get('cache_write_tokens', 0)
    return evt


def _make_executor():
    """Create a minimal StreamingToolExecutor mock with model_config."""
    executor = MagicMock(spec=StreamingToolExecutor)
    executor.model_config = {
        'token_limit': 200000,
        'supports_extended_context': False,
    }
    # Bind the real methods under test
    executor._handle_usage_event = StreamingToolExecutor._handle_usage_event.__get__(executor)
    executor._track_estimation_accuracy = StreamingToolExecutor._track_estimation_accuracy.__get__(executor)
    executor._record_calibration = StreamingToolExecutor._record_calibration.__get__(executor)
    executor._estimate_message_tokens = StreamingToolExecutor._estimate_message_tokens
    executor._estimate_content_tokens = StreamingToolExecutor._estimate_content_tokens
    executor._extract_file_contents_from_messages = MagicMock(return_value={})
    return executor


def _make_throttle_state():
    return {
        'cache_working': None,
        'last_cache_efficiency': 0.0,
    }


# ---------------------------------------------------------------------------
# _handle_usage_event
# ---------------------------------------------------------------------------

class TestHandleUsageEvent:
    """Tests for the top-level _handle_usage_event dispatcher."""

    def test_updates_iteration_usage_fields(self):
        """Usage fields should be copied from the event to iteration_usage."""
        executor = _make_executor()
        usage = IterationUsage()
        evt = _make_usage_event(input_tokens=500, output_tokens=100,
                                cache_read_tokens=3000, cache_write_tokens=200)

        executor._handle_usage_event(
            evt, usage, iteration=1, conversation_id=None,
            conversation=[], system_content=None,
            throttle_state=_make_throttle_state(),
        )

        assert usage.input_tokens == 500
        assert usage.output_tokens == 100
        assert usage.cache_read_tokens == 3000
        assert usage.cache_write_tokens == 200

    def test_updates_throttle_state_on_cache_hit(self):
        """When cached > 0 and iteration > 0, throttle_state should update."""
        executor = _make_executor()
        usage = IterationUsage()
        ts = _make_throttle_state()

        evt = _make_usage_event(cache_read_tokens=8000)
        executor._handle_usage_event(
            evt, usage, iteration=1, conversation_id=None,
            conversation=[], system_content=None,
            throttle_state=ts,
        )

        assert ts['cache_working'] is True
        assert ts['last_cache_efficiency'] > 0

    def test_no_throttle_update_on_iteration_zero(self):
        """Iteration 0 logs metrics debug but doesn't set cache_working."""
        executor = _make_executor()
        usage = IterationUsage()
        ts = _make_throttle_state()

        evt = _make_usage_event(cache_read_tokens=5000)
        executor._handle_usage_event(
            evt, usage, iteration=0, conversation_id=None,
            conversation=[], system_content=None,
            throttle_state=ts,
        )

        # iteration==0 takes the first branch (metrics debug), not cache update
        assert ts['cache_working'] is None

    def test_zero_event_fields_do_not_overwrite(self):
        """If event field is 0/None, iteration_usage should keep prior value."""
        executor = _make_executor()
        usage = IterationUsage(input_tokens=999)

        # Simulate an event where input_tokens is 0 (falsy)
        evt = _make_usage_event(input_tokens=0, output_tokens=50,
                                cache_read_tokens=0, cache_write_tokens=0)
        executor._handle_usage_event(
            evt, usage, iteration=2, conversation_id=None,
            conversation=[], system_content=None,
            throttle_state=_make_throttle_state(),
        )

        # 0 is falsy, so `or` preserves the existing value
        assert usage.input_tokens == 999
        assert usage.output_tokens == 50


# ---------------------------------------------------------------------------
# _estimate_message_tokens / _estimate_content_tokens
# ---------------------------------------------------------------------------

class TestEstimateTokens:
    """Tests for the static token estimation helpers."""

    def test_string_message_naive(self):
        """Without calibrator, estimate = len // 4."""
        tokens = StreamingToolExecutor._estimate_message_tokens(
            {"content": "hello world"},  # 11 chars
            calibrator=None, has_calibration=False, model_family=None,
        )
        assert tokens == 11 // 4

    def test_string_message_calibrated(self):
        """With calibrator, delegates to calibrator.estimate_tokens."""
        cal = MagicMock()
        cal.estimate_tokens.return_value = 42

        tokens = StreamingToolExecutor._estimate_message_tokens(
            {"content": "hello world"},
            calibrator=cal, has_calibration=True, model_family="claude",
        )
        assert tokens == 42
        cal.estimate_tokens.assert_called_once_with("hello world", model_family="claude")

    def test_list_content_with_text_block(self):
        """Text blocks in list content should contribute tokens."""
        msg = {"content": [{"type": "text", "text": "abcdefghijklmnop"}]}  # 16 chars
        tokens = StreamingToolExecutor._estimate_message_tokens(
            msg, calibrator=None, has_calibration=False, model_family=None,
        )
        assert tokens == 16 // 4

    def test_list_content_with_tool_use(self):
        """tool_use blocks should estimate from serialized JSON."""
        msg = {"content": [{"type": "tool_use", "input": {"command": "ls"}}]}
        tokens = StreamingToolExecutor._estimate_message_tokens(
            msg, calibrator=None, has_calibration=False, model_family=None,
        )
        expected = len(json.dumps({"command": "ls"})) // 4
        assert tokens == expected

    def test_list_content_with_tool_result(self):
        """tool_result blocks should estimate from content string."""
        msg = {"content": [{"type": "tool_result", "content": "output text here"}]}  # 16 chars
        tokens = StreamingToolExecutor._estimate_message_tokens(
            msg, calibrator=None, has_calibration=False, model_family=None,
        )
        assert tokens == 16 // 4

    def test_estimate_system_content_string(self):
        """System content as a string."""
        tokens = StreamingToolExecutor._estimate_content_tokens(
            "system prompt text",  # 18 chars
            calibrator=None, has_calibration=False, model_family=None,
        )
        assert tokens == 18 // 4

    def test_estimate_system_content_list(self):
        """System content as a list of text blocks."""
        content = [{"type": "text", "text": "block one"}, {"type": "text", "text": "block two"}]
        tokens = StreamingToolExecutor._estimate_content_tokens(
            content, calibrator=None, has_calibration=False, model_family=None,
        )
        assert tokens == (len("block one") + len("block two")) // 4

    def test_non_dict_blocks_skipped(self):
        """Non-dict items in a list should not crash."""
        msg = {"content": ["just a string", None, 42]}
        tokens = StreamingToolExecutor._estimate_message_tokens(
            msg, calibrator=None, has_calibration=False, model_family=None,
        )
        assert tokens == 0


# ---------------------------------------------------------------------------
# _record_calibration
# ---------------------------------------------------------------------------

class TestRecordCalibration:
    """Tests for the _record_calibration method."""

    def test_skips_when_no_files(self):
        """If no file contents extracted, should return without recording."""
        executor = _make_executor()
        executor._extract_file_contents_from_messages.return_value = {}
        usage = IterationUsage(input_tokens=1000, cache_read_tokens=5000)

        with patch("app.utils.token_calibrator.get_token_calibrator") as mock_cal:
            executor._record_calibration(
                usage, "conv_1", [], "system text",
                fresh=1000, cached=5000, total_input=6000,
            )
            # Should NOT call record_actual_usage since no files
            mock_cal.return_value.record_actual_usage.assert_not_called()

    def test_records_when_files_present(self):
        """With files and tokens, should call calibrator.record_actual_usage."""
        executor = _make_executor()
        executor._extract_file_contents_from_messages.return_value = {
            "app/main.py": "x" * 1000,
        }
        usage = IterationUsage(
            input_tokens=500, cache_read_tokens=0, cache_write_tokens=0,
        )

        mock_cal = MagicMock()
        mock_model_mgr = MagicMock()
        mock_model_mgr.get_model_id.return_value = "claude-sonnet"
        mock_model_mgr.get_model_config.return_value = {"family": "claude"}

        with patch("app.utils.token_calibrator.get_token_calibrator", return_value=mock_cal) as _, \
             patch("app.agents.models.ModelManager", mock_model_mgr):
            executor._record_calibration(
                usage, "conv_1", [], "x" * 2000,
                fresh=500, cached=0, total_input=500,
            )

            mock_cal.record_actual_usage.assert_called_once()
            call_kwargs = mock_cal.record_actual_usage.call_args
            assert call_kwargs[1]['conversation_id'] == "conv_1"
            assert call_kwargs[1]['model_family'] == "claude"
            assert call_kwargs[1]['actual_tokens'] > 0

    def test_skips_when_cache_read_tokens_present(self):
        """If cache_read_tokens > 0, calibration is skipped to avoid inflated ratios."""
        executor = _make_executor()
        executor._extract_file_contents_from_messages.return_value = {
            "app/main.py": "x" * 1000,
        }
        usage = IterationUsage(
            input_tokens=500, cache_read_tokens=30000, cache_write_tokens=0,
        )

        with patch("app.utils.token_calibrator.get_token_calibrator") as mock_get_cal:
            executor._record_calibration(
                usage, "conv_1", [], "x" * 2000,
                fresh=500, cached=30000, total_input=30500,
            )
            # Should not even reach get_token_calibrator
            mock_get_cal.assert_not_called()

    def test_handles_calibrator_import_error_gracefully(self):
        """ImportError from token_calibrator should not propagate."""
        executor = _make_executor()
        usage = IterationUsage(input_tokens=1000)

        with patch("app.utils.token_calibrator.get_token_calibrator", side_effect=ImportError("no module")):
            # Should not raise
            executor._record_calibration(
                usage, "conv_1", [], "system",
                fresh=1000, cached=0, total_input=1000,
            )
