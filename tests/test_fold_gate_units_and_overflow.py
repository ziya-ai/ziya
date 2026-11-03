"""
Tributary-fold gate: savings and pressure in the same units, and a rejected
send recorded as pressure (mbcohn bug report, 2026-11-02).

Observed: a 1M-context conversation alternated every turn between ~970k
(nothing folded) and ~350k (everything folded), then wedged when the
unfolded payload crossed 1M -- Bedrock rejected it six times while the gate
read ~460k and folded nothing.  Two defects, both pinned here:

1. ``fold_pressure_for`` rebuilt the unfolded size as billed tokens plus
   ``saved_chars`` converted at the *learned* ratio, and ``plan_folds``
   deducted savings the same way.  The learned ratio was 13.4 chars/token
   (see test_token_calibrator_text_chars.py for why); the payload's real
   ratio was ~2.5.  Savings were undercounted 5x on the way in and the
   fold target overshot 5x on the way out.
2. A context-length rejection produces no usage event, so the pressure
   cache kept the pre-send estimate and every retry sent the same payload.

Fixture helpers are the tributary-fold suite's own (tests/test_tributary_fold.py).
"""
import re
from pathlib import Path

import pytest

from app.utils import handoff_prelude as hp
from app.utils import tool_history_rewrite as thr
from app.utils.tool_history_rewrite import rewrite_tool_history
from tests.test_tributary_fold import GATED, _quest, _two_quests, _n_folded, _texts

REAL = 2.5       # chars/token the payload is actually billed at
LEARNED = 13.4   # what the calibrator had learned (poisoned by image bytes)
ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def gate(monkeypatch):
    """Learned ratio wrong by 5x, no fixed overhead, clean caches."""
    monkeypatch.setattr(thr, "_chars_to_tokens", lambda c: int(c / LEARNED))
    monkeypatch.setattr(thr, "_baseline_tokens", lambda: 0)
    hp.clear_pressure_cache()
    thr.clear_fold_savings()
    yield
    hp.clear_pressure_cache()
    thr.clear_fold_savings()


def _spans():
    return [thr.classify_fold(_quest(final="first answer"), GATED),
            thr.classify_fold(_quest(final="second answer", prose0="Again."), GATED)]


def test_savings_are_converted_at_the_payloads_own_ratio(gate):
    """Turn N folds at 81%; the folded replay is billed smaller by the
    savings at the REAL ratio.  Turn N+1 must reconstruct ~the same unfolded
    size and keep both spans folded.  With savings added back at the learned
    13.4 the reconstruction lands under the threshold and everything
    un-folds -- the first half of the oscillation."""
    cid = "conv-units"
    hist = _two_quests()
    saved_chars = sum(thr.fold_saving_chars(s) for s in _spans())
    limit = 10 * saved_chars
    t_n = int(0.81 * limit)
    chars_n = int(t_n * REAL)

    hp.record_context_pressure(cid, t_n, limit, chars=chars_n)
    assert _n_folded(rewrite_tool_history(hist, elide_redundant=False, fold=GATED,
                                          conversation_id=cid)) == 2
    # What the provider bills for the folded replay, and its text size.
    t_folded = t_n - int(saved_chars / REAL)
    hp.record_context_pressure(cid, t_folded, limit, chars=chars_n - saved_chars)
    assert t_folded + int(saved_chars / LEARNED) < 0.8 * limit, \
        "fixture: the learned-ratio reconstruction is under the threshold"

    p = thr.fold_pressure_for(cid)
    assert p.chars_per_token == pytest.approx(REAL, rel=0.02)
    assert p.tokens == pytest.approx(t_n, rel=0.01), "unfolded size reconstructed in billed units"
    out = rewrite_tool_history(hist, elide_redundant=False, fold=GATED, conversation_id=cid)
    assert _n_folded(out) == 2, "spans stay folded; no un-fold on the turn after a fold"


def test_without_a_char_count_the_learned_ratio_is_the_fallback(gate):
    """A pressure record with no chars (older producer, overflow record)
    still works; it just cannot do better than the learned ratio."""
    cid = "conv-nochars"
    hp.record_context_pressure(cid, 810_000, 1_000_000)
    thr.record_fold_savings(cid, 134_000)
    p = thr.fold_pressure_for(cid)
    assert p.chars_per_token is None
    assert p.tokens == 810_000 + int(134_000 / LEARNED)


def test_plan_folds_deducts_savings_in_payload_units(gate):
    """The second half of the oscillation: with savings deducted at 13.4
    the planner thinks a fold saves a fifth of what it does, folds far past
    the low-water mark, and the next measurement comes back tiny."""
    hist = _two_quests()
    saving = [int(thr.fold_saving_chars(s) / REAL) for s in _spans()]
    limit = int(saving[0] / (thr.FOLD_PRESSURE_HYSTERESIS * 1.5))
    low_water = (GATED.pressure_ratio - thr.FOLD_PRESSURE_HYSTERESIS) * limit
    tokens = int(0.8 * limit) + 1
    assert tokens - saving[0] <= low_water, "fixture: one fold at REAL reaches low water"
    assert tokens - int(saving[0] * REAL / LEARNED) > low_water, \
        "fixture: one fold at LEARNED would not, so the buggy planner folds both"
    out = _texts(rewrite_tool_history(
        hist, elide_redundant=False, fold=GATED,
        pressure=thr.FoldPressure(tokens, limit, chars_per_token=REAL)))
    assert "tributary tb-" in out[0]
    assert "tributary" not in out[1], "second span left alone: target reached after one fold"


def test_implausible_payload_ratio_falls_back(gate):
    """Text chars against a bill dominated by image tokens gives a ratio
    no tokenizer has; believe the learned ratio instead."""
    cid = "conv-img"
    hp.record_context_pressure(cid, 900_000, 1_000_000, chars=200_000)   # 0.22 chars/token
    assert thr.fold_pressure_for(cid).chars_per_token is None
    hp.record_context_pressure(cid, 100_000, 1_000_000, chars=2_000_000)  # 20 chars/token
    assert thr.fold_pressure_for(cid).chars_per_token is None


def test_fixed_overhead_is_excluded_from_the_ratio(monkeypatch):
    hp.clear_pressure_cache()
    monkeypatch.setattr(thr, "_baseline_tokens", lambda: 160_000)
    cid = "conv-base"
    # 1,000,000 billed of which 160k is tool schema; 2.1M text chars.
    hp.record_context_pressure(cid, 1_000_000, 1_000_000, chars=2_100_000)
    assert thr.fold_pressure_for(cid).chars_per_token == pytest.approx(2.5)
    hp.clear_pressure_cache()


# --- a rejected send is pressure --------------------------------------------

def test_overflow_record_makes_the_next_send_fold(gate):
    cid = "conv-overflow"
    hp.record_context_pressure(cid, 460_000, 1_000_000)   # the pre-send estimate
    hist = _two_quests()
    assert _n_folded(rewrite_tool_history(hist, elide_redundant=False, fold=GATED,
                                          conversation_id=cid)) == 0
    recorded = hp.record_context_overflow(
        cid, "An error occurred (ValidationException) when calling the "
             "ConverseStream operation: prompt is too long: 1003527 tokens > 1000000 maximum",
        1_000_000)
    assert recorded == 1_003_527
    assert hp.get_context_pressure_detail(cid) == (1_003_527, 1_000_000)
    assert hp.get_context_pressure_chars(cid) is None
    p = thr.fold_pressure_for(cid)
    assert p.tokens >= p.limit
    assert _n_folded(rewrite_tool_history(hist, elide_redundant=False, fold=GATED,
                                          conversation_id=cid)) == 2


def test_overflow_without_a_count_records_the_limit(gate):
    cid = "conv-overflow-2"
    assert hp.record_context_overflow(cid, "Input is too long for requested model.", 200_000) == 200_000
    assert hp.get_context_pressure(cid) == 1.0
    assert hp.record_context_overflow(None, "prompt is too long: 5 tokens", 200_000) is None
    assert hp.record_context_overflow(cid, "x", None) is None


def test_overflow_record_replaces_a_stale_char_count(gate):
    cid = "conv-overflow-3"
    hp.record_context_pressure(cid, 460_000, 1_000_000, chars=1_150_000)
    hp.record_context_overflow(cid, "prompt is too long: 1,003,527 tokens > 1000000 maximum", 1_000_000)
    assert hp.get_context_pressure_chars(cid) is None, \
        "1.15M chars against 1.0M tokens would be a bogus 1.1 chars/token"


# --- seams: the producers that must feed the gate ------------------------------

def _src(rel):
    return (ROOT / rel).read_text()


def test_executor_records_the_rejection_as_pressure_on_both_surfaces():
    """Bedrock YIELDS a non-retryable ErrorEvent(CONTEXT_LIMIT); it never
    raises, so a record living only in the ``except`` handler never fires on
    the reporter's path.  Both surfaces must call the one helper."""
    src = _src("app/streaming_tool_executor.py")
    # The raise path.
    i = src.index('"prompt is too long" in error_str')
    assert "_record_overflow_pressure(conversation_id, error_str)" in src[i:i + 3000]
    # The ErrorEvent path: the non-retryable branch that surfaces the error.
    j = src.index("# Non-retryable (e.g. CONTEXT_LIMIT)")
    branch = src[j:j + 600]
    assert "ErrorType.CONTEXT_LIMIT" in branch
    assert "_record_overflow_pressure(conversation_id, stream_event.message)" in branch
    # And the helper itself does the recording.
    k = src.index("def _record_overflow_pressure(")
    assert "record_context_overflow(conversation_id, error_str, int(_ceiling))" in src[k:k + 2500]


def test_overflow_helper_records_the_providers_count_against_the_users_ceiling(gate):
    from unittest.mock import patch
    from app.streaming_tool_executor import StreamingToolExecutor
    exe = StreamingToolExecutor.__new__(StreamingToolExecutor)
    exe.model_config = {"token_limit": 1_000_000}
    cid = "conv-overflow-helper"
    hp.record_context_pressure(cid, 460_000, 1_000_000, chars=1_150_000)
    with patch("app.agents.models.ModelManager.get_model_settings", return_value={}):
        exe._record_overflow_pressure(cid, "prompt is too long: 1003527 tokens > 1000000 maximum")
    assert hp.get_context_pressure_detail(cid) == (1_003_527, 1_000_000)
    assert hp.get_context_pressure_chars(cid) is None
    # The user's Max Input Tokens setting is the ceiling, as everywhere else.
    with patch("app.agents.models.ModelManager.get_model_settings", return_value={"max_input_tokens": 200_000}):
        exe._record_overflow_pressure(cid, "Input is too long for requested model.")
    assert hp.get_context_pressure_detail(cid) == (200_000, 200_000)
    # No conversation: nothing recorded, nothing raised.
    before = dict(hp._pressure_cache)
    exe._record_overflow_pressure(None, "prompt is too long: 5 tokens")
    assert hp._pressure_cache == before


def test_executor_and_server_record_chars_with_tokens():
    exe = _src("app/streaming_tool_executor.py")
    m = re.search(r"record_context_pressure\(conversation_id, effective_input,.*?\n.*?\)", exe)
    assert m and "chars=" in m.group(0), "executor pressure record carries the payload's text chars"
    srv = _src("app/server.py")
    m = re.search(r"record_context_pressure\(conversation_id,\s*estimate_messages_tokens\(messages\),.*?\)\)\n", srv, re.S)
    assert m and "chars=" in m.group(0), "server pre-send record carries the payload's text chars"
