"""Inline reasoning-tag scanner.

Some models emit chain-of-thought as literal tags inside their CONTENT
stream rather than through a provider-level reasoning channel.  Those
carry no ``ThinkingDelta`` to convert, so the tags are parsed out here and
re-emitted as the SAME discrete ``thinking`` event the native path uses.
After this, there is one reasoning transport rather than two.

Observed behaviour this replaces
--------------------------------
The prior handling was a ``str.replace`` in ``process_text_delta`` that
rewrote one inline tag spelling into another and left it in the text
stream.  It changed the spelling, not the channel.  Three consequences,
all reported from real sessions:

1. The reasoning stayed in ``assistant_text``, which the assistant turn is
   rebuilt from -- so the model's own chain-of-thought was re-sent as
   input tokens on every subsequent iteration.

2. The frontend stripped the tags with a regex requiring the CLOSING tag.
   Mid-stream an unclosed opener could not match, so it reached the lexer
   and rendered as literal text.

3. When the closer finally arrived the regex matched and deleted the
   entire span -- the reasoning vanished from the response rather than
   collapsing into a panel.

The two rendering states are mutually exclusive in practice: either the
native channel is in use (panel renders, no raw tags) or the inline
channel is (raw tags, content deleted at the close).  Never both in one
turn.  This scanner therefore assumes a single reasoning producer and
needs no source discriminator.

Streaming behaviour
-------------------
Content inside a block is emitted as it arrives.  The only thing ever
withheld is a trailing run that is a proper PREFIX of a tag, bounded by
``MAX_HOLDBACK`` (15 chars).  Worst-case added latency is one delta on
those few characters; the block itself is never buffered, so the thinking
panel streams at token granularity exactly as the native path does.

Tag literals
------------
Tags are constructed from names rather than written literally.  The
rewrite this supersedes mutated such literals in ANY text passing through
the delta pipeline -- including source code being authored -- which is how
it corrupted diffs of this very feature.
"""

import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

# Recognized reasoning tag names.  No name is a substring of another in a
# way that makes a COMPLETE tag ambiguous ("<thinking-data>" does not
# contain "<thinking>"), so only a tag split across deltas is ambiguous.
TAG_NAMES: Tuple[str, ...] = ('thinking-data', 'reasoning', 'thinking')

OPENERS: Tuple[str, ...] = tuple('<' + n + '>' for n in TAG_NAMES)
CLOSERS: Tuple[str, ...] = tuple('</' + n + '>' for n in TAG_NAMES)

# Upper bound on withheld characters: the longest tag, less one (a full
# tag is found by search and never withheld as a prefix).
MAX_HOLDBACK: int = max(len(t) for t in OPENERS + CLOSERS) - 1


@dataclass
class InlineThinkingState:
    """Per-iteration scanner state.

    Held by the caller's own per-iteration state object rather than hung
    off the executor.  The ``executor._*`` convention requires a matching
    ``delattr`` in ``_cleanup_iteration_resources``, and that contract is
    already incomplete elsewhere in the delta pipeline -- the fake-tool
    accumulator is set but never cleaned, so an unclosed fake-tool fence
    carries into the next iteration.  Composing this dataclass into a
    per-iteration owner makes the lifecycle structural instead of
    hand-maintained.
    """

    #: True while inside a reasoning block opened by this scanner.
    open: bool = False
    #: Carried across deltas: a trailing partial tag, or a deferred block
    #: opener (see ``scan``).  Drained by ``flush``.
    carry: str = ""
    #: Last character consumed BEFORE ``carry`` (or before the next delta
    #: when nothing is carried).  Both guards in ``scan`` need one character
    #: of look-behind -- the inline-code check wants the backtick, the fence
    #: check wants to know whether the buffer starts a line -- and without
    #: this they only ever saw the current buffer.  A delta boundary that
    #: fell between the backtick and the ``<`` therefore defeated the guard:
    #: the backtick was emitted, ``<thi`` was withheld as carry, and the
    #: next call saw a complete opener at offset 0 with nothing before it.
    #: Starts as a newline so the stream begins at a line start.
    prev_char: str = "\n"


# A fenced-code opening line: up to three spaces of indentation then three
# or more backticks, at the start of the buffer or after a newline.
_FENCE_LINE_RE = re.compile(r'(?:^|\n)[ ]{0,3}`{3,}')

# A trailing run that could be the START of a fence line split across
# deltas: line start, optional indentation, one or two backticks.
_PARTIAL_FENCE_TAIL_RE = re.compile(r'(?:^|\n)[ ]{0,3}`{1,2}\Z')


def _line_anchored_start(buf: str, m: 're.Match', at_line_start: bool) -> int:
    """Index where a ``(?:^|\\n)``-anchored match's line begins, or -1 when
    the match sits at offset 0 without the buffer itself being at a line
    start (the buffer began mid-line)."""
    if m.start() == 0 and buf[0] != '\n':
        return 0 if at_line_start else -1
    return m.start() + 1


def _find_fence_start(buf: str, at_line_start: bool) -> int:
    """Index of the first fence opening line in ``buf``, else -1."""
    for m in _FENCE_LINE_RE.finditer(buf):
        i = _line_anchored_start(buf, m, at_line_start)
        if i != -1:
            return i
    return -1


def _pending_fence_prefix_len(buf: str, at_line_start: bool) -> int:
    """Length of a trailing partial fence line (``\\n`` + up to two
    backticks) that must be withheld so a fence split across deltas is
    still recognised, else 0."""
    m = _PARTIAL_FENCE_TAIL_RE.search(buf)
    if not m:
        return 0
    i = _line_anchored_start(buf, m, at_line_start)
    return 0 if i == -1 else len(buf) - i


def _find_first_tag(text: str, tags: Tuple[str, ...]) -> Tuple[int, Optional[str]]:
    """Earliest occurrence of any tag in ``tags``, as ``(index, tag)``.

    Returns ``(-1, None)`` when none is present.
    """
    best_idx, best_tag = -1, None
    for tag in tags:
        i = text.find(tag)
        if i != -1 and (best_idx == -1 or i < best_idx):
            best_idx, best_tag = i, tag
    return best_idx, best_tag


def _pending_prefix_len(text: str, tags: Tuple[str, ...]) -> int:
    """Length of the trailing run of ``text`` that is a PROPER prefix of
    some tag in ``tags``, else 0.

    Checked longest-first so the widest candidate wins.  A complete tag
    returns 0 -- it is located by search, not withheld.
    """
    for n in range(min(len(text), MAX_HOLDBACK), 0, -1):
        tail = text[-n:]
        for tag in tags:
            if len(tail) < len(tag) and tag.startswith(tail):
                return n
    return 0


def scan(
    text: str,
    state: InlineThinkingState,
    timestamp: str = "",
    *,
    final: bool = False,
    in_code_block: bool = False,
) -> Tuple[List[Dict[str, Any]], str]:
    """Split ``text`` into discrete ``thinking`` events and residual text.

    Returns ``(events, remaining_text)``.  Reasoning is emitted as it
    arrives and is NEVER returned in ``remaining_text``, so it does not
    reach the accumulated assistant text and cannot be re-billed on later
    iterations.

    ``in_code_block`` suppresses scanning while the ANSWER is inside a
    fenced block, so a diff or code sample containing these tags as
    content is left intact.  This is the guard the prior ``str.replace``
    lacked -- it rewrote unconditionally, which is how it corrupted diffs
    of this module.  Carry is still drained in that case so a partial tag
    withheld before the fence opened is not stranded.

    A block whose opener is preceded by answer text in the same delta is
    DEFERRED to the next call.  Callers extend their event list before
    appending text events, so emitting both here would order the thinking
    marker ahead of the answer text that precedes it.

    Any closer closes the open block regardless of which opener started
    it.  Strict pairing would strand the remainder of a response inside a
    reasoning block whenever a model closes with a different spelling.
    """
    events: List[Dict[str, Any]] = []
    out: List[str] = []
    buf = state.carry + text
    state.carry = ""

    while buf:
        if state.open:
            at_line_start = state.prev_char == '\n'
            idx, closer = _find_first_tag(buf, CLOSERS)

            # A fenced-code opening line inside a reasoning block is taken
            # as evidence that the block was a misfire -- a prose mention
            # of a tag, or a tag split across deltas that slipped the
            # guards -- rather than reasoning.  Models that reason inline
            # do not emit fenced diffs inside their thinking.  Without this
            # a single false opener stayed open until any closer, so the
            # rest of the answer -- typically a diff -- landed in the
            # thinking panel.  Close the block at the fence and resume text
            # scanning there; the fence itself passes through as content.
            fence = _find_fence_start(buf, at_line_start)
            if fence != -1 and (idx == -1 or fence < idx):
                if fence:
                    events.append({
                        'type': 'thinking',
                        'content': buf[:fence],
                        'timestamp': timestamp,
                    })
                    state.prev_char = buf[fence - 1]
                events.append({
                    'type': 'thinking',
                    'done': True,
                    'timestamp': timestamp,
                })
                state.open = False
                buf = buf[fence:]
                continue

            if idx == -1:
                hold = 0 if final else max(
                    _pending_prefix_len(buf, CLOSERS),
                    _pending_fence_prefix_len(buf, at_line_start),
                )
                keep = len(buf) - hold
                if keep:
                    events.append({
                        'type': 'thinking',
                        'content': buf[:keep],
                        'timestamp': timestamp,
                    })
                    state.prev_char = buf[keep - 1]
                state.carry = buf[keep:]
                break
            if idx:
                events.append({
                    'type': 'thinking',
                    'content': buf[:idx],
                    'timestamp': timestamp,
                })
                state.prev_char = buf[idx - 1]
            events.append({
                'type': 'thinking',
                'done': True,
                'timestamp': timestamp,
            })
            state.open = False
            state.prev_char = '>'
            buf = buf[idx + len(closer):]
            continue

        if in_code_block:
            # Tags are content here.  Pass through verbatim; the caller's
            # fence tracker decides when scanning resumes.
            out.append(buf)
            state.prev_char = buf[-1]
            break

        at_line_start = state.prev_char == '\n'
        idx, opener = _find_first_tag(buf, OPENERS)

        # The caller's ``in_code_block`` is one delta stale: its tracker
        # runs after this scan, so a fence opener arriving in THIS delta
        # is not yet reflected.  A tag in the same delta as the fence --
        # or in a diff line that arrived with it -- was scanned as prose.
        # Detect the fence here: everything from it on is code and passes
        # through; the caller reports in_code_block from the next delta.
        fence = _find_fence_start(buf, at_line_start)
        if fence != -1 and (idx == -1 or fence < idx):
            out.append(buf)
            state.prev_char = buf[-1]
            break

        if idx == -1:
            hold = 0 if final else max(
                _pending_prefix_len(buf, OPENERS),
                _pending_fence_prefix_len(buf, at_line_start),
            )
            keep = len(buf) - hold
            out.append(buf[:keep])
            if keep:
                state.prev_char = buf[keep - 1]
            state.carry = buf[keep:]
            break

        # Inline code span: the model is quoting the tag, not emitting it.
        # Cheap partial cover for unfenced prose mentions.  The look-behind
        # falls back to prev_char so a boundary between the backtick and
        # the tag does not defeat it.
        prev = buf[idx - 1] if idx > 0 else state.prev_char
        if prev == '`':
            out.append(buf[:idx + len(opener)])
            state.prev_char = '>'
            buf = buf[idx + len(opener):]
            continue

        if idx > 0:
            # Answer text precedes the opener -- defer the block so the
            # caller emits this text before any thinking event.
            out.append(buf[:idx])
            state.prev_char = buf[idx - 1]
            state.carry = buf[idx:]
            break

        state.open = True
        state.prev_char = '>'
        buf = buf[len(opener):]

    return events, ''.join(out)


def flush(
    state: InlineThinkingState,
    timestamp: str = "",
) -> Tuple[List[Dict[str, Any]], str]:
    """Drain carried state at end of stream.

    Without this, a stream ending mid-tag -- or immediately after an
    opener that was deferred -- silently loses the carry.

    An unclosed block is left ``open`` for the caller to force-close.  Its
    content has already been emitted as thinking events, so worst case it
    renders in a panel rather than inline; the prior behaviour deleted it
    outright, so this is a strict improvement even when the scanner has
    mistaken a prose mention for a real block.
    """
    if not state.carry:
        return [], ''
    return scan('', state, timestamp, final=True)
