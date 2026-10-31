"""
Tributary fold (design/tributary-fold.md, slice 2): a multi-call "quest"
turn replays as its pre-hunt prose + placeholder + final prose once two
further human turns exist.  Assertions are on the outermost surface — the
history handed to the provider by rewrite_tool_history, and the message
list build_messages_for_streaming produces — not on the plan dict.
"""
import hashlib
import re

import pytest

from app.utils import tool_history_rewrite as thr
from app.utils.tool_history_rewrite import (
    FoldSettings, FOLD_HANDLE_PREFIX, rewrite_tool_history,
)

F4 = "`" * 4
FENCE_RE = re.compile(r"(?m)^`{3,}tool:")
# pressure_ratio=0: the structural tests below are about WHICH spans are
# eligible, not about the pressure gate (its own section further down).
ON = FoldSettings(enabled=True, pressure_ratio=0.0)


def _shell(cmd: str, body: str) -> str:
    return f"{F4}tool:run_shell_command|Shell: {cmd}|bash\n$ {cmd}\n{body}\n{F4}"


def _read(path: str, body: str) -> str:
    return f"{F4}tool:file_read|🔐 file read: {path}|python\n{body}\n{F4}"


def _big(tag: str, n: int = 40) -> str:
    return "\n".join(f"{tag} line {i:03d} some distinctive filler text here" for i in range(n))


PROSE0 = "Let me check the config."
FINAL = "The limit is 1,000,000 (models_config.py:681)."
LABELS = ["grep -n token_limit cfg.py", "sed -n 1,40p cfg.py", "wc -l cfg.py"]


def _quest(final: str = FINAL, prose0: str = PROSE0) -> str:
    return (f"{prose0}\n\n" + _shell(LABELS[0], _big("grep"))
            + "\n\nTwo hits; narrowing.\n\n" + _shell(LABELS[1], _big("sed"))
            + "\n\nChecking length.\n\n" + _shell(LABELS[2], "812 cfg.py")
            + ("\n\n" + final if final else ""))


def H(c="q"):
    return {"type": "human", "content": c}


def A(c):
    return {"type": "ai", "content": c}


def _texts(history):
    return [m["content"] for m in history if m.get("type") == "ai"]


# ---------------------------------------------------------------------------
# Structural rule, on the rewritten history
# ---------------------------------------------------------------------------

def test_quest_with_two_later_human_turns_folds_to_reward():
    hist = [H(), A(_quest()), H(), A("mid"), H(), A("later")]
    out = rewrite_tool_history(hist, elide_redundant=False, fold=ON)
    txt = _texts(out)[0]
    assert txt.startswith(PROSE0)
    assert txt.rstrip().endswith(FINAL)
    assert "Two hits; narrowing." not in txt      # interim prose gone
    assert "grep line 007" not in txt              # bodies gone
    assert not FENCE_RE.search(txt)
    assert "[tool_result" not in txt               # no envelopes rendered
    assert f"tributary {FOLD_HANDLE_PREFIX}" in txt
    assert "3 tool calls" in txt
    for lab in LABELS:
        assert f"Shell: {lab}" in txt              # the calls are listed
    assert 'recall_tributary(handle="tb-' in txt
    assert hist[1]["content"].startswith(PROSE0)   # input not mutated
    assert "grep line 007" in hist[1]["content"]


def test_one_later_human_turn_is_within_grace():
    hist = [H(), A(_quest()), H(), A("mid")]
    out = rewrite_tool_history(hist, elide_redundant=False, fold=ON)
    txt = _texts(out)[0]
    assert "tributary" not in txt
    assert "grep line 007" in txt                  # body still replayed
    assert "Two hits; narrowing." in txt
    assert not FENCE_RE.search(txt)                # but still re-encoded


def test_single_block_turn_is_exempt():
    single = "Reading.\n\n" + _read("f.py", _big("f")) + "\n\nIt defines X."
    hist = [H(), A(single), H(), A("a"), H(), A("b")]
    txt = _texts(rewrite_tool_history(hist, elide_redundant=False, fold=ON))[0]
    assert "tributary" not in txt
    assert "f line 007" in txt


def test_turn_ending_on_a_block_is_untouched():
    hist = [H(), A(_quest(final="")), H(), A("a"), H(), A("b")]
    txt = _texts(rewrite_tool_history(hist, elide_redundant=False, fold=ON))[0]
    assert "tributary" not in txt
    assert "812 cfg.py" in txt


def test_small_span_is_untouched():
    small = "a\n" + _shell("ls", "x") + "\nb\n" + _shell("pwd", "/") + "\nanswer"
    hist = [H(), A(small), H(), A("a"), H(), A("b")]
    txt = _texts(rewrite_tool_history(hist, elide_redundant=False, fold=ON))[0]
    assert "tributary" not in txt


def test_grace_counts_human_messages_in_history_only():
    # Muted messages never reach the history; an assistant-only tail does
    # not satisfy the grace.
    hist = [H(), A(_quest()), H(), A("a"), A("b"), A("c")]
    txt = _texts(rewrite_tool_history(hist, elide_redundant=False, fold=ON))[0]
    assert "tributary" not in txt


def test_pinned_message_never_folds():
    hist = [H(), {**A(_quest()), "foldPinned": True}, H(), A("a"), H(), A("b")]
    txt = _texts(rewrite_tool_history(hist, elide_redundant=False, fold=ON))[0]
    assert "tributary" not in txt
    assert "grep line 007" in txt


def test_disabled_and_kill_switch(monkeypatch):
    hist = [H(), A(_quest()), H(), A("a"), H(), A("b")]
    off = _texts(rewrite_tool_history(hist, elide_redundant=False, fold=False))[0]
    assert "tributary" not in off
    monkeypatch.setenv("ZIYA_DISABLE_TRIBUTARY_FOLD", "1")
    assert thr.resolve_fold_settings().enabled is False
    txt = _texts(rewrite_tool_history(hist, elide_redundant=False, fold=None))[0]
    assert "tributary" not in txt


def test_default_setting_is_on(monkeypatch):
    # Slice 3: recall_tributary shipped, so the fold defaults on.  The
    # module default is asserted unconditionally; the project-model field
    # is asserted once app/models/project.py carries it.
    monkeypatch.delenv("ZIYA_DISABLE_TRIBUTARY_FOLD", raising=False)
    assert FoldSettings().enabled is True
    # ...but gated: nothing folds until the unfolded replay is at 80% of
    # the limit.  Below pressure a fold removes verbatim text the model
    # had in view (diff context lines, exact paths) for no gain.
    assert FoldSettings().pressure_ratio == 0.8
    from app.models.project import ContextManagementSettings
    cm = ContextManagementSettings()
    if hasattr(cm, "fold_tributaries"):
        assert cm.fold_tributaries is True
    if hasattr(cm, "fold_pressure_ratio"):
        assert cm.fold_pressure_ratio == 0.8


# ---------------------------------------------------------------------------
# Pressure gate: fold only near the limit, oldest first, monotone
# ---------------------------------------------------------------------------

GATED = FoldSettings(enabled=True, pressure_ratio=0.8)
LIMIT = 100_000


def _two_quests():
    """Two eligible quest turns (both past the grace), oldest first."""
    return [H("q0"), A(_quest(final="first answer")), H("q1"),
            A(_quest(final="second answer", prose0="Again.")), H("q2"), A("a2"),
            H("q3"), A("a3")]


def _n_folded(history):
    return sum("tributary tb-" in t for t in _texts(history))


def test_gate_folds_nothing_below_pressure_and_nothing_when_unknown():
    hist = _two_quests()
    low = thr.FoldPressure(tokens=int(0.5 * LIMIT), limit=LIMIT)
    assert _n_folded(rewrite_tool_history(hist, elide_redundant=False, fold=GATED,
                                          pressure=low)) == 0
    # Unknown pressure (first turn after a restart, no conversation) is not
    # "full": the ungated planner would fold both; the gated one folds none.
    assert _n_folded(rewrite_tool_history(hist, elide_redundant=False, fold=GATED)) == 0
    assert _n_folded(rewrite_tool_history(hist, elide_redundant=False, fold=ON)) == 2
    # The reward prose is intact either way.
    assert "first answer" in _texts(rewrite_tool_history(hist, fold=GATED, pressure=low))[0]


def test_gate_at_threshold_folds_oldest_first_and_stops_at_low_water(monkeypatch):
    hist = _two_quests()
    # Make the saving arithmetic exact so the stopping point is testable:
    # chars/4 for deltas (the calibrator may be unavailable or learned).
    monkeypatch.setattr(thr, "_chars_to_tokens", lambda c: int(c / 4))
    spans = [thr.classify_fold(_quest(final="first answer"), GATED),
             thr.classify_fold(_quest(final="second answer", prose0="Again."), GATED)]
    saving = [thr.fold_saving_chars(s) // 4 for s in spans]
    assert all(s > 0 for s in saving)
    # A limit sized to the fixture: the hysteresis band (15% of it) holds
    # one span's saving but not two, so the stopping rule is observable.
    limit = int(saving[0] / (thr.FOLD_PRESSURE_HYSTERESIS * 1.5))
    low_water = (GATED.pressure_ratio - thr.FOLD_PRESSURE_HYSTERESIS) * limit
    # Just over the threshold, and one fold is enough to get under the low
    # water mark: only the OLDEST quest folds.
    tokens = int(0.8 * limit) + 1
    assert tokens - saving[0] <= low_water
    out = _texts(rewrite_tool_history(hist, elide_redundant=False, fold=GATED,
                                      pressure=thr.FoldPressure(tokens, limit)))
    assert "tributary tb-" in out[0] and out[0].rstrip().endswith("first answer")
    assert "tributary" not in out[1] and "grep line 007" in out[1]
    # Far enough over that one fold cannot get under: both fold.
    tokens = int(low_water) + saving[0] + 1
    out = _texts(rewrite_tool_history(hist, elide_redundant=False, fold=GATED,
                                      pressure=thr.FoldPressure(tokens, limit)))
    assert "tributary tb-" in out[0] and "tributary tb-" in out[1]


def test_gate_is_monotone_across_turns_via_recorded_savings(monkeypatch):
    """The gate compares the UNFOLDED size.  Without adding back what the
    previous turn's folds saved, a fold would drop the measured pressure,
    the next turn would un-fold, and the message would flap in and out of
    the cached prefix every turn."""
    from app.utils import handoff_prelude as hp
    monkeypatch.setattr(thr, "_chars_to_tokens", lambda c: int(c / 4))
    cid = "conv-fold-gate-mono"
    hp.clear_pressure_cache()
    thr.clear_fold_savings()
    hist = _two_quests()
    saved = sum(thr.fold_saving_chars(s) // 4 for s in (
        thr.classify_fold(_quest(final="first answer"), GATED),
        thr.classify_fold(_quest(final="second answer", prose0="Again."), GATED)))

    # Turn N: measured at 81% unfolded.  Neither span alone reaches the
    # low-water mark at this limit, so both fold.
    hp.record_context_pressure(cid, int(0.81 * LIMIT), LIMIT)
    out = rewrite_tool_history(hist, elide_redundant=False, fold=GATED, conversation_id=cid)
    assert _n_folded(out) == 2
    # The replay that was assembled is smaller by the savings; that is what
    # the next pressure measurement sees.
    hp.record_context_pressure(cid, int(0.81 * LIMIT) - saved, LIMIT)
    assert hp.get_context_pressure(cid) < 0.8, "measured (folded) pressure is under the threshold"
    # Turn N+1: the recorded savings restore the unfolded figure, so the
    # spans stay folded instead of un-folding (which a gate on the measured
    # figure alone would do, and then re-fold the turn after).
    assert thr.fold_pressure_for(cid) == thr.FoldPressure(int(0.81 * LIMIT), LIMIT)
    out2 = rewrite_tool_history(hist, elide_redundant=False, fold=GATED, conversation_id=cid)
    assert _texts(out2)[:2] == _texts(out)[:2], "placeholder bytes identical turn to turn"
    assert _n_folded(out2) == 2
    # Negative control: without the savings record the measured figure
    # alone would have un-folded everything.
    thr.clear_fold_savings()
    assert _n_folded(rewrite_tool_history(hist, elide_redundant=False, fold=GATED,
                                          conversation_id=cid)) == 0
    hp.clear_pressure_cache()
    thr.clear_fold_savings()


def test_fold_section_prompt_is_conditional_not_a_claim():
    """The Session Context paragraph must not assert that turns ARE folded:
    below pressure none are, and a false claim teaches the model to recall
    spans that are in view."""
    from app.utils.session_context_prompt import _format_fold_section
    import app.mcp.builtin_tools as bt
    from unittest.mock import patch
    with patch.object(thr, "resolve_fold_settings", lambda *a, **k: GATED), \
            patch.object(bt, "is_builtin_category_enabled", lambda c: True):
        text = "\n".join(_format_fold_section(None))
    assert text, "the section is emitted when the fold is on"
    assert "nears its context limit" in text
    assert "Some earlier tool-heavy turns in this history are replayed" not in text


# ---------------------------------------------------------------------------
# Handle and cache stability
# ---------------------------------------------------------------------------

def test_handle_is_sha1_of_exact_record_substring():
    q = _quest()
    blocks = thr.find_tool_blocks(q)
    span = q[blocks[0].start:blocks[-1].end]
    expect = FOLD_HANDLE_PREFIX + hashlib.sha1(span.encode()).hexdigest()[:10]
    fs = thr.classify_fold(q, ON)
    assert fs is not None and fs.handle == expect
    hist = [H(), A(q), H(), A("a"), H(), A("b")]
    assert expect in _texts(rewrite_tool_history(hist, elide_redundant=False, fold=ON))[0]


def test_placeholder_bytes_identical_at_n_plus_2_and_n_plus_9():
    q = _quest()
    short = [H(), A(q), H(), A("a"), H(), A("b")]
    long = [H(), A(q)] + [x for _ in range(9) for x in (H(), A("filler"))]
    t_short = _texts(rewrite_tool_history(short, elide_redundant=False, fold=ON))[0]
    t_long = _texts(rewrite_tool_history(long, elide_redundant=False, fold=ON))[0]
    assert t_short == t_long


def test_empty_results_and_interim_notes_are_counted():
    q = (PROSE0 + "\n\n" + _shell("grep nothing cfg.py", "")
         + "\n\nNothing; widening.\n\n" + _shell("grep token cfg.py", _big("g"))
         + "\n\n" + FINAL)
    fs = thr.classify_fold(q, ON)
    assert fs.n_empty == 1 and fs.n_notes == 1
    assert "(1 returned nothing), 1 interim notes" in fs.placeholder


def test_label_cap_lists_overflow_count():
    calls = "\n\n".join(_shell(f"cmd{i}", _big(f"c{i}", 5)) for i in range(15))
    q = PROSE0 + "\n\n" + calls + "\n\n" + FINAL
    fs = thr.classify_fold(q, FoldSettings(enabled=True, max_labels=12))
    assert fs.placeholder.count("   Shell: cmd") == 12
    assert "… 3 more" in fs.placeholder


# ---------------------------------------------------------------------------
# Ordering with Tier 0 (redundant-body elision)
# ---------------------------------------------------------------------------

def test_tier0_does_not_elide_a_body_whose_only_standin_is_folded():
    body = _big("shared", 60)
    early = "Reading.\n\n" + _read("x.py", body) + "\n\nSummary of x."
    # Later quest turn re-reads x.py inside its tributary.
    later = ("Checking.\n\n" + _read("x.py", body) + "\n\nSame file; also:\n\n"
             + _shell("wc -l x.py", "60 x.py") + "\n\n" + FINAL)
    hist = [H(), A(early), H(), A(later), H(), A("a"), H(), A("b")]

    # Without folding, Tier 0 elides the early read (the later copy is in view).
    no_fold = _texts(rewrite_tool_history(hist, elide_redundant=True, fold=False))
    assert "elided on replay" in no_fold[0]
    assert "shared line 007" in no_fold[1]

    # With folding, the later copy is inside a folded span, so the early
    # read must survive — otherwise the content is gone from the model's view.
    folded = _texts(rewrite_tool_history(hist, elide_redundant=True, fold=ON))
    assert "elided on replay" not in folded[0]
    assert "shared line 007" in folded[0]
    assert "tributary" in folded[1]
    assert "shared line 007" not in folded[1]
    assert sum("shared line 007" in t for t in folded) == 1


def test_list_content_segments_fold_independently():
    q = _quest()
    hist = [H(), {"type": "ai", "content": [
                {"type": "text", "text": "plain"},
                {"type": "text", "text": q}]},
            H(), A("a"), H(), A("b")]
    out = rewrite_tool_history(hist, elide_redundant=False, fold=ON)
    segs = out[1]["content"]
    assert segs[0]["text"] == "plain"
    assert "tributary" in segs[1]["text"] and segs[1]["text"].rstrip().endswith(FINAL)


# ---------------------------------------------------------------------------
# Server seam
# ---------------------------------------------------------------------------

def _assistant_texts(msgs):
    out = []
    for m in msgs:
        if m.get("role") != "assistant":
            continue
        c = m["content"]
        out.append(c if isinstance(c, str) else "".join(
            b.get("text", "") for b in c if isinstance(b, dict) and b.get("type") == "text"))
    return out


def test_build_messages_folds_when_project_setting_on(monkeypatch):
    from app.server import build_messages_for_streaming
    monkeypatch.setattr(thr, "resolve_fold_settings", lambda *a, **k: ON)
    hist = [H("q0"), A(_quest()), H("q1"), A("a1"), H("q2"), A("a2")]
    msgs = build_messages_for_streaming("q3", hist, [], "conv-fold-seam-1")
    texts = _assistant_texts(msgs)
    assert len(texts) == 3
    assert "tributary tb-" in texts[0]
    assert texts[0].rstrip().endswith(FINAL)
    assert "grep line 007" not in texts[0]
    assert not any(FENCE_RE.search(t) for t in texts)


def test_build_messages_gate_reads_this_conversations_pressure(monkeypatch):
    """Seam: build_messages_for_streaming hands the conversation id to the
    rewrite, so the gate sees the pressure recorded for THIS conversation —
    no fold for a cold conversation, a fold once its last turn measured
    over the threshold."""
    from app.server import build_messages_for_streaming
    from app.utils import handoff_prelude as hp
    monkeypatch.setattr(thr, "resolve_fold_settings", lambda *a, **k: GATED)
    hist = [H("q0"), A(_quest()), H("q1"), A("a1"), H("q2"), A("a2")]
    hp.clear_pressure_cache()
    thr.clear_fold_savings()
    cold = _assistant_texts(build_messages_for_streaming("q3", hist, [], "conv-fold-gate-cold"))
    assert "tributary" not in cold[0] and "grep line 007" in cold[0]
    # build_messages records its own (small) measurement; a hot reading for
    # another conversation must not leak either.
    hp.record_context_pressure("conv-fold-gate-other", 95_000, 100_000)
    hp.record_context_pressure("conv-fold-gate-hot", 95_000, 100_000)
    hot = _assistant_texts(build_messages_for_streaming("q3", hist, [], "conv-fold-gate-hot"))
    assert "tributary tb-" in hot[0] and "grep line 007" not in hot[0]
    still_cold = _assistant_texts(build_messages_for_streaming("q3", hist, [], "conv-fold-gate-cold2"))
    assert "tributary" not in still_cold[0]
    hp.clear_pressure_cache()
    thr.clear_fold_savings()


def test_build_messages_leaves_quest_alone_when_setting_off(monkeypatch):
    from app.server import build_messages_for_streaming
    monkeypatch.setattr(thr, "resolve_fold_settings",
                        lambda *a, **k: FoldSettings(enabled=False))
    hist = [H("q0"), A(_quest()), H("q1"), A("a1"), H("q2"), A("a2")]
    texts = _assistant_texts(build_messages_for_streaming("q3", hist, [], "conv-fold-seam-2"))
    assert "tributary" not in texts[0]
    assert "grep line 007" in texts[0]
