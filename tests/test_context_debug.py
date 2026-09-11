"""
Tests for app.utils.context_debug — the per-iteration input-token snapshot
store backing the "Submitted context (debug)" panel.

These tests exercise the actual bug this module was built to make visible:
a conversation's persisted chat record can look tiny while an in-flight
agentic turn's actual provider-reported token usage balloons far beyond it,
with nothing before this module recording that growth anywhere.

Capture tiers under test:
  * in-memory recording is ALWAYS on (cheap; the numbers are already in
    hand from the provider event);
  * disk persistence (and, at the executor call site, payload char
    counting) only runs when detailed capture is enabled via
    set_enabled(), the persisted config flag, or ZIYA_CONTEXT_DEBUG.
"""
import json

import pytest

from app.utils import context_debug


@pytest.fixture(autouse=True)
def _isolated_ziya_home(tmp_path, monkeypatch):
    """Redirect get_ziya_home() so tests never touch the real ~/.ziya, and
    clear module-level state (ring buffer + cached enabled flag) between
    tests.  Also guarantees the env override is unset so tests exercise
    the config-file path deterministically."""
    monkeypatch.setattr("app.utils.paths.get_ziya_home", lambda: tmp_path)
    monkeypatch.delenv("ZIYA_CONTEXT_DEBUG", raising=False)
    with context_debug._lock:
        context_debug._history.clear()
    context_debug._enabled_cache = None
    yield
    with context_debug._lock:
        context_debug._history.clear()
    context_debug._enabled_cache = None


def test_record_and_retrieve_single_iteration():
    context_debug.record_iteration(
        "conv-1", 0,
        fresh_tokens=1000, cache_read_tokens=0, cache_write_tokens=500,
        total_input_tokens=1000, effective_limit=200000,
        system_chars=4000, conversation_chars=2000, message_count=3,
    )
    history = context_debug.get_history("conv-1")
    assert len(history) == 1
    assert history[0]["iteration"] == 0
    assert history[0]["total_input_tokens"] == 1000
    assert history[0]["pct_of_limit"] == 0.5


def test_growth_across_iterations_is_all_recorded():
    """The exact scenario this module exists for: a turn's actual submitted
    size grows far past what any turn-start estimate would show, entirely
    within iterations that never get persisted to the chat record."""
    totals = [5_000, 40_000, 250_000, 1_050_000]
    for i, total in enumerate(totals):
        context_debug.record_iteration(
            "conv-grows", i,
            fresh_tokens=total, cache_read_tokens=0, cache_write_tokens=0,
            total_input_tokens=total, effective_limit=1_000_000,
        )
    history = context_debug.get_history("conv-grows")
    assert [h["total_input_tokens"] for h in history] == totals
    # The final iteration is the one that would actually fail against a
    # 1M-token ceiling, and only the debug history captured it — nothing
    # about iteration 0 (5,000 tokens) would have predicted it.
    assert history[0]["total_input_tokens"] < 100_000
    assert history[-1]["total_input_tokens"] > 1_000_000


def test_missing_conversation_id_is_a_noop():
    # Must never raise, and must not create a spurious entry.
    context_debug.record_iteration(None, 0, total_input_tokens=999)
    assert context_debug.get_history("") == []
    assert context_debug.get_history(None) == []  # type: ignore[arg-type]


def test_unknown_conversation_returns_empty_list():
    assert context_debug.get_history("never-seen") == []


def test_per_conversation_ring_buffer_caps_length():
    for i in range(context_debug._MAX_ITERATIONS_PER_CONVERSATION + 50):
        context_debug.record_iteration("conv-long", i, total_input_tokens=i)
    history = context_debug.get_history("conv-long")
    assert len(history) == context_debug._MAX_ITERATIONS_PER_CONVERSATION
    # Oldest entries were evicted; the buffer holds the most recent ones.
    assert history[-1]["iteration"] == context_debug._MAX_ITERATIONS_PER_CONVERSATION + 49


# ── Detailed-capture toggle ──────────────────────────────────────────────

def test_disabled_by_default_records_in_memory_but_not_disk(tmp_path):
    """The always-on tier must work with capture off — and write nothing."""
    assert context_debug.is_enabled() is False
    context_debug.record_iteration("conv-mem", 0, total_input_tokens=42)
    # In-memory history is present…
    assert context_debug.get_history("conv-mem")[0]["total_input_tokens"] == 42
    # …but no snapshot file was written.
    path = tmp_path / "debug" / "context_snapshots" / "conv-mem.json"
    assert not path.exists()


def test_set_enabled_persists_across_cache_reset(tmp_path):
    context_debug.set_enabled(True)
    assert context_debug.is_enabled() is True
    # Simulate a fresh process: the cached flag is gone, so is_enabled()
    # must re-load the persisted config file.
    context_debug._enabled_cache = None
    assert context_debug.is_enabled() is True
    context_debug.set_enabled(False)
    context_debug._enabled_cache = None
    assert context_debug.is_enabled() is False


def test_env_var_overrides_config(monkeypatch):
    context_debug.set_enabled(False)
    monkeypatch.setenv("ZIYA_CONTEXT_DEBUG", "1")
    assert context_debug.is_enabled() is True
    monkeypatch.setenv("ZIYA_CONTEXT_DEBUG", "0")
    assert context_debug.is_enabled() is False
    monkeypatch.setenv("ZIYA_CONTEXT_DEBUG", "false")
    assert context_debug.is_enabled() is False


def test_persists_to_disk_and_survives_memory_eviction(tmp_path):
    context_debug.set_enabled(True)
    context_debug.record_iteration("conv-disk", 0, total_input_tokens=42)
    path = tmp_path / "debug" / "context_snapshots" / "conv-disk.json"
    assert path.exists()
    on_disk = json.loads(path.read_text())
    assert on_disk["conversation_id"] == "conv-disk"
    assert on_disk["iterations"][0]["total_input_tokens"] == 42

    # Simulate the in-memory copy being gone (server restart / LRU eviction)
    # — get_history must fall back to the disk snapshot rather than
    # returning an empty list.
    with context_debug._lock:
        context_debug._history.pop("conv-disk", None)
    history = context_debug.get_history("conv-disk")
    assert len(history) == 1
    assert history[0]["total_input_tokens"] == 42


def test_enabling_mid_conversation_persists_full_ring_buffer(tmp_path):
    """Turning capture on after a few iterations must not lose the earlier
    (in-memory) entries: the next persisted snapshot carries the whole
    ring buffer, so the growth curve stays complete."""
    context_debug.record_iteration("conv-mid", 0, total_input_tokens=100)
    context_debug.record_iteration("conv-mid", 1, total_input_tokens=200)
    context_debug.set_enabled(True)
    context_debug.record_iteration("conv-mid", 2, total_input_tokens=300)
    path = tmp_path / "debug" / "context_snapshots" / "conv-mid.json"
    on_disk = json.loads(path.read_text())
    assert [e["total_input_tokens"] for e in on_disk["iterations"]] == [100, 200, 300]


def test_clear_history_removes_memory_and_disk(tmp_path):
    context_debug.set_enabled(True)
    context_debug.record_iteration("conv-clear", 0, total_input_tokens=1)
    path = tmp_path / "debug" / "context_snapshots" / "conv-clear.json"
    assert path.exists()
    context_debug.clear_history("conv-clear")
    assert context_debug.get_history("conv-clear") == []
    assert not path.exists()


def test_record_iteration_never_raises_on_bad_inputs():
    """A diagnostics module must never be able to break a live model turn."""
    # Non-serializable-looking values, wrong types — must not raise.
    context_debug.record_iteration("conv-bad", 0, total_input_tokens="not-an-int")  # type: ignore[arg-type]


# ── record_failure: the retry-with-too-many-tokens regression ───────────
#
# A provider's "prompt is too long" ValidationException is raised
# synchronously by the HTTP client BEFORE any streaming response begins,
# so no UsageEvent is ever produced and record_iteration (called only
# from inside _handle_usage_event) never fires for that attempt. This is
# exactly why the debug panel showed nothing for a retry that failed on
# "too many tokens" while working fine for an ordinary successful turn.

def test_record_failure_creates_an_estimated_entry(tmp_path):
    conversation = [
        {"role": "user", "content": "a" * 4000},
        {"role": "assistant", "content": "b" * 4000},
    ]
    context_debug.record_failure(
        "conv-fail", 3,
        error_message="ValidationException: prompt is too long: 1122554 tokens > 1000000 maximum",
        system_content="s" * 2000,
        conversation=conversation,
        effective_limit=1_000_000,
    )
    history = context_debug.get_history("conv-fail")
    assert len(history) == 1
    entry = history[0]
    assert entry["is_failure"] is True
    assert entry["is_estimated"] is True
    # chars: system 2000 + conversation 8000 = 10000 -> //4 = 2500
    assert entry["estimated_tokens"] == 2500
    assert entry["total_input_tokens"] == 2500
    assert "prompt is too long" in entry["note"]
    assert entry["message_count"] == 2


def test_record_failure_persists_to_disk_even_when_capture_disabled(tmp_path):
    """This is the whole point: the badge/actual mismatch is discovered
    AFTER the failure, often after restarting to investigate, so a
    failure snapshot that only lived in memory (gated by the same
    detailed-capture toggle as ordinary iterations) would already be
    gone by the time anyone goes looking. record_failure must persist
    unconditionally."""
    assert context_debug.is_enabled() is False
    context_debug.record_failure(
        "conv-fail-disk", 0,
        error_message="prompt is too long",
        system_content="x" * 400,
        conversation=[{"role": "user", "content": "y" * 400}],
    )
    path = tmp_path / "debug" / "context_snapshots" / "conv-fail-disk.json"
    assert path.exists()
    on_disk = json.loads(path.read_text())
    assert on_disk["iterations"][0]["is_failure"] is True


def test_record_failure_survives_memory_eviction_via_disk(tmp_path):
    context_debug.record_failure(
        "conv-fail-restart", 0,
        error_message="prompt is too long",
        system_content="", conversation=[],
    )
    with context_debug._lock:
        context_debug._history.pop("conv-fail-restart", None)
    history = context_debug.get_history("conv-fail-restart")
    assert len(history) == 1
    assert history[0]["is_failure"] is True


def test_record_failure_alongside_prior_successful_iterations():
    """The realistic sequence: a turn runs several successful iterations,
    then a later retry within the same conversation fails outright. Both
    kinds of entry must coexist in one conversation's history, and the
    failure must be distinguishable from the real provider-reported
    entries that preceded it."""
    context_debug.record_iteration(
        "conv-mixed", 0, total_input_tokens=5_000, effective_limit=1_000_000,
    )
    context_debug.record_iteration(
        "conv-mixed", 1, total_input_tokens=40_000, effective_limit=1_000_000,
    )
    context_debug.record_failure(
        "conv-mixed", 2,
        error_message="prompt is too long: 1122554 tokens > 1000000 maximum",
        system_content="s" * 4_400_000, conversation=[],
        effective_limit=1_000_000,
    )
    history = context_debug.get_history("conv-mixed")
    assert len(history) == 3
    assert [e["is_failure"] for e in history] == [False, False, True]
    # The failure's estimate is in the right ballpark of the real
    # >1,000,000-token failure this whole module exists to explain.
    assert history[-1]["estimated_tokens"] > 1_000_000


def test_record_failure_missing_conversation_id_is_a_noop():
    context_debug.record_failure(
        None, 0, error_message="prompt is too long",
    )
    assert context_debug.get_history("") == []


def test_record_failure_never_raises_on_bad_inputs():
    """Must survive garbage system_content/conversation shapes — this
    fires from an exception handler already mid-failure; it must never
    itself become a second exception."""
    context_debug.record_failure(
        "conv-fail-bad", 0,
        error_message="prompt is too long",
        system_content=12345,  # not str or list
        conversation="not-a-list",  # type: ignore[arg-type]
    )
    # No crash is the assertion; a best-effort entry may or may not exist.
