"""Tributary-fold simulation in scripts/measure_context_composition.py.

design/tributary-fold.md slice 1.  The measurement is the evidence the fold
design rests on, so its classification and grace accounting are pinned
here against hand-built records in the persisted (rendered-fence) form.
"""
import importlib.util
from pathlib import Path

_PATH = Path(__file__).resolve().parent.parent / "scripts" / "measure_context_composition.py"
_spec = importlib.util.spec_from_file_location("measure_context_composition", _PATH)
mcc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mcc)

FENCE = "````"


def _block(cmd: str, body: str) -> str:
    return f"{FENCE}tool:run_shell_command|Shell: {cmd}|bash\n$ {cmd}\n{body}\n{FENCE}"


def _big(tag: str, n: int = 40) -> str:
    return "\n".join(f"{tag} line {i:03d} with enough distinctive text to matter here"
                     for i in range(n))


def _quest(final: str = "The limit is 1,000,000 (models_config.py:681).") -> str:
    return ("Let me check.\n\n"
            + _block("grep -n token_limit cfg.py", _big("grep"))
            + "\n\nTwo hits; narrowing.\n\n"
            + _block("sed -n 1,40p cfg.py", _big("sed"))
            + "\n\n" + final)


def _rec(*msgs):
    return {"id": "c1", "messages": [
        {"role": r, "content": c, **({"muted": True} if mute else {})}
        for r, c, mute in msgs]}


def H(c="q", mute=False):
    return ("human", c, mute)


def A(c="ok", mute=False):
    return ("assistant", c, mute)


def test_quest_turn_classified_and_split():
    info = mcc.classify_turn(_quest())
    assert info["kind"] == "quest"
    assert info["n_calls"] == 2
    assert info["labels"] == ["Shell: grep -n token_limit cfg.py", "Shell: sed -n 1,40p cfg.py"]
    # interim prose is inside the span; prose0 and final are outside it
    assert "narrowing" in info["span"]
    assert "Let me check" not in info["span"]
    assert "1,000,000" not in info["span"]
    assert info["interim_chars"] > 0 and info["block_chars"] > info["interim_chars"]


def test_exempt_kinds():
    single = "Reading.\n\n" + _block("cat f.py", _big("cat")) + "\n\nIt defines X."
    assert mcc.classify_turn(single)["kind"] == "single"
    ends = _quest(final="")
    assert mcc.classify_turn(ends)["kind"] == "ends_on_block"
    small = "a\n" + _block("ls", "x") + "\nb\n" + _block("pwd", "/") + "\nanswer"
    assert mcc.classify_turn(small)["kind"] == "small"
    assert mcc.classify_turn("plain prose, no tools") is None


def test_grace_controls_when_savings_apply():
    q = _quest()
    rec = _rec(H(), A(q), H(), A(), H(), A())
    f = mcc.simulate_fold(rec, graces=(1, 2, 3))
    assert f["n_quests"] == 1
    saved = {g: f["per_grace"][g]["steady_saved_chars"] for g in ("1", "2", "3")}
    # Two humans follow the quest: grace 1 and 2 fold it, grace 3 does not.
    assert saved["1"] == saved["2"] > 0
    assert saved["3"] == 0
    # Savings are net of the placeholder, which lists the calls.
    info = mcc.classify_turn(q)
    assert saved["2"] < info["span_chars"]


def test_muted_human_turn_does_not_count_toward_grace():
    rec = _rec(H(), A(_quest()), H(), A(), H(mute=True), A())
    f = mcc.simulate_fold(rec, graces=(2,))
    assert f["per_grace"]["2"]["steady_saved_chars"] == 0


def test_fold_moves_threshold_crossing_later(monkeypatch):
    # Each quest is ~1.2K tokens; 4K is crossed at the 4th unfolded quest,
    # late enough that earlier quests have passed their grace.
    monkeypatch.setattr(mcc, "THRESHOLDS_TOKENS", (4_000,))
    msgs = []
    for _ in range(6):
        msgs += [H(), A(_quest())]
    rec = _rec(*msgs)
    f = mcc.simulate_fold(rec, graces=(2,))
    no_fold = mcc.simulate_fold(rec, graces=(99,))
    with_x = f["per_grace"]["2"]["crossings"]["4000"]
    without_x = no_fold["per_grace"]["99"]["crossings"]["4000"]
    assert without_x is not None
    assert with_x is None or with_x > without_x


def test_reuse_proxy_buckets_by_elapsed_turns():
    q = _quest()
    quoted = "grep line 007 with enough distinctive text to matter here"
    rec = _rec(H(), A(q), H(), A("unrelated"), H(), A(f"as seen: {quoted}"))
    f = mcc.simulate_fold(rec, graces=(2,))
    assert f["reuse"].get("2") == 1
    assert f["reuse"].get("1") is None
    assert f["reuse"].get("min_2") == 1


def test_reuse_ignores_lines_the_reward_keeps():
    quoted = "sed line 003 with enough distinctive text to matter here"
    q = _quest(final=f"Answer: {quoted}")
    rec = _rec(H(), A(q), H(), A(f"again {quoted}"), H(), A())
    f = mcc.simulate_fold(rec, graces=(2,))
    assert not f["reuse"]
