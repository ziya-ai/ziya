"""Which fenced block, if any, is open at the end of already-streamed text.

``_normalize_fence_spacing`` (app/streaming_tool_executor.py) must not touch
text inside a fenced block.  It asks the code-block tracker whether the chunk
starts inside one, but the tracker runs on the content optimizer's OUTPUT and
the optimizer holds a block back until it closes (``StreamingContentOptimizer``
never flushes while its buffer has an odd number of fence runs).  So for the
whole body of a streaming ```mermaid block the tracker still says "outside",
and only the inline-code-span guard -- paragraph-scoped by design -- kept the
normaliser off the body.  Past the first blank line inside the block it did
not (chat 32dfc463, 2026-11-04: a label quoting ```diff was split in two).

``assistant_text`` is ahead of the tracker: it already holds the opener.  This
module reads the open fence off that text, scanning only the complete lines
added since the previous call (state cached on the dict the caller passes,
normally the tracker itself).

CommonMark 4.5: an opener is <=3 spaces, a run of 3+ backticks or tildes and
an info string (no backtick in it for a backtick fence); the closer is the same
character, at least as long, nothing else on the line.  One tightening,
matching the tracker: the closer's indent may not exceed the opener's, so a
" ```" diff context line inside a column-0 ```diff block is content.
"""

from __future__ import annotations

import re
from typing import Dict, Optional, Tuple

_OPEN_RE = re.compile(r'^( {0,3})(`{3,}|~{3,})(.*)$')

# (indent, fence char, run length)
OpenFence = Tuple[int, str, int]

_LEN_KEY = '_fence_state_len'
_OPEN_KEY = '_fence_state_open'


def open_fence_at_end(text: str, cache: Dict) -> Optional[OpenFence]:
    """The fence open after the last complete line of ``text``, or None.

    ``cache`` carries the scan position and state between calls; it is reset
    when ``text`` is shorter than what was scanned (the caller truncated it,
    e.g. the hallucination-recovery path).  A trailing partial line is not
    scanned until its newline arrives.
    """
    pos = cache.get(_LEN_KEY, 0)
    state: Optional[OpenFence] = cache.get(_OPEN_KEY)
    if pos > len(text):
        pos, state = 0, None
    end = text.rfind('\n') + 1
    if end > pos:
        for line in text[pos:end].split('\n')[:-1]:
            if state is None:
                m = _OPEN_RE.match(line)
                if m and not (m.group(2)[0] == '`' and '`' in m.group(3)):
                    state = (len(m.group(1)), m.group(2)[0], len(m.group(2)))
            elif closes(line, state):
                state = None
        cache[_LEN_KEY] = end
        cache[_OPEN_KEY] = state
    return state


def closer_re(fence: OpenFence) -> re.Pattern:
    """Multiline regex matching a line that closes ``fence``."""
    indent, ch, n = fence
    return re.compile(r'^ {0,%d}%s{%d,}[ \t]*$' % (indent, re.escape(ch), n), re.M)


def closes(line: str, fence: OpenFence) -> bool:
    return closer_re(fence).match(line) is not None
