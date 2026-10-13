"""Streaming degeneration detection and early stream termination.

Two pieces, both used by StreamingToolExecutor.stream_with_tools():

``SentenceRepetitionGuard``
    Detects autoregressive degeneration (the model looping on the same
    sentence or paragraph) in streamed prose. The caller feeds it only text
    that is outside fenced blocks; code and diagram specs repeat structurally
    in ways prose degeneration never does.

    A sentence recurring is not by itself a loop. Legitimate prose repeats
    sentences too: a per-section checklist, a refrain in a report, a table
    column of identical cells. The earlier rule ("any 20+ char sentence seen
    3 times") cut those. A loop is recognised instead by one of:

    * cycle: the sentence's last two occurrences bracket a run of sentences
      that is then repeated exactly, up to the new occurrence. ``A A A`` and
      ``A B A B A`` match; ``A x y A z w A`` (new material between copies)
      does not.
    * density: the sentence makes up a large share of the recent window
      (5 copies in the last 12 recorded sentences). This catches loops that
      interleave a varying sentence, e.g. "Attempt number 3 has failed."

``StoppableStream``
    Wraps a provider event stream so that a consumer can stop it mid-flight.
    On stop it closes the upstream stream (so the model stops generating and
    stops being billed for output nobody sees) and yields one synthetic
    ``StreamEnd`` carrying the stop reason, so the consumer's normal
    end-of-message handling (buffer flushes, usage recording, continuation
    gating) still runs.
"""

from __future__ import annotations

import logging
import re
from typing import Any, AsyncIterable, AsyncIterator, List, Optional

logger = logging.getLogger(__name__)

# Stop reason carried by the synthetic StreamEnd. Deliberately not in
# message_stop_handler._CUTOFF_STOP_REASONS: a degenerate loop must not be
# "continued from where it left off".
REPETITION_STOP_REASON = 'repetition'

# Shown to the user (and therefore persisted in the turn) when a loop is cut,
# so neither the user nor the model on its next turn mistakes the cut for a
# complete answer. Previously the cut was silent.
REPETITION_NOTICE = (
    "\n\n*[Output stopped: the response began repeating the same text. "
    "Ask to continue if something is missing.]*\n"
)

_SENTENCE_END_RE = re.compile(r'([.!?])\s')
_NON_WORD_RE = re.compile(r'[^\w\s]')
_SPACE_RE = re.compile(r'\s+')


def normalize_sentence(sentence: str) -> str:
    """Lowercase, drop punctuation, collapse whitespace."""
    return _SPACE_RE.sub(' ', _NON_WORD_RE.sub('', sentence.lower())).strip()


class SentenceRepetitionGuard:
    """Incremental detector for looping prose. See module docstring."""

    MIN_SENTENCE_LEN = 20
    DENSITY_COPIES = 5
    DENSITY_WINDOW = 12
    HISTORY_MAX = 50
    HISTORY_KEEP = 30

    def __init__(self) -> None:
        self._buffer = ''
        self._history: List[str] = []
        # Own fence tracking over the RAW stream. The executor's
        # code_block_tracker is fed text only after the content optimizer and
        # viz buffering release it, and those can hold an entire fence and
        # release opener, body and closer in one chunk: the tracker then
        # reports in_block False before and after, and a guard gated on it
        # read the whole body as prose (observed for ```tikz, depending only
        # on where chunk boundaries fell).
        self._line = ''          # current, possibly partial, line
        self._line_fed = 0       # chars of _line already passed on as prose
        self._fence: Optional[tuple] = None   # (char, length) while inside

    def feed(self, text: str) -> Optional[str]:
        """Add raw streamed text; return the sentence that proves a loop.

        Text inside fenced blocks (``` or ~~~, CommonMark rules) is ignored.
        Passing ``''`` still drains complete sentences already buffered.
        """
        if text:
            parts = text.split('\n')
            for i, part in enumerate(parts):
                self._line += part
                if i < len(parts) - 1:
                    self._end_line()
                else:
                    self._feed_partial_line()
        return self._drain()

    # -- fence tracking ---------------------------------------------------

    @staticmethod
    def _fence_run(line: str) -> Optional[tuple]:
        """(char, run_length, rest) if *line* starts a fence run, else None."""
        stripped = line.lstrip(' ')
        if len(line) - len(stripped) > 3 or not stripped:
            return None
        ch = stripped[0]
        if ch not in '`~':
            return None
        n = len(stripped) - len(stripped.lstrip(ch))
        return (ch, n, stripped[n:])

    def _could_become_fence_line(self, line: str) -> bool:
        """True while a partial line may still turn out to be a fence line."""
        run = self._fence_run(line)
        if run is None:
            # Only spaces so far (or nothing) may still be followed by a fence.
            return line.strip(' ') == '' and len(line) <= 3
        ch, n, rest = run
        if self._fence is not None:
            fch, flen = self._fence
            return ch == fch and (n < flen and rest == '' or rest.strip() == '')
        return n < 3 and rest == '' or n >= 3

    def _feed_partial_line(self) -> None:
        if self._fence is not None or self._could_become_fence_line(self._line):
            return
        self._buffer += self._line[self._line_fed:]
        self._line_fed = len(self._line)

    def _end_line(self) -> None:
        line = self._line
        run = self._fence_run(line)
        if self._fence is None:
            if run and run[1] >= 3 and not (run[0] == '`' and '`' in run[2]):
                self._fence = (run[0], run[1])
                # A sentence left open before a fence must not join with prose
                # after it; terminate it at the fence boundary.
                self._buffer += '\n'
            else:
                self._buffer += line[self._line_fed:] + '\n'
        else:
            fch, flen = self._fence
            if run and run[0] == fch and run[1] >= flen and run[2].strip() == '':
                self._fence = None
        self._line = ''
        self._line_fed = 0

    # -- sentence detection -----------------------------------------------

    def _drain(self) -> Optional[str]:
        while True:
            m = _SENTENCE_END_RE.search(self._buffer)
            if not m:
                return None
            end = m.end()
            sentence = self._buffer[:end].strip()
            self._buffer = self._buffer[end:]
            if len(sentence) < self.MIN_SENTENCE_LEN:
                continue
            norm = normalize_sentence(sentence)
            if self._is_degenerate(norm):
                return sentence
            self._history.append(norm)
            if len(self._history) > self.HISTORY_MAX:
                self._history = self._history[-self.HISTORY_KEEP:]

    def _is_degenerate(self, norm: str) -> bool:
        h = self._history
        prev = [i for i, s in enumerate(h) if s == norm]
        if len(prev) >= 2:
            i1, i2 = prev[-2], prev[-1]
            # The run between the last two copies repeats exactly up to now.
            if h[i1:i2] == h[i2:]:
                return True
        recent = sum(1 for s in h[-self.DENSITY_WINDOW:] if s == norm)
        return recent >= self.DENSITY_COPIES - 1


class StoppableStream:
    """Async-iterable wrapper over a provider stream that can be stopped."""

    def __init__(self, source: AsyncIterable[Any]) -> None:
        self._source = source
        self._stop_reason: Optional[str] = None
        self.stopped_early = False

    def request_stop(self, reason: str = REPETITION_STOP_REASON) -> None:
        """Stop before the next upstream event is requested."""
        if self._stop_reason is None:
            self._stop_reason = reason

    def __aiter__(self) -> AsyncIterator[Any]:
        return self._run()

    async def _close_source(self, it: Any) -> None:
        aclose = getattr(it, 'aclose', None)
        if aclose is None:
            return
        try:
            await aclose()
        except Exception as exc:  # noqa: BLE001 - closing must not mask the stop
            logger.debug(f"StoppableStream: upstream aclose raised: {exc}")

    async def _run(self) -> AsyncIterator[Any]:
        it = self._source.__aiter__()
        try:
            while True:
                if self._stop_reason is not None:
                    break
                try:
                    event = await it.__anext__()
                except StopAsyncIteration:
                    return
                yield event
        finally:
            # Runs on early stop, on normal exhaustion (no-op for a finished
            # generator) and when the consumer itself abandons iteration.
            await self._close_source(it)
        # Reached only via the stop break above.
        self.stopped_early = True
        from app.providers.base import StreamEnd
        yield StreamEnd(stop_reason=self._stop_reason)
