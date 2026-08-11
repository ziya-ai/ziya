"""Tests for mid-stream feedback delivery: classification, SSE relay, recovery.

Covers three independent defects that combined to make feedback sent during a
running tool chain appear to be "queued for minutes and never delivered":

  1. ``feedback_delivered`` was emitted by the executor but had no branch in
     the SSE relay, so it never reached the browser.  The frontend's
     confirmation handler was unreachable and the status chip could only go
     queued -> undelivered, reporting loss on EVERY turn.
  2. Stop detection was a substring scan, so "don't stop, keep going" and
     "stop reading that and check the tests" both killed the turn.
  3. Feedback staged at a tool boundary (popped out of the shared pending
     list into ``deferred_feedback_messages``) was unrecoverable if the turn
     ended before the end-of-iteration injection point.
"""

import asyncio
import re
import time

import pytest

from app.utils.feedback_directives import is_stop_directive, is_stop_feedback


# ── 1. Stop classification ────────────────────────────────────────────────

@pytest.mark.parametrize("msg", [
    "stop", "STOP", "  stop  ", "stop.", "stop!",
    "please stop", "just stop", "stop now", "ok stop",
    "halt", "abort", "cancel", "quit",
    "stop it", "stop that", "cancel that", "nevermind", "never mind",
])
def test_unconditional_stops_are_stops(msg):
    assert is_stop_directive(msg) is True


@pytest.mark.parametrize("msg", [
    # Negations — the substring scan killed the turn on all of these.
    "don't stop, keep going with the second file",
    "dont stop",
    "do not abort",
    "no need to cancel",
    "never stop until the tests pass",
    # Redirection: says stop AND says something else.  Treated as feedback so
    # the model decides — a wrong guess here is recoverable, ending the turn
    # is not.
    "stop reading that file and check the tests instead",
    "stop using grep, use the AST tools",
    "cancel the third one but finish the first two",
    # Ordinary feedback that merely contains a stop word.
    "the retry loop never stops, look at that",
    "add a stop button to the toolbar",
    "this is a non-stop loop",
    # Degenerate input.
    "", "   ", "!!!", None, 42,
])
def test_non_stops_are_not_stops(msg):
    assert is_stop_directive(msg) is False


def test_is_stop_feedback_covers_interrupt_type():
    assert is_stop_feedback({"type": "interrupt"}) is True
    assert is_stop_feedback({"type": "feedback", "message": "stop"}) is True
    assert is_stop_feedback(
        {"type": "feedback", "message": "don't stop"}) is False
    assert is_stop_feedback({}) is False
    assert is_stop_feedback(None) is False


# ── 2. SSE relay completeness ─────────────────────────────────────────────
#
# The relay is a long inline if/elif chain inside a streaming generator, so
# exercising it end-to-end would require standing up the whole chat endpoint.
# The defect class is precisely "a chunk type has no elif branch", which is a
# structural property, so it is asserted structurally.  This is what makes the
# regression detectable at all: the missing branch produced no error, only a
# debug log.

def _relayed_chunk_types(source: str) -> set:
    types = set(re.findall(
        r"""chunk\.get\(['"]type['"]\)\s*==\s*['"]([a-z_]+)['"]""", source))
    for grp in re.findall(
            r"""chunk\.get\(['"]type['"]\)\s+in\s+\(([^)]*)\)""", source):
        types |= {t.strip().strip("'\"") for t in grp.split(",") if t.strip()}
    return types


def test_feedback_delivered_is_relayed_to_the_client():
    """The executor emits feedback_delivered; the relay must forward it.

    Without this branch the chunk fell through to the relay's final else and
    was logged as "Unknown chunk type", so the user was never told their
    feedback landed — the single most visible symptom of the whole bug.
    """
    src = open("app/server.py").read()
    assert "feedback_delivered" in _relayed_chunk_types(src), (
        "server.py's SSE relay has no branch for 'feedback_delivered'; the "
        "frontend confirmation handler is unreachable and the feedback status "
        "chip can never leave 'queued'"
    )


def test_continuation_protocol_events_are_relayed_to_the_client():
    """Explicit rewind/failure events must not fall through as unknown chunks."""
    src = open("app/server.py").read()
    relayed = _relayed_chunk_types(src)
    assert "continuation_rewind" in relayed
    assert "continuation_failed" in relayed

    retry_types = set(re.findall(
        r"""retry_chunk\.get\(['"]type['"]\)\s*==\s*['"]([a-z_]+)['"]""", src))
    for grp in re.findall(
            r"""retry_chunk\.get\(['"]type['"]\)\s+in\s+\(([^)]*)\)""", src):
        retry_types |= {t.strip().strip("'\"") for t in grp.split(",") if t.strip()}
    assert "continuation_rewind" in retry_types
    assert "continuation_failed" in retry_types


def test_retry_loop_also_relays_feedback_delivered():
    """The diff-validation retry loop is a second, separate relay chain."""
    src = open("app/server.py").read()
    retry_types = set(re.findall(
        r"""retry_chunk\.get\(['"]type['"]\)\s*==\s*['"]([a-z_]+)['"]""", src))
    assert "feedback_delivered" in retry_types, (
        "feedback delivered during a validation retry is not confirmed"
    )


# ── 3. Mid-tool stop watch ────────────────────────────────────────────────

class _Ctx:
    """Minimal stand-in for ToolExecContext for _await_tool_result."""

    def __init__(self, pending):
        self.actual_tool_name = "slow_tool"
        self._pending = pending
        self.peek_feedback_fn = lambda: list(self._pending)


@pytest.mark.asyncio
async def test_await_returns_result_when_no_feedback():
    from app.tool_execution import _await_tool_result

    async def _work():
        await asyncio.sleep(0.05)
        return {"ok": True}

    out = await _await_tool_result(_work(), 5.0, _Ctx([]))
    assert out == {"ok": True}


@pytest.mark.asyncio
async def test_await_aborts_on_midflight_stop():
    """A stop arriving mid-tool aborts within ~0.3s, not after the tool ends.

    The tool here would take 10s; the assertion is that we do not wait for it.
    """
    from app.tool_execution import _await_tool_result, ToolStopRequested

    pending = []

    async def _slow():
        await asyncio.sleep(10)
        return {"ok": True}

    async def _send_stop():
        await asyncio.sleep(0.05)
        pending.append({"type": "feedback", "message": "stop"})

    started = time.monotonic()
    asyncio.ensure_future(_send_stop())
    with pytest.raises(ToolStopRequested):
        await _await_tool_result(_slow(), 30.0, _Ctx(pending))
    assert time.monotonic() - started < 2.0


@pytest.mark.asyncio
async def test_await_does_not_abort_on_ordinary_feedback():
    """Non-stop feedback must NOT abort the tool, and must stay pending.

    Draining it here would strip it from the shared list at a point where it
    cannot be injected (a user message may not sit between a tool_use and its
    tool_result), so the post-execution drain has to still find it.
    """
    from app.tool_execution import _await_tool_result

    pending = [{"type": "feedback", "message": "also check the tests"}]

    async def _work():
        await asyncio.sleep(0.5)
        return {"ok": True}

    out = await _await_tool_result(_work(), 5.0, _Ctx(pending))
    assert out == {"ok": True}
    assert pending == [{"type": "feedback", "message": "also check the tests"}]


@pytest.mark.asyncio
async def test_await_without_peek_fn_falls_back_to_wait_for():
    """Callers that never wire a peek fn keep the previous behaviour."""
    from app.tool_execution import _await_tool_result

    class _NoPeek:
        actual_tool_name = "t"
        peek_feedback_fn = None

    async def _work():
        return 7

    assert await _await_tool_result(_work(), 5.0, _NoPeek()) == 7


@pytest.mark.asyncio
async def test_await_still_times_out():
    from app.tool_execution import _await_tool_result

    async def _hang():
        await asyncio.sleep(30)

    with pytest.raises(asyncio.TimeoutError):
        await _await_tool_result(_hang(), 0.5, _Ctx([]))


# ── 4. Staged-feedback recovery ───────────────────────────────────────────

def test_staged_recovery_block_reads_deferred_messages():
    """The teardown recovery must cover deferred_feedback_messages.

    ``_drain_pending_feedback()`` cannot see staged items: the tool-boundary
    drain already popped them out of the shared list.  A turn ending between
    staging and the end-of-iteration injection therefore lost them silently.
    """
    src = open("app/streaming_tool_executor.py").read()
    tail = src[src.index("FEEDBACK_STAGED_RECOVERY") - 2000:]
    assert "deferred_feedback_messages" in tail, (
        "teardown recovery does not inspect deferred_feedback_messages, so "
        "feedback staged at a tool boundary is still lost when the turn ends "
        "before the injection point"
    )


def test_stop_scans_are_centralised():
    """No drain point may re-inline the substring scan.

    Six sites each had their own copy; they drifted, and every copy mis-fired
    on "don't stop".  A new one would silently reintroduce the bug.
    """
    pattern = re.compile(
        r"""for\s+w\s+in\s+\[\s*['"]stop['"]\s*,\s*['"]halt['"]""")
    for path in ("app/streaming_tool_executor.py", "app/tool_execution.py"):
        assert not pattern.search(open(path).read()), (
            f"{path} re-inlines the stop-word substring scan; use "
            f"app.utils.feedback_directives.is_stop_directive instead"
        )
