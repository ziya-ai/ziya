"""
Non-prefill continuation branches: conversation shape and fence verdict.

Observed live 2026-10-19 (claude, supports_assistant_prefill=False).  A
complete 3,784-char answer containing one closed ```mermaid block ended on
a clean end_turn and was judged "open fence on clean stop"
(NO_PREFILL_BLOCK_CONTINUE, transitions=1).  The injected request then
carried the answer TWICE -- MSG_STRUCT showed the final assistant turn as
``assistant[text,text]`` -- and the model replied "The text was accidentally
printed twice".

Two defects, tested at the seam (the real executor driven by a scripted
provider, asserting on the prompt the provider actually receives):

1. Every inject branch (max_tokens / intent / block_continue) appended
   ``assistant_text`` after the iteration-end block had already appended
   it; the pre-send coalesce merged the pair into one turn with the text
   duplicated.
2. The open-fence verdict came from the incremental streaming tracker.
   The decision is about the COMPLETE text, so it is now re-derived in a
   single pass over it; the incremental state is only logged when the two
   disagree.  (The incremental divergence itself did not reproduce from
   the logged text under exhaustive local chunkings, so this pins the
   verdict rather than a root cause -- see FENCE_STATE_DIVERGENCE in the
   logs for the next occurrence.)
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, List
from unittest.mock import patch

import pytest

from app.providers.base import StreamEnd, TextDelta
from tests.test_mid_stream_resume import _env  # noqa: F401
from tests.test_mid_stream_resume import ScriptedProvider as _LiveRefProvider


class ScriptedProvider(_LiveRefProvider):
    """Snapshots each prompt: the executor passes its live ``conversation``
    list, which later iterations mutate in place."""

    async def stream_response(self, messages, system_content, tools, config):
        import copy
        snapshot = copy.deepcopy(messages)
        first = True
        # The executor breaks out of the stream at message_stop without
        # exhausting the generator, so swap the reference in on the FIRST
        # yield, not after the loop.
        async for ev in super().stream_response(messages, system_content, tools, config):
            if first:
                self.prompts[-1] = snapshot
                first = False
            yield ev

BT = "`"

# A response that genuinely ends inside a fence (block_continue is correct).
OPEN_FENCE_RESPONSE = (
    "Here is the change.\n\n"
    f"{BT*3}python\n"
    "def f():\n"
    "    return 1\n"
)

# A complete response with one closed viz block (block_continue is wrong).
CLOSED_RESPONSE = (
    "Short version: the fault is on the transmitting side.\n\n"
    "## His mechanism vs mine\n\n"
    f"{BT*3}mermaid\n"
    "flowchart LR\n"
    "  A1[\"stale entry<br/>never receives\"] --> B1[\"skipped write\"]\n"
    "  style A1 fill:#eda100\n"
    f"{BT*3}\n\n"
    "Why his wins on the evidence we already have:\n\n"
    "- **Per-face bimodality.** One face always bad, the other always good.\n\n"
    "Want me to post those as CR comments?"
)


def _make_executor(provider):
    from app.streaming_tool_executor import StreamingToolExecutor
    with patch("app.streaming_tool_executor.StreamingToolExecutor.__init__",
               lambda self, **kw: self.__dict__.update(
                   provider=provider,
                   bedrock=None,
                   model_id="mock-model",
                   model_config={"family": "claude", "token_limit": 200000,
                                 "max_output_tokens": 4096,
                                 "supports_extended_context": False,
                                 "supports_assistant_prefill": False,
                                 "name": "mock"},
                   temperature_override=None,
                   max_tokens_override=None,
               )):
        return StreamingToolExecutor()


def _deltas(text: str, n: int) -> List[Any]:
    return [TextDelta(content=text[i:i + n], index=0)
            for i in range(0, len(text), n)]


async def _run(provider) -> List[Dict[str, Any]]:
    ex = _make_executor(provider)
    messages = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "Explain the thing."},
    ]
    return [c async for c in ex.stream_with_tools(messages, tools=[])]


def _assistant_turns(prompt: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [m for m in prompt if m.get("role") == "assistant"]


def _text_of(msg: Dict[str, Any]) -> str:
    c = msg.get("content")
    if isinstance(c, str):
        return c
    return "".join(b.get("text", "") for b in c
                   if isinstance(b, dict) and b.get("type") == "text")


class TestBlockContinueConversationShape:

    @pytest.mark.asyncio
    async def test_block_continue_sends_the_partial_answer_exactly_once(self):
        """The positive path: a real open fence IS continued, and the
        continuation request holds the partial answer once, not twice."""
        provider = ScriptedProvider([
            _deltas(OPEN_FENCE_RESPONSE, 7) + [StreamEnd(stop_reason="end_turn")],
            [TextDelta(content=f"{BT*3}\n", index=0),
             StreamEnd(stop_reason="end_turn")],
        ])
        logging.disable(logging.CRITICAL)
        try:
            await _run(provider)
        finally:
            logging.disable(logging.NOTSET)

        # >= 2, not == 2: the continuation iteration starts a fresh tracker,
        # so the model's bare closer is read as an opener and a further
        # block_continue fires (pre-existing, tracked separately).  This
        # test is about the SHAPE of the first continuation request.
        assert provider.calls >= 2, "an unclosed fence must trigger block_continue"
        second = provider.prompts[1]
        # The injected system/user prompt is last; the assistant turn
        # directly before it is the partial answer.
        assert second[-1]["role"] == "user"
        assert "finish the code block" in str(second[-1]["content"])
        tail_assistants = [m for m in second[-3:] if m.get("role") == "assistant"]
        assert len(tail_assistants) == 1, (
            "partial answer appended twice before the continuation prompt: "
            f"{[m.get('role') for m in second[-3:]]}")
        assert _text_of(tail_assistants[0]).count("def f():") == 1
        # And no two consecutive assistant turns anywhere in the prompt,
        # so there is nothing for the pre-send coalesce to merge.
        roles = [m.get("role") for m in second]
        assert not any(a == b == "assistant" for a, b in zip(roles, roles[1:])), roles


class TestClosedFenceIsNotContinued:

    @pytest.mark.asyncio
    @pytest.mark.parametrize("n", [3, 7, 50])
    async def test_complete_answer_with_closed_viz_block_ends_the_turn(self, n):
        provider = ScriptedProvider([
            _deltas(CLOSED_RESPONSE, n) + [StreamEnd(stop_reason="end_turn")],
        ])
        logging.disable(logging.CRITICAL)
        try:
            events = await _run(provider)
        finally:
            logging.disable(logging.NOTSET)
        assert provider.calls == 1, "a closed fence must not trigger block_continue"
        streamed = "".join(e.get("content", "") for e in events
                           if e.get("type") == "text")
        assert streamed == CLOSED_RESPONSE

    @pytest.mark.asyncio
    async def test_full_text_verdict_overrides_a_wedged_incremental_tracker(self):
        """The 2026-10-19 shape: incremental state says in_block=True with
        transitions=1 on a complete response.  The decision must come from
        the complete text, and the divergence must be logged with the
        incremental state so it can be attributed next time."""
        from app.streaming_tool_executor import StreamingToolExecutor

        provider = ScriptedProvider([
            _deltas(CLOSED_RESPONSE, 7) + [StreamEnd(stop_reason="end_turn")],
        ])
        orig = StreamingToolExecutor._update_code_block_tracker
        stream_tracker: list = []

        def wedge(self, text, tracker):
            orig(self, text, tracker)
            # Emulate the live divergence on the STREAM's tracker only, keyed
            # by identity: the first dict fed during the stream is the
            # executor's per-iteration tracker.  The full-text recheck runs
            # afterwards on a fresh dict and must stay unaffected -- it
            # calls this same (patched) method, so a content-based condition
            # would wedge the recheck too.
            if not stream_tracker:
                stream_tracker.append(tracker)
            if tracker is stream_tracker[0] and tracker.get('fence_transitions', 0) >= 1:
                tracker['in_block'] = True
                tracker['block_type'] = None
                tracker['fence_transitions'] = 1

        # The executor's mode-aware logger clears propagation, so caplog
        # never sees its records; collect them with a handler on the logger.
        records: list = []

        class _Collect(logging.Handler):
            def emit(self, record):
                records.append(record)

        log = logging.getLogger("app.streaming_tool_executor")
        handler = _Collect()
        log.addHandler(handler)
        try:
            with patch.object(StreamingToolExecutor, "_update_code_block_tracker", wedge):
                await _run(provider)
        finally:
            log.removeHandler(handler)

        assert provider.calls == 1, (
            "incremental tracker reported an open fence on a complete "
            "response and the turn was continued anyway")
        div = [r for r in records if "FENCE_STATE_DIVERGENCE" in r.getMessage()]
        assert div, "divergence between incremental and full-text verdicts was not logged"
        msg = div[0].getMessage()
        assert "in_block=True" in msg and "'fence_transitions': 1" in msg


class TestFenceOpenInFullText:

    def test_helper_agrees_with_single_pass_tracker(self):
        from app.streaming_tool_executor import StreamingToolExecutor
        ex = StreamingToolExecutor.__new__(StreamingToolExecutor)
        assert ex._fence_open_in_full_text(CLOSED_RESPONSE) is False
        assert ex._fence_open_in_full_text(OPEN_FENCE_RESPONSE) is True
        assert ex._fence_open_in_full_text("") is False
        # A diff whose body patches a fenced file: the inner fences are
        # content and the diff's own closer closes it.
        diff = (
            f"{BT*3}diff\n"
            "--- a/README.md\n+++ b/README.md\n@@ -1,3 +1,3 @@\n"
            f" {BT*3}bash\n-pip install x\n+pip install y\n {BT*3}\n"
            f"{BT*3}\n"
        )
        assert ex._fence_open_in_full_text(diff) is False
