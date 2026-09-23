"""
Token calibration is keyed by MODEL, falling back to model FAMILY.

fable-5.1 tokenizes source at ~3.1 chars/token, Sonnet 4 at ~4.0; one
'claude' bucket averaged them and biased every estimate for whichever model
was in use.  Samples are recorded under both the model's bucket and its
family's; lookups take the model bucket when it has data for the file type
and the family otherwise.  Samples are now persisted (compactly, no paths)
so learning survives a restart -- before, only stats were written and the
first sample after a restart replaced a 100-sample statistic with n=1.

Every test here fails against the pre-change calibrator.
"""
import json
import os
import tempfile

import pytest

from app.utils.token_calibrator import TokenCalibrator

FABLE = "us.anthropic.claude-fable-5-1"
FABLE_KEY = "anthropic.claude-fable-5-1"
SONNET = "us.anthropic.claude-sonnet-4-20250514-v1:0"


@pytest.fixture
def cache_path():
    fd, path = tempfile.mkstemp(suffix=".json", prefix="calib_buckets_")
    os.close(fd)
    os.remove(path)
    yield path
    for p in (path, path + ".lock", path + ".tmp"):
        try:
            os.remove(p)
        except OSError:
            pass


def _record(cal, model_id, ext, chars_per_token, n=1):
    # 4000 chars at the requested density; ratio must land inside [1, 15]
    for i in range(n):
        cal.record_actual_usage(
            conversation_id="c", file_contents={f"f{i}{ext}": "x" * 4000},
            actual_tokens=int(4000 / chars_per_token),
            model_id=model_id, model_family="claude",
        )


def test_region_prefix_is_not_part_of_the_bucket(cache_path):
    cal = TokenCalibrator(cache_file=cache_path)
    assert cal._normalize_model_key("us.anthropic.claude-fable-5-1") == FABLE_KEY
    assert cal._normalize_model_key("global.anthropic.claude-fable-5-1") == FABLE_KEY
    assert cal._normalize_model_key("Anthropic.Claude-Fable-5-1") == FABLE_KEY
    assert cal._normalize_model_key(None) is None
    assert cal._normalize_model_key("unknown") is None


def test_sample_lands_in_model_and_family_buckets(cache_path):
    cal = TokenCalibrator(cache_file=cache_path)
    _record(cal, FABLE, ".py", 3.1)
    assert cal.stats_by_model_and_type[FABLE_KEY][".py"]["sample_count"] == 1
    assert cal.stats_by_model_and_type["claude"][".py"]["sample_count"] == 1


def test_display_ratio_prefers_model_bucket_then_family(cache_path):
    cal = TokenCalibrator(cache_file=cache_path)
    _record(cal, SONNET, ".py", 4.0, n=3)   # family bucket now blends both
    _record(cal, FABLE, ".py", 3.1)
    _record(cal, SONNET, ".ts", 4.0)         # only sonnet has seen .ts

    ratio, source = cal.get_display_ratio(".py", model_family="claude", model_id="global." + FABLE_KEY)
    assert source == "learned_model_type"
    assert ratio == pytest.approx(4000 / int(4000 / 3.1), rel=1e-6)

    ratio, source = cal.get_display_ratio(".ts", model_family="claude", model_id=FABLE)
    assert source == "learned_type", "type unseen by this model falls back to the family"
    assert ratio == pytest.approx(4.0, rel=1e-3)

    ratio, source = cal.get_display_ratio(".py", model_family="claude", model_id="us.anthropic.claude-opus-9")
    assert source == "learned_type", "an unseen model uses the family blend"


def test_estimate_tokens_uses_model_bucket(cache_path):
    cal = TokenCalibrator(cache_file=cache_path)
    _record(cal, SONNET, ".py", 4.0, n=3)
    _record(cal, FABLE, ".py", 2.0)
    text = "y" * 8000
    by_model = cal.estimate_tokens(text, "a.py", model_family="claude", model_id=FABLE)
    by_family = cal.estimate_tokens(text, "a.py", model_family="claude", model_id=SONNET)
    assert by_model == 4000            # fable's own p95 = 2.0
    assert by_family < by_model        # sonnet's p95 is the family's high end (4.0)


def test_samples_persist_and_learning_continues_after_restart(cache_path):
    cal = TokenCalibrator(cache_file=cache_path)
    _record(cal, FABLE, ".py", 3.1, n=7)
    cal._save_calibration_data()
    on_disk = json.loads(open(cache_path).read())
    assert on_disk["version"] == "1.1"
    assert len(on_disk["samples"][FABLE_KEY][".py"]) == 7
    assert all(len(p) == 2 for p in on_disk["samples"][FABLE_KEY][".py"]), "no paths on disk"

    cal2 = TokenCalibrator(cache_file=cache_path)
    assert cal2.stats_by_model_and_type[FABLE_KEY][".py"]["sample_count"] == 7
    _record(cal2, FABLE, ".py", 3.1)
    assert cal2.stats_by_model_and_type[FABLE_KEY][".py"]["sample_count"] == 8, \
        "a restart must not reset the window to the new process's samples"
    assert cal2.stats_by_model_and_type["claude"][".py"]["sample_count"] == 8


def test_legacy_stats_only_file_keeps_its_weight(cache_path):
    legacy = {
        "stats_by_model_and_type": {"claude": {".py": {
            "mean": 2.63, "median": 2.56, "p50": 2.56, "p95": 3.33, "p99": 4.19,
            "sample_count": 100, "min": 2.35, "max": 4.19}}},
        "global_by_model": {"claude": 2.56},
        "document_cache": {}, "global_fallback": 4.1,
        "baseline_overhead_tokens": {}, "baselines_measured": [], "baseline_tool_counts": {},
        "version": "1.0",
    }
    with open(cache_path, "w") as f:
        json.dump(legacy, f)
    cal = TokenCalibrator(cache_file=cache_path)

    # One 4.0 sample against 100 seeded at the 2.56 median barely moves the mean.
    _record(cal, SONNET, ".py", 4.0)
    stats = cal.stats_by_model_and_type["claude"][".py"]
    assert stats["sample_count"] == 100, "window is capped at 100"
    assert 2.55 < stats["mean"] < 2.60
    # The model bucket is new and reflects only its own sample.
    assert cal.stats_by_model_and_type["anthropic.claude-sonnet-4-20250514-v1:0"][".py"]["sample_count"] == 1


def test_directory_util_display_source_skips_multiplier_for_model_tier(cache_path, monkeypatch):
    """estimate_tokens_fast applies FILE_TYPE_MULTIPLIER only for non-type tiers;
    the new model tier must be treated as type-specific like 'learned_type'."""
    from app.utils import directory_util
    cal = TokenCalibrator(cache_file=cache_path)
    _record(cal, FABLE, ".json", 2.0)      # .json has a 1.2 multiplier
    import app.utils.token_calibrator as tc
    monkeypatch.setattr(tc, "get_token_calibrator", lambda: cal)
    monkeypatch.setattr(cal, "_get_current_model_key", lambda: FABLE_KEY)
    p = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)
    p.write("z" * 2000); p.close()
    try:
        assert directory_util.estimate_tokens_fast(p.name) == 1000  # 2000 / 2.0, no ×1.2
    finally:
        os.remove(p.name)
