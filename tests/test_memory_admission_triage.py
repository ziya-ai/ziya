"""Admission-triage tests (stage: ADMISSION, design §1).

These cover the model-based triage gate that REPLACES the user-only regex
salience gate in ``run_post_conversation_extraction``:

  * triage NONE  -> extraction skipped with reason ``triage_none``
  * triage hints -> a conversation whose USER turns have ZERO regex salience
                    hits still reaches the extraction model path (the seam)
  * triage FAIL  -> falls back to the regex gate verbatim (skip when
                    user-salience==0, proceed otherwise; never unconditional)
  * the per-conversation extra-window cap (HINT_ADMITTED_WINDOW_CAP) binds
  * ZIYA_MEMORY_TRIAGE_DISABLED=1 restores the legacy regex behavior

Every test mocks ``call_service_model`` so no network/model call is made and
dispatches by ``system_prompt`` (TRIAGE_SYSTEM_PROMPT vs EXTRACTION_SYSTEM_PROMPT)
and by ``category`` (memory_comparison).  All extraction/lifecycle runs against
sandbox MemoryStorage/ProposalsStore under tmp_path — never ~/.ziya/memory.

Fails on the pre-change backup: the module-level names imported below
(triage_conversation, _window_matches_hint, _extract_json_object,
HINT_ADMITTED_WINDOW_CAP, TRIAGE_SYSTEM_PROMPT) do not exist there, so import
fails and every test errors.
"""
import json
from unittest.mock import patch

import pytest

from app.memory.extractor import (
    run_post_conversation_extraction,
    triage_conversation,
    _window_matches_hint,
    _extract_json_object,
    HINT_ADMITTED_WINDOW_CAP,
    TRIAGE_SYSTEM_PROMPT,
    EXTRACTION_SYSTEM_PROMPT,
)


# ── shared helpers ──────────────────────────────────────────────────

def _stores(tmp_path):
    from app.storage.memory import MemoryStorage
    from app.storage.proposals import ProposalsStore
    return (MemoryStorage(memory_dir=tmp_path / "memory"),
            ProposalsStore(memory_dir=tmp_path / "memory"))


def _make_dispatch(triage_response, extraction_candidates, flags):
    """Build a call_service_model side_effect.

    ``flags`` is a mutable dict updated in place with ``triage_calls`` and
    ``extraction_calls`` counts.
    """
    async def mock_call(category, system_prompt, user_message,
                        max_tokens=2048, temperature=0.2):
        if category == "memory_comparison":
            return '{"action": "ADD"}'
        if system_prompt == TRIAGE_SYSTEM_PROMPT:
            flags["triage_calls"] += 1
            return triage_response
        assert system_prompt == EXTRACTION_SYSTEM_PROMPT
        flags["extraction_calls"] += 1
        return json.dumps(extraction_candidates)
    return mock_call


async def _run(messages, mock_call, store, proposals, conv_id="conv-triage"):
    with patch("app.mcp.builtin_tools.is_builtin_category_enabled", return_value=True), \
         patch("app.storage.memory.get_memory_storage", return_value=store), \
         patch("app.storage.proposals.get_proposals_store", return_value=proposals), \
         patch("app.services.model_resolver.call_service_model", side_effect=mock_call):
        return await run_post_conversation_extraction(messages, conv_id)


# ── pure-unit tests (no orchestration) ──────────────────────────────

def test_extract_json_object_balanced():
    assert _extract_json_object('prefix {"candidates": []} trailing') == '{"candidates": []}'
    assert _extract_json_object('[1,2,3]') is None       # array, not object
    assert _extract_json_object('no json here') is None
    # nested braces walk correctly
    assert _extract_json_object('x {"a": {"b": 1}} y') == '{"a": {"b": 1}}'


@pytest.mark.asyncio
async def test_triage_parses_candidates_and_caps():
    resp = json.dumps({"candidates": [
        {"gist": f"durable fact {i}", "layer": "architecture", "quote": "q"}
        for i in range(20)
    ]})

    async def mock_call(category, system_prompt, user_message, **kw):
        return resp

    with patch("app.services.model_resolver.call_service_model", side_effect=mock_call):
        hints = await triage_conversation("some transcript with content")
    assert isinstance(hints, list)
    assert len(hints) == 8            # TRIAGE_MAX_HINTS cap
    assert all(set(h) >= {"gist", "layer", "quote"} for h in hints)


@pytest.mark.asyncio
async def test_triage_empty_candidates_returns_empty_list():
    async def mock_call(category, system_prompt, user_message, **kw):
        return '{"candidates": []}'
    with patch("app.services.model_resolver.call_service_model", side_effect=mock_call):
        hints = await triage_conversation("transcript")
    assert hints == []               # skip signal, not failure


@pytest.mark.asyncio
async def test_triage_bad_layer_coerced_to_domain_context():
    async def mock_call(category, system_prompt, user_message, **kw):
        return '{"candidates": [{"gist": "x", "layer": "bogus", "quote": ""}]}'
    with patch("app.services.model_resolver.call_service_model", side_effect=mock_call):
        hints = await triage_conversation("transcript")
    assert hints[0]["layer"] == "domain_context"


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", ["not json at all", "[1,2,3]", '{"nope": 1}', ""])
async def test_triage_malformed_returns_none(bad):
    async def mock_call(category, system_prompt, user_message, **kw):
        return bad
    with patch("app.services.model_resolver.call_service_model", side_effect=mock_call):
        hints = await triage_conversation("transcript")
    assert hints is None             # failure -> caller falls back to regex


@pytest.mark.asyncio
async def test_triage_exception_returns_none():
    async def mock_call(category, system_prompt, user_message, **kw):
        raise RuntimeError("model down")
    with patch("app.services.model_resolver.call_service_model", side_effect=mock_call):
        hints = await triage_conversation("transcript")
    assert hints is None


def test_window_matches_hint_token_and_quote():
    win = [
        {"role": "user", "content": "Can you show the widget dashboard layout?"},
        {"role": "assistant",
         "content": "The widget dashboard refresh interval is thirty seconds "
                    "via the frobnicator service."},
    ]
    # token overlap match
    assert _window_matches_hint(
        win, [{"gist": "widget dashboard refresh interval thirty seconds frobnicator",
               "layer": "architecture", "quote": ""}]) is True
    # single-token gist must NOT admit (min 2 matching tokens)
    assert _window_matches_hint(
        win, [{"gist": "widget", "layer": "architecture", "quote": ""}]) is False
    # unrelated gist does not match
    assert _window_matches_hint(
        win, [{"gist": "kafka broker replication factor quorum", "layer": "architecture",
               "quote": ""}]) is False
    # quote substring match
    assert _window_matches_hint(
        win, [{"gist": "z z", "layer": "architecture",
               "quote": "refresh interval is thirty seconds"}]) is True
    # no hints
    assert _window_matches_hint(win, None) is False
    assert _window_matches_hint(win, []) is False


# ── orchestration tests ─────────────────────────────────────────────

# Three user turns, ALL zero user-salience (verified against _SALIENCE_PATTERNS);
# the durable fact is stated by the ASSISTANT.  Padding keeps the stripped
# conversation and each window over the 200-char length gate.
_ZERO_SALIENCE_MESSAGES = [
    {"role": "user",
     "content": "Can you show me the widget dashboard layout in some detail, "
                "walking through what each region of the screen displays?"},
    {"role": "assistant",
     "content": "The widget dashboard renders its panels through the Grafana "
                "embed API, and the dashboard refresh interval is fixed at thirty "
                "seconds by the frobnicator service on port 8080."},
    {"role": "user",
     "content": "And where does the panel data itself come from behind the scenes?"},
    {"role": "assistant",
     "content": "Panel data for the widget dashboard is served by the frobnicator "
                "service, which aggregates the metrics and exposes them to the "
                "Grafana embed API for rendering at the thirty second cadence."},
    {"role": "user",
     "content": "Okay, that walkthrough of the widget dashboard is helpful, "
                "appreciate you laying out how the whole layout fits together."},
]

_HINT_JSON = json.dumps({"candidates": [
    {"gist": "widget dashboard refresh interval is thirty seconds via the frobnicator service",
     "layer": "architecture", "quote": "refresh interval is fixed at thirty seconds"},
]})


@pytest.mark.asyncio
async def test_triage_none_skips_with_new_reason(tmp_path):
    """Triage returns no candidates -> extraction skipped with reason
    'triage_none' and the extraction model is never called."""
    store, proposals = _stores(tmp_path)
    flags = {"triage_calls": 0, "extraction_calls": 0}
    mock = _make_dispatch('{"candidates": []}',
                          [{"content": "x", "layer": "decision", "tags": [], "confidence": "high"}],
                          flags)
    result = await _run(_ZERO_SALIENCE_MESSAGES, mock, store, proposals)
    assert result.get("skipped") is True
    assert result.get("reason") == "triage_none"
    assert flags["triage_calls"] == 1
    assert flags["extraction_calls"] == 0
    assert proposals.list_open() == []


@pytest.mark.asyncio
async def test_triage_hint_admits_zero_salience_conversation(tmp_path):
    """SEAM TEST: a conversation whose USER turns have ZERO regex salience
    hits is admitted by a triage hint and REACHES the extraction model
    path — the exact class the old regex gate silently zeroed out."""
    from app.memory.extractor import _count_salience_hits
    assert _count_salience_hits(_ZERO_SALIENCE_MESSAGES) == 0  # precondition

    store, proposals = _stores(tmp_path)
    flags = {"triage_calls": 0, "extraction_calls": 0}
    mock = _make_dispatch(
        _HINT_JSON,
        [{"content": "The widget dashboard refresh interval is thirty seconds, set by "
                     "the frobnicator service.", "layer": "architecture",
          "tags": ["widget", "dashboard"], "confidence": "high"}],
        flags)
    result = await _run(_ZERO_SALIENCE_MESSAGES, mock, store, proposals)

    assert flags["triage_calls"] == 1
    assert flags["extraction_calls"] >= 1, "extraction model path was NOT reached"
    assert result.get("extracted", 0) >= 1
    assert result.get("proposed", 0) >= 1


@pytest.mark.asyncio
async def test_triage_failure_falls_back_to_regex_skip(tmp_path):
    """Triage FAILS (unparseable) on a zero-salience conversation -> the
    regex fallback skips with a distinct reason, no extraction."""
    store, proposals = _stores(tmp_path)
    flags = {"triage_calls": 0, "extraction_calls": 0}
    mock = _make_dispatch("garbage not json",
                          [{"content": "x", "layer": "decision", "tags": [], "confidence": "high"}],
                          flags)
    result = await _run(_ZERO_SALIENCE_MESSAGES, mock, store, proposals)
    assert result.get("skipped") is True
    assert result.get("reason") == "no_salience_signal_triage_fallback"
    assert flags["extraction_calls"] == 0


@pytest.mark.asyncio
async def test_triage_failure_falls_back_to_regex_proceed(tmp_path):
    """Triage FAILS on a SALIENT conversation -> the regex fallback
    proceeds to extraction (without hints), matching legacy behavior."""
    store, proposals = _stores(tmp_path)
    salient = [
        {"role": "user", "content": "We decided to use CCSDS framing because IP overhead "
                                    "was too high for the onboard processor budget here."},
        {"role": "assistant", "content": "Understood, CCSDS it is for the space segment."},
        {"role": "user", "content": "Right, and we always cap the forward channel at 500 Mbps "
                                    "since the modem cannot sustain more than that reliably."},
        {"role": "assistant", "content": "Noted, 500 Mbps forward channel ceiling."},
        {"role": "user", "content": "Also we chose CCSDS specifically because the onboard "
                                    "processor cannot afford IP header overhead per packet."},
        {"role": "assistant", "content": "Right, CCSDS keeps the per-packet overhead low."},
    ]
    flags = {"triage_calls": 0, "extraction_calls": 0}
    mock = _make_dispatch("still not json",
                          [{"content": "Chose CCSDS framing over IP for the onboard processor.",
                            "layer": "decision", "tags": ["ccsds"], "confidence": "high"}],
                          flags)
    result = await _run(salient, mock, store, proposals, conv_id="conv-salient")
    assert flags["extraction_calls"] >= 1
    assert result.get("proposed", 0) >= 1


@pytest.mark.asyncio
async def test_extra_window_cap_binds(tmp_path):
    """Four non-salient, hint-matching windows -> only
    HINT_ADMITTED_WINDOW_CAP of them reach extraction."""
    assert HINT_ADMITTED_WINDOW_CAP == 2
    long_assist = ("The widget dashboard renders panels via the Grafana embed API and the "
                   "frobnicator service holds the refresh interval at thirty seconds, "
                   "aggregating panel metrics for the dashboard layout continuously.")
    # Topic-shift user phrases force window boundaries; all are zero-salience.
    messages = [
        {"role": "user", "content": "Can you show me the widget dashboard layout to begin with?"},
        {"role": "assistant", "content": long_assist},
        {"role": "user", "content": "Moving on to another area now, what about the widget panels?"},
        {"role": "assistant", "content": long_assist},
        {"role": "user", "content": "Switching to the panels topic here, and the widget refresh?"},
        {"role": "assistant", "content": long_assist},
        {"role": "user", "content": "On a different note about the widget layout, anything else?"},
        {"role": "assistant", "content": long_assist},
    ]
    from app.memory.extractor import _count_salience_hits, _split_into_topic_windows
    assert _count_salience_hits(messages) == 0
    assert len(_split_into_topic_windows(messages)) >= 3   # >2 windows so the cap can bind

    store, proposals = _stores(tmp_path)
    flags = {"triage_calls": 0, "extraction_calls": 0}
    mock = _make_dispatch(
        _HINT_JSON,
        [{"content": "The widget dashboard refresh interval is thirty seconds via the "
                     "frobnicator service.", "layer": "architecture",
          "tags": ["widget"], "confidence": "high"}],
        flags)
    await _run(messages, mock, store, proposals, conv_id="conv-cap")
    assert flags["extraction_calls"] == HINT_ADMITTED_WINDOW_CAP


@pytest.mark.asyncio
async def test_env_bypass_restores_legacy_regex_behavior(tmp_path, monkeypatch):
    """ZIYA_MEMORY_TRIAGE_DISABLED=1 -> no triage call; the legacy
    user-only regex gate governs: a zero-salience conversation is skipped
    with reason 'no_salience_signal' and the model is never called."""
    monkeypatch.setenv("ZIYA_MEMORY_TRIAGE_DISABLED", "1")
    store, proposals = _stores(tmp_path)
    flags = {"triage_calls": 0, "extraction_calls": 0}
    mock = _make_dispatch(_HINT_JSON,
                          [{"content": "x", "layer": "decision", "tags": [], "confidence": "high"}],
                          flags)
    result = await _run(_ZERO_SALIENCE_MESSAGES, mock, store, proposals, conv_id="conv-bypass")
    assert result.get("skipped") is True
    assert result.get("reason") == "no_salience_signal"
    assert flags["triage_calls"] == 0
    assert flags["extraction_calls"] == 0
