"""
Tests for StreamingToolExecutor._handle_usage_event and its sub-methods.

These test the extracted usage tracking, accuracy estimation, and
calibration recording logic (Phase 5b of the refactoring plan).
"""

import contextlib
import json
import logging
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

    def test_list_content_tool_result_with_list_content(self):
        """tool_result content can itself be a LIST of blocks (Anthropic's
        [{"type":"text","text":...}] shape), not just a string. The list shape
        previously counted as ZERO tokens — a latent undercount. Each text
        sub-block must now contribute its tokens (naive len//4 path)."""
        msg = {"content": [{
            "type": "tool_result",
            "content": [
                {"type": "text", "text": "abcdefgh"},   # 8 chars
                {"type": "text", "text": "ijklmnop"},   # 8 chars
            ],
        }]}
        tokens = StreamingToolExecutor._estimate_message_tokens(
            msg, calibrator=None, has_calibration=False, model_family=None,
        )
        # 8 + 8 = 16 chars -> 16 // 4
        assert tokens == 16 // 4

    def test_list_content_tool_result_list_calibrated(self):
        """List-shaped tool_result text sub-blocks route through the
        calibrator when one is present, same as every other text path."""
        cal = MagicMock()
        cal.estimate_tokens.side_effect = lambda text, model_family=None: len(text) // 4
        msg = {"content": [{
            "type": "tool_result",
            "content": [{"type": "text", "text": "x" * 40}],  # 40 chars
        }]}
        tokens = StreamingToolExecutor._estimate_message_tokens(
            msg, calibrator=cal, has_calibration=True, model_family="claude",
        )
        assert tokens == 40 // 4
        cal.estimate_tokens.assert_called_once_with("x" * 40, model_family="claude")

    def test_list_content_tool_result_list_string_items(self):
        """A tool_result whose list items are bare strings (not dicts) is
        tolerated — each string contributes its own tokens."""
        msg = {"content": [{
            "type": "tool_result",
            "content": ["abcd", "efgh"],  # 4 + 4 chars
        }]}
        tokens = StreamingToolExecutor._estimate_message_tokens(
            msg, calibrator=None, has_calibration=False, model_family=None,
        )
        assert tokens == (4 + 4) // 4

    def test_list_content_tool_result_list_non_text_blocks_skipped(self):
        """Non-text sub-blocks (e.g. image blocks) contribute no text tokens,
        consistent with the rest of this estimator, and must not crash."""
        msg = {"content": [{
            "type": "tool_result",
            "content": [
                {"type": "image", "source": {"data": "BASE64..."}},
                {"type": "text", "text": "abcdefgh"},  # 8 chars — the only counted part
                None,
                42,
            ],
        }]}
        tokens = StreamingToolExecutor._estimate_message_tokens(
            msg, calibrator=None, has_calibration=False, model_family=None,
        )
        assert tokens == 8 // 4

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

    def test_folds_cache_write_into_actual_tokens_on_fresh_turn(self):
        """On a fresh cache-write turn, cache_write_tokens must be folded into
        the recorded actual_tokens.

        Regression for the calibration undercount bug: Bedrock reports the
        large codebase context as cache_write_tokens (NOT input_tokens) on the
        first turn, so total_input alone is far too small for the character
        volume. The recorded actual_tokens must reflect input + cache_write so
        the chars/token ratio is plausible.
        """
        executor = _make_executor()
        # 26,352 chars of file content — mirrors the reported warning.
        executor._extract_file_contents_from_messages.return_value = {
            "app/big.py": "x" * 26352,
        }
        # File content is essentially the whole input (file fraction ~1.0).
        system_text = "x" * 26352
        # Fresh turn: small uncached remainder, large cache write.
        usage = IterationUsage(
            input_tokens=1414, cache_read_tokens=0, cache_write_tokens=8000,
        )

        mock_cal = MagicMock()
        mock_model_mgr = MagicMock()
        mock_model_mgr.get_model_id.return_value = "claude-sonnet"
        mock_model_mgr.get_model_config.return_value = {"family": "claude"}

        with patch("app.utils.token_calibrator.get_token_calibrator", return_value=mock_cal), \
             patch("app.agents.models.ModelManager", mock_model_mgr):
            executor._record_calibration(
                usage, "conv_1", [], system_text,
                fresh=1414, cached=0, total_input=1414,
            )

        recorded = mock_cal.record_actual_usage.call_args[1]['actual_tokens']
        # Without the fix this would be ~1414 (ratio 26352/1414 = 18.6, rejected).
        # With cache_write folded in: effective_input ~= 1414 + 8000 = 9414.
        assert recorded > 1414, "cache_write tokens were not folded in"
        ratio = 26352 / recorded
        assert 1.0 <= ratio <= 15.0, (
            f"chars/token ratio {ratio:.2f} is outside the calibrator's "
            f"plausible band; sample would be rejected"
        )

    def test_no_cache_write_uses_total_input_unchanged(self):
        """When cache_write_tokens is 0, behaviour is unchanged: actual_tokens
        is derived from total_input alone (no spurious inflation)."""
        executor = _make_executor()
        executor._extract_file_contents_from_messages.return_value = {
            "app/big.py": "x" * 8000,
        }
        usage = IterationUsage(
            input_tokens=2000, cache_read_tokens=0, cache_write_tokens=0,
        )

        mock_cal = MagicMock()
        mock_model_mgr = MagicMock()
        mock_model_mgr.get_model_id.return_value = "claude-sonnet"
        mock_model_mgr.get_model_config.return_value = {"family": "claude"}

        with patch("app.utils.token_calibrator.get_token_calibrator", return_value=mock_cal), \
             patch("app.agents.models.ModelManager", mock_model_mgr):
            executor._record_calibration(
                usage, "conv_1", [], "x" * 8000,
                fresh=2000, cached=0, total_input=2000,
            )

        recorded = mock_cal.record_actual_usage.call_args[1]['actual_tokens']
        # file fraction ~1.0 of total_input (2000), no cache_write to add.
        assert recorded == 2000

    def test_real_calibrator_accepts_sample_after_fix(self):
        """End-to-end: the recorded sample is accepted by the REAL calibrator's
        plausibility check (rather than triggering the 'implausible ratio'
        rejection) once cache_write is folded in."""
        import tempfile
        from app.utils.token_calibrator import TokenCalibrator

        executor = _make_executor()
        executor._extract_file_contents_from_messages.return_value = {
            "app/big.py": "x" * 26352,
        }
        usage = IterationUsage(
            input_tokens=1414, cache_read_tokens=0, cache_write_tokens=8000,
        )

        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tf:
            cal = TokenCalibrator(cache_file=tf.name)

        mock_model_mgr = MagicMock()
        mock_model_mgr.get_model_id.return_value = "claude-sonnet"
        mock_model_mgr.get_model_config.return_value = {"family": "claude"}

        with patch("app.utils.token_calibrator.get_token_calibrator", return_value=cal), \
             patch("app.agents.models.ModelManager", mock_model_mgr):
            executor._record_calibration(
                usage, "conv_1", [], "x" * 26352,
                fresh=1414, cached=0, total_input=1414,
            )

        # The sample should have been accepted and a learned ratio stored.
        assert cal.samples_by_model_and_type.get("claude", {}).get(".py"), (
            "calibration sample was rejected — cache_write fix not effective"
        )


# ---------------------------------------------------------------------------
# _track_estimation_accuracy — bucketed INFO instrumentation (step 5)
# ---------------------------------------------------------------------------

class TestEstimationAccuracyInstrumentation:
    """Verifies the estimate-vs-actual gap is logged at INFO, decomposed into
    conversation / system / baseline buckets, so the 65%->1M undercount can be
    attributed to a specific component rather than a single lumped number.
    """

    def _executor_with_calibrator(self, baseline_overhead):
        """Build an executor whose _track_estimation_accuracy runs for real,
        with a mock calibrator returning a fixed baseline overhead and naive
        (len//4) per-content estimates.
        """
        executor = MagicMock(spec=StreamingToolExecutor)
        executor.model_config = {"family": "claude"}
        executor._track_estimation_accuracy = (
            StreamingToolExecutor._track_estimation_accuracy.__get__(executor)
        )
        # Use the real static estimators so bucket math is exercised.
        executor._estimate_message_tokens = StreamingToolExecutor._estimate_message_tokens
        executor._estimate_content_tokens = StreamingToolExecutor._estimate_content_tokens
        return executor

    def _patches(self, baseline_overhead):
        """Patch the calibrator + ModelManager used inside the method."""
        cal = MagicMock()
        cal.global_by_model = {"claude": 4.1}
        cal.get_baseline_overhead.return_value = baseline_overhead
        # has_calibration path uses calibrator.estimate_tokens; route to naive
        # len//4 so the test math is deterministic and independent of ratios.
        cal.estimate_tokens.side_effect = lambda text, model_family=None: len(text) // 4

        mock_mgr = MagicMock()
        mock_mgr.get_model_id.return_value = "claude-opus"
        mock_mgr.get_model_config.return_value = {"family": "claude"}
        return cal, mock_mgr

    @staticmethod
    @contextlib.contextmanager
    def _capture_accuracy_log():
        """Capture the ESTIMATE_ACCURACY line directly off the executor's
        logger.

        pytest's `caplog` attaches to the root logger and relies on
        propagation, but `app.streaming_tool_executor`'s ModeAwareLogger
        sets `propagate = False` and installs its own handler on first emit
        (see app/utils/logging_utils.ModeAwareLogger._ensure_configured).
        So `caplog` never sees these records — it goes blind the moment the
        logger is configured, which made the prior versions of these tests
        pass only by accident (zero records → the `not in` style checks were
        vacuous) and fail outright once a real assertion on content was added.
        Attaching our own handler to the named logger captures the record
        regardless of propagation or mode.
        """
        records = []

        class _Collector(logging.Handler):
            def emit(self, record):
                records.append(record.getMessage())

        target = logging.getLogger("app.streaming_tool_executor")
        handler = _Collector(level=logging.INFO)
        prev_level = target.level
        target.addHandler(handler)
        target.setLevel(logging.INFO)
        try:
            yield records
        finally:
            target.removeHandler(handler)
            target.setLevel(prev_level)

    def _run_and_capture(self, executor, cal, mock_mgr, conversation,
                         system_content, usage):
        """Run _track_estimation_accuracy under the patches and return the
        single ESTIMATE_ACCURACY log line (or None)."""
        with self._capture_accuracy_log() as records, \
             patch("app.utils.token_calibrator.get_token_calibrator", return_value=cal), \
             patch("app.agents.models.ModelManager", mock_mgr):
            executor._track_estimation_accuracy(
                usage, conversation, system_content,
                fresh=usage.input_tokens, cached=usage.cache_read_tokens,
                total_input=usage.input_tokens + usage.cache_read_tokens,
            )
        return next((m for m in records if "ESTIMATE_ACCURACY" in m), None)

    def test_logs_bucketed_accuracy_at_info(self):
        """The accuracy line is emitted at INFO and names all three buckets,
        the signed delta, and the raw character volumes per bucket."""
        executor = self._executor_with_calibrator(baseline_overhead=37000)
        cal, mock_mgr = self._patches(37000)

        conversation = [{"role": "user", "content": "x" * 400}]   # ~100 tok
        system_content = "y" * 4000                                # ~1000 tok
        usage = IterationUsage(
            input_tokens=2000, cache_read_tokens=0, cache_write_tokens=50000,
        )

        line = self._run_and_capture(
            executor, cal, mock_mgr, conversation, system_content, usage,
        )
        assert line is not None, "no INFO accuracy line emitted"
        # All three token buckets must be present and named.
        assert "conv=" in line
        assert "system=" in line
        assert "baseline=" in line
        # Signed delta present (direction of error).
        assert "delta=" in line
        # Raw character volumes — the field that distinguishes a content
        # gap from a chars->token ratio gap. Must carry the real counts:
        # conversation = 400 chars, system = 4000 chars.
        assert "chars: conv=400 system=4,000" in line, (
            f"char volumes not surfaced correctly: {line}"
        )

    def test_baseline_zero_is_visible_in_buckets(self):
        """When the baseline hasn't been measured yet (fresh ~/.ziya), the
        log must show baseline=0 — this is the single biggest collectable
        cause of undercount right after a calibration reset.
        """
        executor = self._executor_with_calibrator(baseline_overhead=0)
        cal, mock_mgr = self._patches(0)

        conversation = [{"role": "user", "content": "x" * 400}]
        system_content = "y" * 4000
        usage = IterationUsage(
            input_tokens=2000, cache_read_tokens=0, cache_write_tokens=50000,
        )

        line = self._run_and_capture(
            executor, cal, mock_mgr, conversation, system_content, usage,
        )
        assert line is not None, "no accuracy line emitted"
        assert "baseline=0" in line, f"baseline=0 not surfaced: {line}"

    def test_label_reflects_calibrated_ratio_not_naive(self):
        """The estimation_method label must report the calibrated ratio when
        the calibrator has a learned global ratio for the family — not the
        frozen 'naive (4.0 chars/token)' string it used to always print
        (which actively misled debugging of the undercount). The label is
        derived from calibrator.global_by_model, set to 4.1 in _patches.
        """
        executor = self._executor_with_calibrator(baseline_overhead=0)
        cal, mock_mgr = self._patches(0)

        conversation = [{"role": "user", "content": "x" * 400}]
        system_content = "y" * 4000
        usage = IterationUsage(
            input_tokens=2000, cache_read_tokens=0, cache_write_tokens=50000,
        )

        line = self._run_and_capture(
            executor, cal, mock_mgr, conversation, system_content, usage,
        )
        assert line is not None, "no accuracy line emitted"
        assert "calibrated (4.10 chars/token, claude)" in line, (
            f"label did not reflect the calibrated ratio: {line}"
        )
        assert "naive (4.0 chars/token)" not in line, (
            f"stale naive label still present: {line}"
        )

    def test_label_falls_back_when_family_not_learned(self):
        """If the family has no learned global ratio yet, the label reports
        'calibrated (release defaults)' rather than the stale naive string —
        the calibrator's per-content estimator still applies release defaults.
        """
        executor = self._executor_with_calibrator(baseline_overhead=0)
        cal, mock_mgr = self._patches(0)
        # Family present in config but absent from learned global_by_model.
        cal.global_by_model = {}

        conversation = [{"role": "user", "content": "x" * 400}]
        system_content = "y" * 4000
        usage = IterationUsage(
            input_tokens=2000, cache_read_tokens=0, cache_write_tokens=50000,
        )

        line = self._run_and_capture(
            executor, cal, mock_mgr, conversation, system_content, usage,
        )
        assert line is not None, "no accuracy line emitted"
        assert "calibrated (release defaults)" in line, (
            f"fallback label not used when family unlearned: {line}"
        )
