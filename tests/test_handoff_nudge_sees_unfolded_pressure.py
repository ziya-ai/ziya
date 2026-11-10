"""The handoff nudge must measure the UNFOLDED conversation, as the fold gate does.

Observed 2026-11-10: a conversation the frontend showed at over 1M tokens had
no handoff draft.  Two gates read one pressure cache in different units.  The
tributary fold (tool_history_rewrite.plan_folds) compares the unfolded size
(assembled tokens + the previous replay's fold savings) against 0.80 and then
folds the payload back down to 0.65 of the limit.  The nudge compared the
ASSEMBLED -- i.e. folded -- size against 0.90.  Past 0.80 every turn is folded
to ~0.65, so the nudge never fired and the drawer's "context N%" sat at the
folded figure.  ``get_handoff_pressure`` adds the savings back; the nudge and
``GET .../handoff`` read it.

FAILS against the version that read ``get_context_pressure``.
"""
import pytest

from app.utils import handoff_prelude as hp
from app.utils import tool_history_rewrite as thr
from tests.test_handoff_draft_prelude import storage, _write  # noqa: F401  (fixture)

CID = "folded-conv"
LIMIT = 1_000_000


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    monkeypatch.delenv("ZIYA_HANDOFF_NUDGE_RATIO", raising=False)
    hp.clear_pressure_cache()
    thr.clear_fold_savings()
    yield
    hp.clear_pressure_cache()
    thr.clear_fold_savings()


def _record_folded_turn(tokens: int, saved_chars: int):
    # 3 chars/token payload; chars recorded so savings convert at the
    # payload's own ratio (the fold gate's units), not a learned one.
    hp.record_context_pressure(CID, tokens, LIMIT, chars=tokens * 3)
    thr.record_fold_savings(CID, saved_chars)


def test_unfolded_pressure_adds_back_what_the_folds_saved():
    _record_folded_turn(650_000, saved_chars=0)
    assert hp.get_handoff_pressure(CID) == pytest.approx(0.65, abs=0.01)
    # Folds removed ~300k tokens' worth of text: the conversation is at ~0.95.
    _record_folded_turn(650_000, saved_chars=900_000)
    assert hp.get_context_pressure(CID) == pytest.approx(0.65, abs=0.01)   # folded, unchanged
    assert hp.get_handoff_pressure(CID) >= 0.9


def test_nudge_fires_on_a_folded_payload_that_is_really_over_the_threshold(storage):
    _write(storage, CID)
    _record_folded_turn(650_000, saved_chars=900_000)
    _, turn = hp.split_handoff_prelude(CID, storage=storage)
    assert "[Handoff draft — start one now]" in turn
    # Positive control for the gate itself: the same folded size with
    # nothing folded away is genuinely at 0.65 and must stay silent.
    thr.clear_fold_savings()
    _record_folded_turn(650_000, saved_chars=0)
    assert hp.split_handoff_prelude(CID, storage=storage) == ("", "")


def test_the_drawer_reads_the_same_number(monkeypatch, storage):
    """GET .../handoff reports what the nudge sees, so the drawer's figure
    and the model's instruction cannot disagree."""
    import app.api.handoff as api
    _write(storage, CID)
    _record_folded_turn(650_000, saved_chars=900_000)
    monkeypatch.setattr(api, "_resolve_source",
                        lambda pid, cid: (storage, pid, storage._read_json(storage._chat_file(cid))))
    out = api.get_handoff_state("P", CID)
    assert out["contextPressure"] >= 0.9
