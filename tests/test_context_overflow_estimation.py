"""The 2026-09-28 overflow: a 2.04M-char payload estimated at 509,517 tokens
while Bedrock rejected it at 1,017,040 (conv 793a72aa).

Two seams failed at once, and both used ``chars // 4``:

  - record_failure (context_debug) reported the failed call at half its real
    size, so the debug panel disagreed with the error text sitting next to it;
  - estimate_messages_tokens (handoff_prelude) read the same payload as 51% of
    a 1M limit, so the 70% handoff nudge never fired on any of the turns that
    grew the conversation past the ceiling.

The learned ratio for this model family is ~2.4 chars/token on line-numbered
code, and the tool schema baseline (~168k) is not in the messages at all.
Pinned here: the provider's own count from the rejection text wins when it is
present; otherwise the estimate goes through the calibrator; and the nudge
fires on the real payload shape.
"""
import pytest

from app.utils import context_debug, handoff_prelude
from app.utils.token_calibrator import TokenCalibrator

# Character totals from the captured failure frame
# (~/.ziya/debug/context_payloads/793a72aa.../legacy/iter_0000.json).
SYSTEM_CHARS = 1_155_630
CONV_CHARS = 882_438
REJECTION = ("An error occurred (ValidationException) when calling the "
             "InvokeModelWithResponseStream operation: prompt is too long: "
             "1017040 tokens > 1000000 maximum")


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setattr("app.utils.paths.get_ziya_home", lambda: tmp_path)
    monkeypatch.delenv("ZIYA_CONTEXT_DEBUG", raising=False)
    with context_debug._lock:
        context_debug._history.clear()
    context_debug._enabled_cache = None
    handoff_prelude.clear_pressure_cache()
    yield
    with context_debug._lock:
        context_debug._history.clear()
    handoff_prelude.clear_pressure_cache()


@pytest.fixture
def calibrated(tmp_path, monkeypatch):
    """A calibrator carrying the live store's learned values for 'claude'."""
    cal = TokenCalibrator(cache_file=str(tmp_path / "cal.json"))
    cal.global_by_model["claude"] = 2.3954
    cal.baseline_overhead_tokens["claude"] = 168_477
    monkeypatch.setattr(cal, "_get_current_model_family", lambda: "claude")
    import app.utils.token_calibrator as tc
    monkeypatch.setattr(tc, "get_token_calibrator", lambda: cal)
    return cal


def test_parse_provider_rejection():
    assert context_debug.parse_provider_token_rejection(REJECTION) == (1_017_040, 1_000_000)
    assert context_debug.parse_provider_token_rejection(
        "Input is too long: 1,234 tokens > 1,000 maximum") == (1_234, 1_000)
    # Other providers carry no count.
    assert context_debug.parse_provider_token_rejection("Input is too long for requested model") is None
    assert context_debug.parse_provider_token_rejection(None) is None


def test_calibrated_estimate_is_in_range_of_the_real_count(calibrated):
    est = context_debug.estimate_payload_tokens(SYSTEM_CHARS + CONV_CHARS)
    # chars/4 gave 509,517; the provider counted 1,017,040.
    assert est > 900_000, est
    assert abs(est - 1_017_040) / 1_017_040 < 0.15
    # Positive control: the ratio actually came from the calibrator.
    assert est == int((SYSTEM_CHARS + CONV_CHARS) / 2.3954) + 168_477


def test_estimate_falls_back_to_chars_over_4_without_calibrator(monkeypatch):
    import app.utils.token_calibrator as tc
    def _boom():
        raise RuntimeError("no calibrator")
    monkeypatch.setattr(tc, "get_token_calibrator", _boom)
    assert context_debug.estimate_payload_tokens(4000) == 1000


def test_failure_record_uses_the_providers_number(calibrated):
    context_debug.record_failure(
        "conv-793a", 0, error_message=REJECTION,
        system_content="s" * SYSTEM_CHARS,
        conversation=[{"role": "user", "content": "c" * CONV_CHARS}],
        origin="custom_bedrock",
    )
    e = context_debug.get_history("conv-793a")[0]
    assert e["total_input_tokens"] == 1_017_040
    assert e["is_estimated"] is False
    assert e["effective_limit"] == 1_000_000   # taken from the rejection text
    assert e["pct_of_limit"] == 101.7
    # The estimate is still recorded beside it, and no longer half the truth.
    assert e["estimated_tokens"] > 900_000


def test_failure_record_without_a_count_is_a_calibrated_estimate(calibrated):
    context_debug.record_failure(
        "conv-noc", 0, error_message="Input is too long for requested model",
        system_content="s" * SYSTEM_CHARS,
        conversation=[{"role": "user", "content": "c" * CONV_CHARS}],
    )
    e = context_debug.get_history("conv-noc")[0]
    assert e["is_estimated"] is True
    assert e["total_input_tokens"] == e["estimated_tokens"] > 900_000


def test_handoff_nudge_fires_on_the_real_payload(calibrated):
    messages = [{"role": "system", "content": "s" * SYSTEM_CHARS},
                {"role": "user", "content": [{"type": "text", "text": "c" * CONV_CHARS}]}]
    tokens = handoff_prelude.estimate_messages_tokens(messages)
    handoff_prelude.record_context_pressure("conv-793a", tokens, 1_000_000)
    pressure = handoff_prelude.get_context_pressure("conv-793a")
    assert pressure >= handoff_prelude.nudge_ratio(), pressure
    # And with chars/4 it would not have (the bug): 509k / 1M = 0.51.
    assert (SYSTEM_CHARS + CONV_CHARS) // 4 / 1_000_000 < handoff_prelude.nudge_ratio()
