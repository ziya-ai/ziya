"""
Per-conversation extraction cursor: windows already extracted on an earlier
turn are not re-extracted on the next one.

Why: server.py dispatches ``run_post_conversation_extraction`` after every
stream with the FULL non-muted history, and ``_split_into_topic_windows`` is
prefix-stable (a boundary depends only on the messages before it).  So on a
200-turn conversation every turn re-ran triage plus ~25 Haiku windows whose
output was then discarded as duplicate by dedup — pure spend.  The cursor
keys each window by a hash of its stripped text, so an unchanged prefix
window is skipped and a window whose text changed (muting, or the trailing
window growing) is extracted again.

The seam test counts actual extraction calls across three successive turns
of one conversation.  It FAILS against code without the cursor: the second
run re-extracts both windows (2 calls instead of 0).
"""
import json

import pytest
from unittest.mock import patch


# ── unit: the cursor store ──────────────────────────────────────────

def test_window_key_is_stable_and_content_sensitive():
    from app.memory.extraction_cursor import window_key
    assert window_key("USER: a\n\nASSISTANT: b") == window_key("USER: a\n\nASSISTANT: b")
    assert window_key("USER: a") != window_key("USER: a ")


def test_round_trip_and_cap(tmp_path, monkeypatch):
    # conftest already redirects ZIYA_HOME; be explicit so this test cannot
    # ever write under the developer's real ~/.ziya.
    monkeypatch.setenv("ZIYA_HOME", str(tmp_path / "home"))
    from app.memory.extraction_cursor import (
        MAX_KEYS, load_extracted_windows, record_extracted_windows,
    )
    assert load_extracted_windows("c1") == set()
    record_extracted_windows("c1", ["k1", "k2"])
    record_extracted_windows("c1", ["k2", "k3"])          # idempotent merge
    assert load_extracted_windows("c1") == {"k1", "k2", "k3"}
    assert load_extracted_windows("c2") == set()           # per conversation
    record_extracted_windows("c1", [f"x{i}" for i in range(MAX_KEYS + 10)])
    kept = load_extracted_windows("c1")
    assert len(kept) == MAX_KEYS
    assert "k1" not in kept and f"x{MAX_KEYS + 9}" in kept  # oldest trimmed


def test_missing_conversation_id_is_a_noop(tmp_path, monkeypatch):
    monkeypatch.setenv("ZIYA_HOME", str(tmp_path / "home"))
    from app.memory.extraction_cursor import load_extracted_windows, record_extracted_windows
    record_extracted_windows(None, ["k"])
    assert load_extracted_windows(None) == set()
    assert not (tmp_path / "home" / "memory" / "extraction_cursors").exists()


# ── seam: run_post_conversation_extraction across turns ─────────────

def _exchange(i):
    # Every user turn carries a salience phrase so no window is gated out,
    # and enough text that each window clears strip_conversation's floor.
    return [
        {"role": "user",
         "content": f"Important: subsystem {i} uses credit-based flow control "
                    f"with a {i * 16} KB window and we decided to keep it that way."},
        {"role": "assistant", "content": f"Noted the flow-control design for subsystem {i}."},
    ]


@pytest.mark.asyncio
async def test_prior_turn_windows_are_not_re_extracted(tmp_path, monkeypatch):
    from app.memory.extractor import run_post_conversation_extraction, WINDOW_TURN_COUNT
    from app.storage.memory import MemoryStorage
    from app.storage.proposals import ProposalsStore

    monkeypatch.setenv("ZIYA_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("ZIYA_MEMORY_TRIAGE_DISABLED", "1")
    store = MemoryStorage(memory_dir=tmp_path / "home" / "memory")
    proposals = ProposalsStore(memory_dir=tmp_path / "home" / "memory")

    extraction_calls = {"n": 0}

    async def mock_call(category, system_prompt, user_message,
                        max_tokens=2048, temperature=0.2, **_):
        if category == "memory_comparison":
            return '{"action": "ADD"}'
        if category != "memory_extraction":
            return "[]"
        extraction_calls["n"] += 1
        return json.dumps([{
            "content": f"Window-derived fact {extraction_calls['n']} about flow control.",
            "layer": "architecture", "tags": ["flow"], "confidence": "high",
        }])

    patches = (
        patch("app.mcp.builtin_tools.is_builtin_category_enabled", return_value=True),
        patch("app.storage.memory.get_memory_storage", return_value=store),
        patch("app.storage.proposals.get_proposals_store", return_value=proposals),
        patch("app.services.model_resolver.call_service_model", side_effect=mock_call),
    )

    # Turn 1: two full windows (2 × WINDOW_TURN_COUNT human turns).
    msgs = []
    for i in range(2 * WINDOW_TURN_COUNT):
        msgs += _exchange(i)
    with patches[0], patches[1], patches[2], patches[3]:
        await run_post_conversation_extraction(msgs, "conv-cursor")
    assert extraction_calls["n"] == 2, "positive control: both windows extracted on first sight"

    # Turn 2: identical history re-dispatched (the server's per-stream
    # behaviour).  Nothing new → nothing to extract.
    extraction_calls["n"] = 0
    with patches[0], patches[1], patches[2], patches[3]:
        await run_post_conversation_extraction(msgs, "conv-cursor")
    assert extraction_calls["n"] == 0, (
        "already-extracted windows were re-extracted on the next turn"
    )

    # Turn 3: more exchanges.  16 human turns is exactly two full windows,
    # so new turns open a THIRD window rather than growing the second; the
    # two earlier windows hash unchanged and are skipped, and only the new
    # window is extracted.  Three exchanges, so the new window clears
    # strip_conversation's 200-char floor (one 1-turn exchange is ~160 chars
    # and is dropped before the cursor is consulted at all).
    extraction_calls["n"] = 0
    msgs3 = msgs + _exchange(97) + _exchange(98) + _exchange(99)
    with patches[0], patches[1], patches[2], patches[3]:
        await run_post_conversation_extraction(msgs3, "conv-cursor")
    assert extraction_calls["n"] == 1

    # A different conversation with the same text is NOT affected: the
    # cursor is per conversation, not global.
    extraction_calls["n"] = 0
    with patches[0], patches[1], patches[2], patches[3]:
        await run_post_conversation_extraction(msgs, "conv-other")
    assert extraction_calls["n"] == 2
