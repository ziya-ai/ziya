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
            idx, closer = _find_first_tag(buf, CLOSERS)
            if idx == -1:
                hold = 0 if final else _pending_prefix_len(buf, CLOSERS)
                keep = len(buf) - hold
                if keep:
                    events.append({
                        'type': 'thinking',
                        'content': buf[:keep],
                        'timestamp': timestamp,
                    })
                state.carry = buf[keep:]
                break
            if idx:
                events.append({
                    'type': 'thinking',
                    'content': buf[:idx],
                    'timestamp': timestamp,
                })
            events.append({
                'type': 'thinking',
                'done': True,
                'timestamp': timestamp,
            })
            state.open = False
            buf = buf[idx + len(closer):]
            continue

        if in_code_block:
            # Tags are content here.  Pass through verbatim; the caller's
            # fence tracker decides when scanning resumes.
            out.append(buf)
            break

        idx, opener = _find_first_tag(buf, OPENERS)
        if idx == -1:
            hold = 0 if final else _pending_prefix_len(buf, OPENERS)
            keep = len(buf) - hold
            out.append(buf[:keep])
            state.carry = buf[keep:]
            break

        # Inline code span: the model is quoting the tag, not emitting it.
        # Cheap partial cover for unfenced prose mentions.
        if idx > 0 and buf[idx - 1] == '`':
            out.append(buf[:idx + len(opener)])
            buf = buf[idx + len(opener):]
            continue

        if idx > 0:
            # Answer text precedes the opener -- defer the block so the
            # caller emits this text before any thinking event.
            out.append(buf[:idx])
            state.carry = buf[idx:]
            break

        state.open = True
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
