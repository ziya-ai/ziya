"""
Region extraction for hallucination detection.

Given assistant text, returns the portions that are scannable for tool-result
parroting patterns -- i.e., the portions NOT inside Markdown code fences,
indented code blocks, blockquotes, or inline backtick spans. Those regions
are where the user expects natural-language prose and where tool-output-
shaped text is suspicious.

Design decisions:
- Markdown block-level constructs are excluded line-by-line.
- Inline backtick spans are excluded character-by-character within a line.
- Over-exclusion is preferred: false negatives (missed hallucinations) are
  recoverable; false positives (flagging legitimate analytical prose)
  damage user trust in the detector.
- Indented code blocks are detected purely by leading whitespace (4 spaces
  or a tab). This over-excludes vs. strict CommonMark (which requires a
  preceding blank line), which is the safe direction.
"""
from __future__ import annotations

import re
from typing import NamedTuple


# Fenced code block open/close: ``` or ~~~ (3 or more of either char).
_FENCE_RE = re.compile(r'^(\s*)(`{3,}|~{3,})(.*)$')

# Indented code block: 4+ leading spaces or a leading tab.
_INDENT_BLOCK_RE = re.compile(r'^(    |\t)')

# Blockquote: optional leading whitespace then >.
_BLOCKQUOTE_RE = re.compile(r'^\s*>')


def _is_fence_close(m: "re.Match[str] | None", fence_marker: str | None) -> bool:
    """
    True iff line match ``m`` is a valid CLOSE for an open fence whose
    opening marker was ``fence_marker``.

    Single source of truth for close-line semantics across all fence
    scanners in this module (extract_scannable_regions, scannable_line_indices,
    open_fence_at). A close line must:
      * be a fence line at all (``m`` truthy, an open fence in progress);
      * use the same fence character as the opener;
      * be at least as wide as the opener (CommonMark width discipline);
      * carry NO info string -- only trailing whitespace is allowed after
        the fence characters. A line like ```` ```bash ```` is therefore an
        OPENING fence, never a close, so it cannot terminate a fence it did
        not open. group(3) is the trailing-content capture of _FENCE_RE.
    """
    return bool(
        m
        and fence_marker is not None
        and m.group(2).startswith(fence_marker[0])
        and len(m.group(2)) >= len(fence_marker)
        and m.group(3).strip() == ''
    )


def extract_scannable_regions(text: str) -> list[str]:
    """
    Return scannable region strings in order of appearance.

    Regions are the portions of ``text`` outside Markdown code constructs
    and blockquotes, with inline backtick spans stripped.
    """
    regions: list[str] = []
    current: list[str] = []
    in_fence = False
    fence_marker: str | None = None

    def flush() -> None:
        nonlocal current
        if current:
            regions.append(''.join(current))
            current = []

    for line in text.splitlines(keepends=True):
        if in_fence:
            m = _FENCE_RE.match(line)
            if _is_fence_close(m, fence_marker):
                in_fence = False
                fence_marker = None
            flush()
            continue

        m = _FENCE_RE.match(line)
        if m:
            in_fence = True
            fence_marker = m.group(2)
            flush()
            continue

        if _INDENT_BLOCK_RE.match(line):
            flush()
            continue

        if _BLOCKQUOTE_RE.match(line):
            flush()
            continue

        current.append(_strip_inline_code(line))

    flush()
    return regions


def scannable_text(text: str) -> str:
    """
    Convenience wrapper returning the concatenation of scannable regions,
    joined by newlines. Suitable for feeding into regex-based detection.
    """
    return '\n'.join(extract_scannable_regions(text))


def scannable_line_indices(text: str) -> list[tuple[int, str]]:
    """
    Per-line variant of ``extract_scannable_regions``.

    Returns ``(line_index, line)`` pairs for each line of ``text`` (split
    on newlines) that is scannable -- outside fenced code blocks, indented
    code blocks, and blockquotes -- with inline backtick spans stripped.
    Fence delimiter lines are not scannable.

    Intended for consumers that need to map detection hits back to line
    positions in the original text (e.g. truncating assistant text at the
    first fabricated line), which the region-string API cannot express.

    Fence semantics match ``extract_scannable_regions``: a fence closes
    only on the same character with at least the opening width, so
    narrower fences quoted inside a wider fence are inert content.
    """
    out: list[tuple[int, str]] = []
    in_fence = False
    fence_marker: str | None = None

    for i, line in enumerate(text.split('\n')):
        if in_fence:
            m = _FENCE_RE.match(line)
            if _is_fence_close(m, fence_marker):
                in_fence = False
                fence_marker = None
            continue

        m = _FENCE_RE.match(line)
        if m:
            in_fence = True
            fence_marker = m.group(2)
            continue

        if _INDENT_BLOCK_RE.match(line) or _BLOCKQUOTE_RE.match(line):
            continue

        out.append((i, _strip_inline_code(line)))

    return out


def open_fence_at(text: str, position: int) -> str | None:
    """
    Return the open fence marker (e.g. ``'```'`` or ``'~~~~'``) governing
    ``position`` in ``text``, or ``None`` if the position is not inside a
    fenced code block.

    Scans the lines preceding ``position`` with the same width-disciplined
    semantics as the other scanners in this module: a fence closes only on
    the same character with at least the opening width, so narrower fences
    quoted inside a wider fence are inert content. Fences do NOT nest --
    inside an open fence, every line that is not a valid closer is content
    (CommonMark), so a quoted opener never creates a phantom nesting level.

    Intended for consumers that need fence state at a character offset
    (e.g. choosing a safe continuation split point), which the per-line
    and region APIs do not express.
    """
    in_fence = False
    fence_marker: str | None = None

    for line in text[:position].split('\n'):
        if in_fence:
            m = _FENCE_RE.match(line)
            if _is_fence_close(m, fence_marker):
                in_fence = False
                fence_marker = None
            continue

        m = _FENCE_RE.match(line)
        if m:
            in_fence = True
            fence_marker = m.group(2)

    return fence_marker if in_fence else None


# ---------------------------------------------------------------------------
# Detector-compat fence extraction.
#
# NOTE: this deliberately uses a NARROWER fence grammar than _FENCE_RE above.
# It exists to give the fake-shell detector a single shared extraction walk
# without changing that detector's behavior. Specifically, versus _FENCE_RE
# it recognises ONLY backtick fences (no ~~~) with NO leading indentation,
# and a close line is backticks-only followed by whitespace, NOT "same char
# with trailing content allowed". Those are exactly the rules the detector's
# private regex used.
#
# Reconciling this grammar with _FENCE_RE / open_fence_at (so the whole
# module speaks one CommonMark-faithful dialect) is a follow-up behavior
# change tracked separately -- it widens detection to ~~~ and indented
# fences, which must be reviewed and tested on its own.
# ---------------------------------------------------------------------------
_DETECTOR_FENCE_OPEN_RE = re.compile(r'^(`{3,})', re.MULTILINE)


class FencedRegion(NamedTuple):
    """One fenced code block found by :func:`extract_fenced_regions`."""
    opening_line: str   # full opening fence line, sans trailing newline
    marker: str         # the backtick run that opened the fence, e.g. 3 backticks
    body: str           # text between the opening line and the close (exclusive)
    closed: bool        # True iff a matching close fence was found


def extract_fenced_regions(text: str) -> list[FencedRegion]:
    """
    Return the backtick-fenced code blocks in *text*, in order.

    Both completed and trailing-unclosed fences are returned: an unclosed
    fence (the stream may still be arriving) is emitted as the final region
    with ``closed=False`` and a body running to end of text. This mirrors
    the fake-shell detector's need to fire on in-progress fences, where
    token-by-token output delivery is itself the fabrication signal.

    A fence whose opening marker is not yet followed by a newline (the text
    ends on the opening line) is NOT emitted -- there is no body to scan.

    Close semantics match CommonMark width discipline: the close line must
    be a run of at least as many backticks as the opener, optionally
    followed by whitespace only.
    """
    regions: list[FencedRegion] = []
    pos = 0
    while pos < len(text):
        open_m = _DETECTOR_FENCE_OPEN_RE.search(text, pos)
        if open_m is None:
            break
        open_line_end = text.find('\n', open_m.start())
        if open_line_end == -1:
            break  # opener with no newline yet -- no body to scan
        opening_line = text[open_m.start():open_line_end]
        marker = open_m.group(1)
        body_start = open_line_end + 1
        close_re = re.compile(rf'^`{{{len(marker)},}}\s*$', re.MULTILINE)
        close_m = close_re.search(text, body_start)
        if close_m is None:
            regions.append(
                FencedRegion(opening_line, marker, text[body_start:], False)
            )
            break
        regions.append(
            FencedRegion(opening_line, marker, text[body_start:close_m.start()], True)
        )
        pos = close_m.end()
    return regions


def _strip_inline_code(line: str) -> str:
    """
    Remove inline code spans (text between backticks) from a line.

    Supports multi-backtick markers per CommonMark (e.g. double-backtick
    spans containing a literal backtick). Unclosed spans are preserved.
    """
    out: list[str] = []
    i = 0
    n = len(line)
    while i < n:
        if line[i] == '`':
            marker_len = 0
            while i + marker_len < n and line[i + marker_len] == '`':
                marker_len += 1
            marker = '`' * marker_len
            close_idx = line.find(marker, i + marker_len)
            if close_idx == -1:
                out.append(line[i:i + marker_len])
                i += marker_len
            else:
                i = close_idx + marker_len
        else:
            out.append(line[i])
            i += 1
    return ''.join(out)
