"""CommonMark inline code-span pairing for the completion scanner.

Two routines in the streaming executor decide whether a response that
stopped cleanly is nonetheless truncated: ``_has_unclosed_inline_code``
(text ends inside an open backtick span) and, upstream of the fence
tracker, ``_normalize_fence_spacing`` (which inserts a blank line before a
glued ``\\`\\`\\`lang`` opener).  Neither knew that inline code spans exist.

Observed failure (2026-09-07, claude-fable-5): a reply containing the span

    ```` ```diff ````

-- a 4-backtick span whose CONTENT is three literal backticks -- was judged
"open_inline" (the fence-stripping regex ``\\`\\`\\`.*?\\`\\`\\``` ate the wrong
three of the four, leaving a lone backtick and an odd count), so the
executor injected a "finish the code block" prompt.  The reply to THAT
mentioned the same span; the fence normaliser matched ``\\`\\`\\`diff`` inside
it and inserted a blank line, which turned the span's interior into a real
fence opener at line start, so the tracker reported "open_fence" and the
prompt fired again.  Three rounds of the model explaining that nothing was
cut off were appended to the answer.

CommonMark (section 6.1): a code span opens with a run of N backticks and
closes with the NEXT run of exactly N backticks.  Runs of any other length
in between are literal content.  A run with no equal-length partner is
literal text, not an opener.  A span cannot cross a blank line.  This
module implements exactly that and nothing more.
"""

from __future__ import annotations

import re
from typing import List, Tuple

_RUN_RE = re.compile(r'`+')

# A fence line: <=3 spaces of indent, a run of 3+ backticks or tildes, and
# for backtick fences an info string that contains no backtick (CommonMark
# 4.5).  ``\`\`\`\` \`\`\`diff \`\`\`\``` at line start therefore is NOT a
# fence -- it is a paragraph holding a code span.
_FENCE_LINE_RE = re.compile(r'^ {0,3}(`{3,}|~{3,})(.*)$')


def backtick_runs(text: str) -> List[Tuple[int, int]]:
    """``(start, length)`` of every maximal backtick run in ``text``."""
    return [(m.start(), m.end() - m.start()) for m in _RUN_RE.finditer(text)]


def pair_code_spans(text: str) -> Tuple[List[Tuple[int, int]], List[Tuple[int, int]]]:
    """Pair backtick runs the CommonMark way.

    Returns ``(spans, unmatched)``: ``spans`` as ``(start, end)`` character
    offsets covering delimiters and content; ``unmatched`` as the
    ``(start, length)`` runs that found no equal-length partner and are
    therefore literal text.
    """
    runs = backtick_runs(text)
    spans: List[Tuple[int, int]] = []
    unmatched: List[Tuple[int, int]] = []
    i = 0
    while i < len(runs):
        start, length = runs[i]
        j = i + 1
        while j < len(runs) and runs[j][1] != length:
            j += 1
        if j < len(runs):
            spans.append((start, runs[j][0] + length))
            i = j + 1
        else:
            unmatched.append((start, length))
            i += 1
    return spans, unmatched


def has_unmatched_run(text: str) -> bool:
    """True if any backtick run in ``text`` has no equal-length partner.

    Used mid-stream, where an unmatched run may be a span whose closer has
    not arrived yet.  Callers that would otherwise rewrite text near a
    fence-looking run treat this as "leave it alone".
    """
    return bool(pair_code_spans(text)[1])


def last_paragraph(text: str) -> str:
    """The text after the last blank line.  A code span cannot cross one,
    so nothing before it can bear on whether the tail is inside a span."""
    idx = -1
    for m in re.finditer(r'\n[ \t]*\n', text):
        idx = m.end()
    return text if idx == -1 else text[idx:]


def strip_fenced_blocks(text: str) -> str:
    """Remove complete fenced code blocks, line-based.

    A closer is a line whose run is at least as wide as the opener's.
    CommonMark forbids text after a closer, but a model that closes a
    fence and keeps writing on the same line (``\\`\\`\\` then \\`x``) has
    closed it as far as the reader is concerned, so that text is kept as
    prose.  A fence with no closer swallows the remainder: the caller's
    fence tracker already reports no open fence when this is consulted,
    so a disagreement resolves toward "no evidence" rather than a guess.
    """
    out: List[str] = []
    fence_char = ''
    fence_len = 0
    for line in text.split('\n'):
        m = _FENCE_LINE_RE.match(line)
        if fence_char:
            if (m and m.group(1)[0] == fence_char
                    and len(m.group(1)) >= fence_len):
                fence_char = ''
                if m.group(2).strip():
                    out.append(m.group(2))
            continue
        if m:
            marker, info = m.group(1), m.group(2)
            if marker[0] == '~' or '`' not in info:
                fence_char, fence_len = marker[0], len(marker)
                continue
        out.append(line)
    return '\n'.join(out)


def ends_inside_code_span(text: str) -> bool:
    """True if ``text`` ends inside an inline code span that never closed.

    Fenced blocks are removed first (their backticks are content), then
    only the final paragraph is examined.  The verdict is "truncated" when
    the LAST backtick run in that paragraph has no equal-length partner:
    the span opened and the text ran out before it closed.
    """
    if not text or '`' not in text:
        return False
    para = last_paragraph(strip_fenced_blocks(text))
    runs = backtick_runs(para)
    if not runs:
        return False
    _, unmatched = pair_code_spans(para)
    return bool(unmatched) and unmatched[-1] == runs[-1]
