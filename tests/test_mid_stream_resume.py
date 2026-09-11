"""Mid-stream transient fault after partial text is RESUMED, not fatal.

Observed live (Anthropic direct, fable-5):

    WARNING  AnthropicDirectProvider: READ_TIMEOUT mid-stream after content
             was yielded — refusing duplicate-producing retry
    INFO     ERROR_CHUNK: {'type': 'api_error', 'message': 'Internal server error'}
    INFO     NON_RETRYABLE_END: provider error already surfaced, ending stream

Classification was correct (READ_TIMEOUT, retryable — locked by
test_anthropic_500_retryable.py) and the provider's refusal to retry was
correct too: a from-scratch retry APPENDS a second full response onto the
partial text the consumer already accumulated.  The defect was vocabulary —
"cannot retry HERE" was collapsed into the same ``retryable=False`` signal
used for a fatal 400, so the orchestrator killed the turn even though it
owns the accumulated text and can resume from it via assistant prefill.

Three layers are covered:
  1. ErrorEvent carries a distinct ``resumable`` state.
  2. Every provider with the content_yielded refusal sets it — and withholds
     it once a tool_use block has started (a text prefill cannot finish a
     half-emitted tool call).
  3. The seam: StreamingToolExecutor actually resumes instead of ending the
     turn, and still ends the turn when the error is genuinely fatal.

Layer 3 is the one that matters; layers 1-2 exist so a regression names
itself instead of surfacing as "the resume mysteriously stopped happening".
"""
from __future__ import annotations

import asyncio
import json
from typing import Any, AsyncGenerator, Dict, List, Optional
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.providers.base import (
    ErrorEvent,
    ErrorType,
    LLMProvider,
    ProviderConfig,
    StreamEnd,
    StreamEvent,
    TextDelta,
    ThinkingDelta,
    ToolUseEnd,
    ToolUseStart,
    UsageEvent,
)

# The exact live 500 body that started this.
LIVE_500 = ("{'type': 'error', 'error': {'details': None, 'type': 'api_error', "
            "'message': 'Internal server error'}, 'request_id': 'req_pp27rx'}")


# ---------------------------------------------------------------------------
# Layer 1 — the event vocabulary
# ---------------------------------------------------------------------------

class TestErrorEventResumableField:

    def test_resumable_defaults_false(self):
        """Existing construction sites must not silently become resumable."""
        ev = ErrorEvent(message="boom", error_type=ErrorType.CONTEXT_LIMIT)
        assert ev.resumable is False

    def test_resumable_is_independent_of_retryable(self):
        """resumable is a THIRD state, not an alias for either bool."""
        ev = ErrorEvent(message=LIVE_500, error_type=ErrorType.READ_TIMEOUT,
                        retryable=False, resumable=True)
        assert (ev.retryable, ev.resumable) == (False, True)


# ---------------------------------------------------------------------------
# Layer 2 — providers mark the refusal as resumable
#
# Every provider below owns an identical "refuse duplicate-producing retry"
# branch. Parametrizing over all of them is deliberate: the bug was fixed in
# one provider's classifier before and the others drifted.
# ---------------------------------------------------------------------------

# The set of classifications the retry ladder acts on.  A fault outside it
# never reaches the content_yielded refusal at all, so a test that supplies
# one would assert resumable is False and pass for entirely the wrong reason.
RETRYABLE_TYPES = (ErrorType.THROTTLE, ErrorType.READ_TIMEOUT,
                   ErrorType.OVERLOADED)


def _provider_specs():
    """(id, module_path, class_name, transient_error_text) per provider.

    The error text is per-provider ON PURPOSE.  Each ``_classify_error`` keys
    on substrings its OWN backend emits: the Anthropic 500 ``api_error`` dict
    classifies UNKNOWN on the OpenAI/Google/Mantle classifiers, which is
    correct (those APIs never emit it) but makes it useless as a probe there —
    an UNKNOWN never reaches the content_yielded refusal, so every assertion
    about that refusal would pass vacuously.
    """
    return [
        # 500 api_error family -> READ_TIMEOUT (test_anthropic_500_retryable.py)
        ("anthropic", "app.providers.anthropic_direct",
         "AnthropicDirectProvider", LIVE_500),
        # "readtimeout" contains "timeout" -> READ_TIMEOUT
        ("openai", "app.providers.openai_direct",
         "OpenAIDirectProvider",
         "httpx.ReadTimeout: The read operation timed out"),
        # "503" -> OVERLOADED
        ("google", "app.providers.google_direct",
         "GoogleDirectProvider",
         "503 The service is currently unavailable."),
        # "server had an error" -> OVERLOADED
        ("mantle", "app.providers.openai_responses_mantle",
         "OpenAIResponsesMantleProvider",
         "The server had an error while processing your request. "
         "Sorry about that!"),
    ]


@pytest.mark.parametrize("pid,module_path,class_name,err_text",
                         _provider_specs(),
                         ids=[s[0] for s in _provider_specs()])
def test_probe_error_is_actually_retryable_on_that_provider(
        pid, module_path, class_name, err_text):
    """Anti-vacuity guard for the two provider tests below.

    Both of them depend on the fault reaching the ``content_yielded``
    refusal branch, which only transient classifications do.  If a probe
    string ever stops classifying as retryable on its provider, this fails
    by name instead of letting the resumable assertions pass for free.
    """
    import importlib
    cls = getattr(importlib.import_module(module_path), class_name)
    classified = cls._classify_error(err_text)
    assert classified in RETRYABLE_TYPES, (
        f"{class_name} classifies its own probe string as "
        f"{classified.name}, which never reaches the content_yielded "
        f"refusal — the resumable tests below would pass vacuously"
    )


def _instantiate_bare(module_path: str, class_name: str):
    """Build a provider WITHOUT running __init__ (no creds, no network)."""
    import importlib
    mod = importlib.import_module(module_path)
    cls = getattr(mod, class_name)
    obj = cls.__new__(cls)
    # stream_response reads these on the way through.
    obj.model_id = "test-model"
    obj.model_config = {}
    return obj


def _neutralize_request_build(provider, pid: str) -> None:
    """Stub out request building so the test reaches the retry ladder.

    ``_build_request``'s RETURN SHAPE differs by provider and a wrong shape
    fails *before* ``_do_stream`` is ever called, inside stream_response's own
    try/except, yielding an UNKNOWN ErrorEvent — which looks exactly like a
    resumable regression.  Google returns a ``(contents, gen_config)`` pair;
    the others return a single kwargs dict.
    """
    if pid == "google":
        provider._build_request = MagicMock(return_value=([], None))
    else:
        provider._build_request = MagicMock(return_value={})
    if hasattr(provider, "_check_context_limit"):
        provider._check_context_limit = MagicMock(return_value=None)


async def _drain(agen) -> List[StreamEvent]:
    out = []
    async for ev in agen:
        out.append(ev)
    return out


def _stub_stream(events_then_raise: List[StreamEvent], exc: Exception):
    """An async generator that yields events then raises mid-stream."""
    async def _gen(*args, **kwargs):
        for ev in events_then_raise:
            yield ev
        raise exc
    return _gen


@pytest.mark.parametrize("pid,module_path,class_name,err_text",
                         _provider_specs(),
                         ids=[s[0] for s in _provider_specs()])
@pytest.mark.asyncio
async def test_text_then_transient_fault_is_resumable(
        pid, module_path, class_name, err_text):
    """Partial TEXT + transient fault -> retryable=False, resumable=True."""
    provider = _instantiate_bare(module_path, class_name)
    provider._do_stream = _stub_stream(
        [TextDelta(content="Here is the first half of the answer.")],
        Exception(err_text),
    )
    _neutralize_request_build(provider, pid)

    events = await _drain(provider.stream_response(
        [{"role": "user", "content": "hi"}], "sys", [], ProviderConfig()))

    errors = [e for e in events if isinstance(e, ErrorEvent)]
    assert errors, f"{class_name} yielded no ErrorEvent: {events}"
    err = errors[-1]
    assert err.error_type in RETRYABLE_TYPES, (
        f"{class_name} classified the fault {err.error_type.name}; the "
        f"content_yielded refusal is never reached for non-transient faults"
    )
    assert err.retryable is False, "retrying here duplicates the emitted text"
    assert err.resumable is True, (
        f"{class_name} refused the retry but did not mark the fault "
        f"resumable — the orchestrator will kill the turn instead of "
        f"resuming from the partial text"
    )


@pytest.mark.parametrize("pid,module_path,class_name,err_text",
                         _provider_specs(),
                         ids=[s[0] for s in _provider_specs()])
@pytest.mark.asyncio
async def test_tool_use_started_is_not_resumable(
        pid, module_path, class_name, err_text):
    """A half-emitted tool call cannot be finished by a text prefill.

    Paired with ``test_text_then_transient_fault_is_resumable``, which uses
    the SAME error text and asserts the opposite: only the ToolUseStart
    differs between them, so a False here is attributable to the tool-use
    gate rather than to the fault never having been transient.
    """
    provider = _instantiate_bare(module_path, class_name)
    provider._do_stream = _stub_stream(
        [TextDelta(content="Let me check."),
         ToolUseStart(id="tu_1", name="mcp_run_shell_command", index=1)],
        Exception(err_text),
    )
    _neutralize_request_build(provider, pid)

    events = await _drain(provider.stream_response(
        [{"role": "user", "content": "hi"}], "sys", [], ProviderConfig()))

    err = [e for e in events if isinstance(e, ErrorEvent)][-1]
    assert err.error_type in RETRYABLE_TYPES, (
        f"{class_name} classified the fault {err.error_type.name}; this test "
        f"would then prove nothing about the tool_use gate"
    )
    assert err.resumable is False, (
        f"{class_name} marked a stream with an in-flight tool_use resumable; "
        f"a text prefill cannot complete the tool call"
    )


# ---------------------------------------------------------------------------
# Layer 3 — the seam: does the executor actually resume?
#
# Drives StreamingToolExecutor.stream_with_tools with a scripted provider,
# mirroring the harness in test_e2e_pipeline.py.
# ---------------------------------------------------------------------------

class ScriptedProvider(LLMProvider):
    """Yields one scripted list of StreamEvents per stream_response call."""

    def __init__(self, turns: List[List[StreamEvent]]):
        self.turns = list(turns)
        self.calls = 0
        self.prompts: List[Any] = []

    async def stream_response(self, messages, system_content, tools, config):
        idx = self.calls
        self.calls += 1
        self.prompts.append(messages)
        if idx >= len(self.turns):
            yield TextDelta(content="[unexpected extra turn]")
            yield StreamEnd(stop_reason="end_turn")
            return
        for event in self.turns[idx]:
            yield event
            await asyncio.sleep(0)

    def build_assistant_message(self, text, tool_uses, **kw):
        return {"role": "assistant", "content": [{"type": "text", "text": text}]}

    def build_tool_result_message(self, tool_results):
        return {"role": "user", "content": []}

    @property
    def provider_name(self) -> str:
        return "scripted"


@pytest.fixture(autouse=True)
def _env(tmp_path, monkeypatch):
    monkeypatch.setenv("ZIYA_ENDPOINT", "bedrock")
    monkeypatch.setenv("ZIYA_MODEL", "sonnet4.0")
    monkeypatch.setenv("ZIYA_ENABLE_MCP", "false")
    monkeypatch.setenv("ZIYA_USER_CODEBASE_DIR", str(tmp_path))
    monkeypatch.setenv("ZIYA_MODE", "test")
    monkeypatch.setenv("ZIYA_LOG_LEVEL", "WARNING")
    monkeypatch.setenv("ZIYA_DISABLE_PLUGINS", "1")


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
                                 "name": "mock"},
                   temperature_override=None,
                   max_tokens_override=None,
               )):
        return StreamingToolExecutor()


async def _run(executor) -> List[Dict[str, Any]]:
    messages = [
        {"role": "system", "content": "You are a helpful assistant."},
        {"role": "user", "content": "Explain the thing."},
    ]
    return [c async for c in executor.stream_with_tools(messages, tools=[])]


# Long enough that the resume helper's 200-char / 5-newline flush heuristics
# are not what the assertions depend on (the tail flush covers short text,
# but relying on it would make this test about buffering, not resumption).
RESUME_TAIL = ("continues to the actual conclusion, which is the part the "
               "user was waiting for and which the dead-turn bug threw away.\n"
               "Line two of the resumed content.\nLine three.\nLine four.\n"
               "Line five so the buffer flushes on a newline boundary.\n")


def _resumable_500() -> ErrorEvent:
    return ErrorEvent(message=LIVE_500, error_type=ErrorType.READ_TIMEOUT,
                      retryable=False, resumable=True)


class TestExecutorResumesInterruptedStream:

    @pytest.mark.asyncio
    async def test_resumable_error_triggers_second_call_and_completes(self):
        """The turn survives: provider is called again and the tail arrives."""
        provider = ScriptedProvider([
            # Turn 1 — partial text, then a resumable mid-stream fault.
            [TextDelta(content="The first part of the answer is here and it\n"
                               "was cut off mid-sen"),
             _resumable_500()],
            # Turn 2 — the resume call.
            [TextDelta(content=RESUME_TAIL),
             UsageEvent(input_tokens=10, output_tokens=10),
             StreamEnd(stop_reason="end_turn")],
        ])
        chunks = await _run(_make_executor(provider))

        assert provider.calls == 2, (
            f"expected a resume call after the resumable fault, got "
            f"{provider.calls} provider call(s) — the turn was killed")

        text = "".join(c.get("content", "") for c in chunks
                       if c.get("type") == "text")
        assert "actual conclusion" in text, (
            f"resumed content never reached the consumer: {text!r}")

        # Positive control on the path having run at all.
        rewinds = [c for c in chunks if c.get("type") == "continuation_rewind"]
        assert len(rewinds) == 1, (
            f"expected exactly one rewind so the partial tail is dropped "
            f"from the rendered content, got {rewinds}")
        # The partial line ("...mid-sen") is line 2; rewind keeps line 1.
        assert rewinds[0]["rewind_line"] == 1, rewinds[0]

        # The raw provider fault must NOT be shown when recovery succeeded.
        assert not [c for c in chunks if c.get("type") == "error"], (
            "a successful resume still surfaced the transient error to the user")

        ends = [c for c in chunks if c.get("type") == "stream_end"]
        assert len(ends) == 1, f"expected exactly one stream_end, got {ends}"

    @pytest.mark.asyncio
    async def test_resume_prompt_carries_interruption_framing(self):
        """The resume must not be framed as an unclosed code fence."""
        provider = ScriptedProvider([
            [TextDelta(content="Prose with no code fence at all here.\nand a tail"),
             _resumable_500()],
            [TextDelta(content=RESUME_TAIL), StreamEnd(stop_reason="end_turn")],
        ])
        await _run(_make_executor(provider))

        assert provider.calls == 2
        resume_messages = provider.prompts[1]
        blob = json.dumps(resume_messages)
        assert "cut off mid-stream" in blob, (
            f"resume prompt did not use interruption framing: {blob[-600:]}")
        assert "unclosed" not in blob, (
            "resume reused the unclosed-fence wording, which invites the model "
            "to invent a code block that was never there")

    @pytest.mark.asyncio
    async def test_resume_is_attempted_at_most_once(self):
        """A resume that fails the same way must not loop."""
        provider = ScriptedProvider([
            [TextDelta(content="First chunk of text.\npartial"),
             _resumable_500()],
            # The resume itself dies the same way.
            [TextDelta(content="a bit more text that also dies\n"),
             _resumable_500()],
        ])
        chunks = await _run(_make_executor(provider))

        assert provider.calls <= 2, (
            f"resume looped: {provider.calls} provider calls")
        assert len([c for c in chunks if c.get("type") == "stream_end"]) == 1

    @pytest.mark.asyncio
    async def test_resume_producing_nothing_surfaces_original_error(self):
        """Silent truncation is worse than a visible failure."""
        provider = ScriptedProvider([
            [TextDelta(content="Some text.\ntail"), _resumable_500()],
            # Resume yields no text at all.
            [StreamEnd(stop_reason="end_turn")],
        ])
        chunks = await _run(_make_executor(provider))

        errors = [c for c in chunks if c.get("type") == "error"]
        assert errors, (
            "an empty resume left the user with a truncated answer and no "
            "indication anything went wrong")
        assert "Internal server error" in errors[0].get("content", "")


def _apply_continuation_rewind(content: str, rewind_line: Any):
    """Port of frontend/src/apis/continuationProtocol.ts.

    Asserting on executor internals would miss the whole defect class here:
    ``rewind_line`` is computed from ``assistant_text``, but the CONSUMER can
    only trim what it has actually received.  This models the consumer so the
    tests below assert on what the user would see.
    """
    line_count = len(content.split("\n"))
    if not isinstance(rewind_line, int) or rewind_line < 0 or rewind_line > line_count:
        return content, False
    return "\n".join(content.split("\n")[:rewind_line]), True


async def _run_as_consumer(executor):
    """Drive the executor and accumulate text the way the frontend does.

    Returns (final_content, rewind_line, rewind_applied, chunks).
    """
    content = ""
    rewind_line = None
    applied = None
    chunks = []
    async for chunk in executor.stream_with_tools(
            [{"role": "user", "content": "go"}], tools=[]):
        chunks.append(chunk)
        ctype = chunk.get("type")
        if ctype == "continuation_rewind":
            rewind_line = chunk.get("rewind_line")
            content, applied = _apply_continuation_rewind(content, rewind_line)
        elif ctype == "text":
            content += chunk.get("content", "")
    return content, rewind_line, applied, chunks


# Shapes whose trailing text sits in a display buffer at the moment the fault
# arrives.  Each was OBSERVED to break the rewind before the buffers were
# flushed ahead of measuring rewind_line:
#   - short lines / open fence: consumer held NOTHING while assistant_text
#     held every line, so rewind_line exceeded the consumer's line count and
#     applyContinuationRewind refused it
#   - bare backticks: assistant_text ITSELF was empty, so no resume was even
#     attempted and the turn died
# Shapes are sized against StreamingContentOptimizer's ACTUAL thresholds
# (app/utils/streaming_optimizer.py): a buffer under ``min_chunk_size`` (15)
# is withheld entirely, and once an unclosed ``` is seen nothing flushes until
# 5000 chars.  Both numbers are load-bearing here — an earlier revision of
# this test used longer tokens, pushed "short_lines" past 15 chars, and the
# shape silently stopped reproducing while still passing.
#
# Tokens are also distinctive rather than "a\nb\nc": a single-letter first
# line is a substring of the resumed tail ("actual"), so the
# content-preservation assertion would pass without the text ever having been
# flushed.
BUFFERED_SHAPES = {
    # 11 chars < min_chunk_size, so the optimizer holds all of it.
    "short_lines": "ZQ7\nb\nc\nd\ne",
    # Unclosed fence -> optimizer refuses to flush at any size.
    "open_code_fence": "QWKINTRO text\n```python\ndef f():\n    return 1",
    # Trailing backticks land in _block_opening_buffer, which
    # process_text_delta withholds from assistant_text entirely.
    "trailing_backticks": "VBNPROSE leading up to a fence\n``",
}


class TestRewindIsAppliableByTheConsumer:
    """The rewind offset must be reachable in the text the consumer HOLDS."""

    @pytest.mark.parametrize("shape", sorted(BUFFERED_SHAPES),
                             ids=sorted(BUFFERED_SHAPES))
    @pytest.mark.asyncio
    async def test_buffered_partial_text_still_rewinds(self, shape):
        partial = BUFFERED_SHAPES[shape]
        provider = ScriptedProvider([
            [TextDelta(content=partial), _resumable_500()],
            [TextDelta(content=RESUME_TAIL),
             UsageEvent(input_tokens=5, output_tokens=5),
             StreamEnd(stop_reason="end_turn")],
        ])
        content, rewind_line, applied, chunks = await _run_as_consumer(
            _make_executor(provider))

        assert provider.calls == 2, (
            f"{shape!r}: no resume attempted ({provider.calls} call(s)) — "
            f"buffered text left assistant_text empty, so the turn died")
        assert rewind_line is not None, f"{shape!r}: no rewind emitted"
        assert applied is True, (
            f"{shape!r}: consumer could not apply rewind_line={rewind_line}; "
            f"the partial tail stays on screen and the resume duplicates it")

    @pytest.mark.parametrize("shape", sorted(BUFFERED_SHAPES),
                             ids=sorted(BUFFERED_SHAPES))
    @pytest.mark.asyncio
    async def test_buffered_partial_text_is_not_lost(self, shape):
        """The flush must DISPLAY the buffered text, not silently drop it.

        Paired with the test above on purpose: a resume that emitted no text
        at all would satisfy "rewind applied" trivially while losing every
        line the model actually produced.
        """
        partial = BUFFERED_SHAPES[shape]
        provider = ScriptedProvider([
            [TextDelta(content=partial), _resumable_500()],
            [TextDelta(content=RESUME_TAIL),
             UsageEvent(input_tokens=5, output_tokens=5),
             StreamEnd(stop_reason="end_turn")],
        ])
        content, _, _, _ = await _run_as_consumer(_make_executor(provider))

        # First line survives the rewind in every shape here (the rewind only
        # drops the final incomplete line).
        first_line = partial.split("\n")[0]
        assert first_line in content, (
            f"{shape!r}: {first_line!r} never reached the consumer — buffered "
            f"text was dropped instead of flushed. Got: {content[:200]!r}")
        assert "actual conclusion" in content, (
            f"{shape!r}: resumed content missing: {content[:200]!r}")


class TestExecutorStillEndsFatalTurns:
    """The other half of the contract — resumable must not swallow fatals."""

    @pytest.mark.asyncio
    async def test_non_resumable_error_ends_the_turn(self):
        """A fatal 400 must NOT be retried or resumed."""
        provider = ScriptedProvider([
            [TextDelta(content="partial text\nand more"),
             ErrorEvent(message="prompt is too long: 250000 tokens > 200000",
                        error_type=ErrorType.CONTEXT_LIMIT,
                        retryable=False, resumable=False)],
        ])
        chunks = await _run(_make_executor(provider))

        assert provider.calls == 1, (
            f"a CONTEXT_LIMIT 400 was re-sent {provider.calls} times")
        errors = [c for c in chunks if c.get("type") == "error"]
        assert errors and "too long" in errors[0].get("content", "")
        assert not [c for c in chunks if c.get("type") == "continuation_rewind"]

    @pytest.mark.asyncio
    async def test_resumable_with_no_text_yet_is_not_resumed(self):
        """With nothing accumulated there is no prefill to resume from."""
        provider = ScriptedProvider([
            [ThinkingDelta(content="reasoning only, no visible text"),
             _resumable_500()],
        ])
        chunks = await _run(_make_executor(provider))

        assert provider.calls == 1, (
            "resumed with an empty prefill, which is just a from-scratch "
            "retry wearing a costume")
        assert [c for c in chunks if c.get("type") == "error"]
