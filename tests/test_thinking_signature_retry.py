"""
A request rejected with "Invalid `signature` in `thinking` block" is retried
once with echoed thinking removed, instead of ending the turn (2026-11-13: a
turn died at iteration 15 after an earlier message was rewritten under a
signed thinking block).

Drives the real ``stream_with_tools`` loop with a scripted provider, using
the harness in tests/test_await_diff_apply.py.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(__file__))
import test_await_diff_apply as h  # noqa: E402

from app.providers.base import ErrorEvent, StreamEnd, TextDelta  # noqa: E402
from app.utils.thinking_signature import is_thinking_signature_error  # noqa: E402

SIG_ERR = (
    "An error occurred (ValidationException) when calling the "
    "InvokeModelWithResponseStream operation: messages.11.content.75: Invalid "
    "`signature` in `thinking` block. The block is bound to a different "
    "conversation. Content before this block differs from when it was created, "
    "first at `messages.36.content.1`.")


def _history():
    return [
        {"role": "user", "content": [{"type": "text", "text": "Go"}]},
        {"role": "assistant", "content": [
            {"type": "thinking", "thinking": "plan", "signature": "sig"},
            {"type": "text", "text": "Looking."}]},
        {"role": "user", "content": [{"type": "text", "text": "keep at it"}]},
    ]


def _thinking_blocks(conv):
    return [b for m in conv if isinstance(m.get("content"), list)
            for b in m["content"] if isinstance(b, dict) and b.get("type") == "thinking"]


def test_predicate_matches_the_api_message_only():
    assert is_thinking_signature_error(SIG_ERR)
    assert not is_thinking_signature_error("prompt is too long: 250000 tokens > 200000")
    assert not is_thinking_signature_error("thinking blocks cannot be modified")
    assert not is_thinking_signature_error(None)


@pytest.mark.asyncio
async def test_signature_error_is_retried_once_without_thinking():
    script = [[ErrorEvent(message=SIG_ERR)],
              [TextDelta(content="Recovered."), StreamEnd(stop_reason="end_turn")]]
    provider, events = await h._run(script, None, history=_history())
    assert len(provider.calls) == 2, "the turn ended on the signature error"
    assert len(_thinking_blocks(provider.calls[0])) == 1     # the fixture really sent one
    assert _thinking_blocks(provider.calls[1]) == []
    # The rest of the message is intact.
    assert any("Looking." in str(m.get("content")) for m in provider.calls[1])
    assert not [e for e in events if e.get("type") == "error"]
    assert "Recovered." in "".join(e.get("content", "") for e in events if e.get("type") == "text")


@pytest.mark.asyncio
async def test_a_second_signature_error_ends_the_turn_with_the_error():
    script = [[ErrorEvent(message=SIG_ERR)], [ErrorEvent(message=SIG_ERR)],
              [TextDelta(content="never"), StreamEnd(stop_reason="end_turn")]]
    provider, events = await h._run(script, None, history=_history())
    assert len(provider.calls) == 2
    errors = [e for e in events if e.get("type") == "error"]
    assert len(errors) == 1 and "signature" in str(errors[0].get("content"))


@pytest.mark.asyncio
async def test_other_non_retryable_errors_are_not_retried():
    script = [[ErrorEvent(message="prompt is too long")],
              [TextDelta(content="never"), StreamEnd(stop_reason="end_turn")]]
    provider, events = await h._run(script, None, history=_history())
    assert len(provider.calls) == 1
    assert [e for e in events if e.get("type") == "error"]


@pytest.mark.asyncio
async def test_signature_error_with_no_thinking_to_remove_is_not_retried():
    history = [{"role": "user", "content": [{"type": "text", "text": "Go"}]}]
    script = [[ErrorEvent(message=SIG_ERR)],
              [TextDelta(content="never"), StreamEnd(stop_reason="end_turn")]]
    provider, events = await h._run(script, None, history=history)
    assert len(provider.calls) == 1
    assert [e for e in events if e.get("type") == "error"]
