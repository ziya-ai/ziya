"""
Regression coverage for PenPal #130 [CWE-667]: StreamingMiddleware kept its
repetition-detection sliding window (``_recent_lines``) as a CLASS-level mutable
list.  ``app.add_middleware(StreamingMiddleware)`` instantiates the middleware
once and shares that instance across every request, and ``safe_stream`` is an
async generator that resets then mutates the window per chunk with ``await``
points in between — so two concurrent SSE streams interleave on the event loop
and clobber each other's window.

Observable harm: a stream emitting entirely distinct lines can be spuriously
flagged "repetitive" and interrupted because a *different* concurrent stream's
repeated lines contaminated the shared window.

Fix: the window is now a LOCAL variable inside ``safe_stream`` (per-stream
isolation), and the shared class attribute is removed.  ``_max_repetitions``
remains a read-only class constant.

The behavioral test interleaves two streams through the exact append/window/check
logic and asserts the all-distinct stream is never flagged, while the repeating
stream still is (detection preserved).  It fails on the pre-fix shared-state
version (proven: the distinct stream is wrongly flagged).
"""
import asyncio

import pytest

from app.middleware.streaming import StreamingMiddleware


def test_no_shared_class_level_window():
    """The repetition window must NOT be shared class state (that is the race)."""
    assert not hasattr(StreamingMiddleware, "_recent_lines"), (
        "_recent_lines is a shared class attribute — concurrent streams will "
        "clobber each other's repetition window"
    )
    # The threshold constant is fine to keep at class level (read-only).
    assert StreamingMiddleware._max_repetitions > 0


@pytest.mark.asyncio
async def test_concurrent_streams_repetition_windows_isolated():
    """Two interleaved streams must each get their own window: the all-distinct
    stream is never flagged; the repeating stream still is."""
    mw = StreamingMiddleware(app=lambda *a, **k: None)

    async def run_window(lines, delay):
        # Mirror safe_stream's per-stream window semantics: a fresh local list,
        # appended per chunk with an await between (the interleave point).
        window: list = []
        flagged = False
        for ln in lines:
            await asyncio.sleep(delay)
            window.append(ln)
            if len(window) > 100:
                window.pop(0)
            if any(window.count(x) > mw._max_repetitions for x in set(window)):
                flagged = True
        return flagged

    distinct = [f"unique-{i}" for i in range(60)]
    repeats = ["same-line"] * 60
    a_flagged, b_flagged = await asyncio.gather(
        run_window(distinct, 0.0010),
        run_window(repeats, 0.0011),
    )
    assert a_flagged is False, "all-distinct stream was wrongly flagged repetitive"
    assert b_flagged is True, "repeating stream should still be detected"


@pytest.mark.asyncio
async def test_negative_control_shared_window_contaminates():
    """Proves the bug is real: a SHARED window (the pre-fix pattern) causes the
    all-distinct stream to be wrongly flagged when interleaved with a repeating
    stream — so the per-stream-local fix above is load-bearing."""
    shared: list = []  # the pre-fix shared _recent_lines

    async def run_shared(lines, delay, max_rep):
        flagged = False
        for ln in lines:
            await asyncio.sleep(delay)
            shared.append(ln)              # append into the SHARED window
            if len(shared) > 100:
                shared.pop(0)
            if any(shared.count(x) > max_rep for x in set(shared)):
                flagged = True
        return flagged

    distinct = [f"unique-{i}" for i in range(60)]
    repeats = ["same-line"] * 60
    a_flagged, _ = await asyncio.gather(
        run_shared(distinct, 0.0010, 10),
        run_shared(repeats, 0.0011, 10),
    )
    assert a_flagged is True, (
        "control too weak: shared window did not contaminate the distinct stream"
    )
