"""Unit tests for app/utils/repetition_guard.py.

The end-to-end behaviour through stream_with_tools() is covered by
tests/test_stream_repetition_guard.py; these pin the detector's rules and the
stream wrapper's close-and-end contract directly.
"""

import asyncio

import pytest

from app.providers.base import StreamEnd, TextDelta
from app.utils.repetition_guard import (
    REPETITION_STOP_REASON,
    SentenceRepetitionGuard,
    StoppableStream,
)


def _feed_all(text, chunk=13):
    """Feed in small chunks; return the triggering sentence or None."""
    g = SentenceRepetitionGuard()
    for i in range(0, len(text), chunk):
        hit = g.feed(text[i:i + chunk])
        if hit:
            return hit
    return g.feed('')


S = "The system is processing your request right now. "
A = "First we validate every modem key in the plan. "
B = "Then each spot identifier is checked against its range. "


class TestTriggers:

    def test_consecutive_loop(self):
        assert _feed_all(S * 3) is not None

    def test_two_copies_do_not_trigger(self):
        assert _feed_all(S * 2 + "And then something new happens here. ") is None

    def test_paragraph_cycle(self):
        # A B A B A: the third A closes a repeated two-sentence cycle.
        hit = _feed_all(A + B + A + B + A)
        assert hit is not None and "validate every modem key" in hit

    def test_loop_with_varying_sentence_caught_by_density(self):
        text = "".join(
            f"Attempt number {n} of the operation has failed. Retrying the same operation now. "
            for n in range(1, 8)
        )
        hit = _feed_all(text)
        assert hit is not None and "Retrying the same operation" in hit


class TestDoesNotTrigger:

    def test_recurring_sentence_with_new_material_between(self):
        """A refrain separated by different content each time is not a loop."""
        refrain = "This behaviour is unchanged from the previous release. "
        text = (
            "The parser now accepts trailing commas in arrays. " + refrain
            + "Error messages include the byte offset of the fault. " + refrain
            + "Unicode escapes are validated before decoding begins. " + refrain
            + "That is the full list of changes for this version. "
        )
        assert _feed_all(text) is None

    def test_per_section_checklist(self):
        """Same three checklist items under several headings."""
        section = (
            "- Verify the build passes on the CI fleet.\n"
            "- Confirm the telemetry dashboard shows the new metric.\n"
            "- Record the result in the release ticket.\n"
        )
        text = "".join(f"\n## Component {c}\n{section}" for c in "ABCD")
        assert _feed_all(text) is None

    def test_short_fragments_ignored(self):
        assert _feed_all("Yes it is. " * 10) is None


class TestFenceAwareness:
    """The guard tracks fences on the raw stream itself (see module docs for
    why the executor's tracker cannot be relied on)."""

    def test_loop_inside_backtick_fence_ignored(self):
        assert _feed_all("Intro text here.\n```tikz\n" + S * 6 + "\n```\nAll done now.\n") is None

    def test_loop_inside_tilde_fence_ignored(self):
        assert _feed_all("Intro.\n~~~~\n" + S * 6 + "\n~~~~\n") is None

    def test_shorter_run_does_not_close_fence(self):
        text = "````md\n```\n" + S * 6 + "\n```\n````\nEnd.\n"
        assert _feed_all(text) is None

    def test_prose_after_fence_is_scanned_again(self):
        text = "```python\nx = 1\n```\n" + S * 3
        assert _feed_all(text) is not None

    def test_single_long_line_loop_caught_before_newline(self):
        """Degenerate loops are often one paragraph with no newline; the
        partial line must be scanned as it streams."""
        g = SentenceRepetitionGuard()
        hit = None
        for piece in [S] * 3:
            hit = hit or g.feed(piece)
        assert hit is not None

    def test_four_space_indent_is_not_a_fence(self):
        assert _feed_all("Some text.\n    ```\n" + S * 3) is not None

    def test_inline_backticks_are_prose(self):
        assert _feed_all(("Run `make` again to rebuild the whole tree. ") * 3) is not None

    def test_fence_split_across_chunks(self):
        g = SentenceRepetitionGuard()
        pieces = ["Text.\n`", "``ti", "kz\n", S, S, S, S, "\n``", "`\n"]
        assert all(g.feed(p) is None for p in pieces)


async def _source(events, log):
    try:
        for e in events:
            log.append(('yield', e))
            yield e
    finally:
        log.append(('closed', None))


class TestStoppableStream:

    @pytest.mark.asyncio
    async def test_passthrough_when_not_stopped(self):
        log = []
        evs = [TextDelta(content="a"), StreamEnd(stop_reason="end_turn")]
        s = StoppableStream(_source(evs, log))
        out = [e async for e in s]
        assert out == evs
        assert not s.stopped_early

    @pytest.mark.asyncio
    async def test_stop_closes_upstream_and_ends_with_reason(self):
        log = []
        evs = [TextDelta(content=str(i)) for i in range(100)] + [StreamEnd(stop_reason="end_turn")]
        s = StoppableStream(_source(evs, log))
        out = []
        async for e in s:
            out.append(e)
            if len(out) == 3:
                s.request_stop()
        # Three real events, then exactly one synthetic end.
        assert [getattr(e, 'content', None) for e in out[:3]] == ['0', '1', '2']
        assert isinstance(out[-1], StreamEnd)
        assert out[-1].stop_reason == REPETITION_STOP_REASON
        assert len(out) == 4
        assert s.stopped_early
        # Upstream was closed and never asked for the rest.
        assert ('closed', None) in log
        assert sum(1 for k, _ in log if k == 'yield') == 3

    @pytest.mark.asyncio
    async def test_consumer_abandoning_iteration_closes_upstream(self):
        log = []
        evs = [TextDelta(content=str(i)) for i in range(10)]
        s = StoppableStream(_source(evs, log))
        it = s.__aiter__()
        await it.__anext__()
        await it.aclose()
        assert ('closed', None) in log
