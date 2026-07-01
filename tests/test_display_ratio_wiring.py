"""
Tests for the token-display calibration wiring.

Covers:
  - TokenCalibrator.get_display_ratio() tier selection and bias (mean, not p95)
  - estimate_tokens_fast() consuming the learned ratio, and the
    double-compensation guard (legacy FILE_TYPE_MULTIPLIER applied ONLY for
    non-type-specific tiers).

Context: _record_calibration was fixed to fold cache_write tokens into the
effective input (otherwise fresh cache-write turns produced implausible
chars/token ratios that got rejected, so the calibrator never learned). But
the file-tree display used a hardcoded file_size/4.1 and never consumed the
calibrator at all — so learning never reached the displayed number. These
tests lock in the wiring that closes that gap.
"""

import os
import tempfile
from unittest.mock import patch

import pytest

from app.utils.token_calibrator import TokenCalibrator


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_calibrator():
    """A calibrator with an isolated temp cache file (no disk side effects)."""
    fd, path = tempfile.mkstemp(suffix=".json", prefix="calib_test_")
    os.close(fd)
    os.remove(path)  # let the calibrator create it fresh
    cal = TokenCalibrator(cache_file=path)
    return cal, path


def _cleanup(path):
    for p in (path, path + ".lock"):
        try:
            os.remove(p)
        except OSError:
            pass


# ---------------------------------------------------------------------------
# get_display_ratio
# ---------------------------------------------------------------------------

class TestGetDisplayRatio:
    def test_learned_type_uses_mean_not_p95(self):
        """Tier 1 must return the learned MEAN, not p95.

        The display should err toward over-counting; p95 (the chars/token
        high end) minimizes tokens, which is the wrong bias for a budget bar.
        """
        cal, path = _make_calibrator()
        try:
            cal.stats_by_model_and_type['claude']['.py'] = {
                'mean': 4.0, 'p50': 4.0, 'p95': 8.0,
                'sample_count': 10, 'min': 3.5, 'max': 8.0,
            }
            ratio, source = cal.get_display_ratio('.py', model_family='claude')
            assert source == 'learned_type'
            assert ratio == pytest.approx(4.0), "should use mean, not p95 (8.0)"
        finally:
            _cleanup(path)

    def test_learned_type_clamped_to_bounds(self):
        """A corrupt learned mean is clamped into [MIN, MAX]."""
        cal, path = _make_calibrator()
        try:
            cal.stats_by_model_and_type['claude']['.css'] = {
                'mean': 99.0, 'p50': 99.0, 'p95': 99.0,
                'sample_count': 3, 'min': 99.0, 'max': 99.0,
            }
            ratio, source = cal.get_display_ratio('.css', model_family='claude')
            assert source == 'learned_type'
            assert ratio == cal.MAX_CHARS_PER_TOKEN
        finally:
            _cleanup(path)

    def test_zero_sample_count_falls_through(self):
        """A stats entry with no samples must not be treated as learned."""
        cal, path = _make_calibrator()
        try:
            cal.stats_by_model_and_type['claude']['.py'] = {
                'mean': 4.0, 'p95': 4.0, 'sample_count': 0,
            }
            # release default for claude has only 'default', not '.py'
            cal.global_by_model['claude'] = 4.18
            ratio, source = cal.get_display_ratio('.py', model_family='claude')
            assert source == 'global'
            assert ratio == pytest.approx(4.18)
        finally:
            _cleanup(path)

    def test_global_tier_when_no_type_data(self):
        cal, path = _make_calibrator()
        try:
            cal.global_by_model['claude'] = 4.18
            ratio, source = cal.get_display_ratio('.xyz', model_family='claude')
            assert source == 'global'
            assert ratio == pytest.approx(4.18)
        finally:
            _cleanup(path)

    def test_fallback_when_unknown_family(self):
        cal, path = _make_calibrator()
        try:
            ratio, source = cal.get_display_ratio('.py', model_family='nonesuch')
            assert source == 'fallback'
            assert ratio == cal.global_fallback
        finally:
            _cleanup(path)

    def test_release_type_tier(self):
        """A baked-in release default for a model+type is reported as release_type."""
        cal, path = _make_calibrator()
        try:
            # claude release defaults define only 'default'; inject a typed one
            cal.release_defaults['claude']['.py'] = 3.7
            ratio, source = cal.get_display_ratio('.py', model_family='claude')
            assert source == 'release_type'
            assert ratio == pytest.approx(3.7)
        finally:
            _cleanup(path)


# ---------------------------------------------------------------------------
# estimate_tokens_fast wiring + double-compensation guard
# ---------------------------------------------------------------------------

class TestEstimateTokensFastWiring:
    def _write_file(self, content: str, suffix: str) -> str:
        fd, path = tempfile.mkstemp(suffix=suffix)
        with os.fdopen(fd, 'w') as f:
            f.write(content)
        return path

    def test_learned_type_skips_multiplier(self):
        """For a learned (type-specific) ratio, the legacy multiplier must NOT
        be applied — otherwise we double-compensate a ratio that already
        encodes type density."""
        from app.utils import directory_util as du

        path = self._write_file("x" * 4000, ".json")  # .json multiplier = 1.2
        try:
            class _FakeCal:
                def get_display_ratio(self, ext, model_family=None):
                    return 4.0, 'learned_type'

            with patch('app.utils.token_calibrator.get_token_calibrator',
                       return_value=_FakeCal()):
                tokens = du.estimate_tokens_fast(path)
            # 4000 / 4.0 = 1000, NO 1.2 multiplier
            assert tokens == 1000
        finally:
            os.remove(path)

    def test_global_tier_applies_multiplier(self):
        """For a non-type-specific (global) ratio, the legacy multiplier IS
        applied — preserving today's behavior for unlearned types."""
        from app.utils import directory_util as du

        path = self._write_file("x" * 4000, ".json")  # .json multiplier = 1.2
        try:
            class _FakeCal:
                def get_display_ratio(self, ext, model_family=None):
                    return 4.0, 'global'

            with patch('app.utils.token_calibrator.get_token_calibrator',
                       return_value=_FakeCal()):
                tokens = du.estimate_tokens_fast(path)
            # 4000 / 4.0 = 1000, * 1.2 multiplier = 1200
            assert tokens == 1200
        finally:
            os.remove(path)

    def test_calibrator_failure_falls_back_to_4_1(self):
        """If the calibrator raises, fall back to 4.1 + multiplier (legacy)."""
        from app.utils import directory_util as du

        path = self._write_file("x" * 4100, ".txt")  # .txt multiplier = 1.0
        try:
            with patch('app.utils.token_calibrator.get_token_calibrator',
                       side_effect=RuntimeError("boom")):
                tokens = du.estimate_tokens_fast(path)
            # 4100 / 4.1 = 1000, * 1.0 = 1000
            assert tokens == 1000
        finally:
            os.remove(path)
