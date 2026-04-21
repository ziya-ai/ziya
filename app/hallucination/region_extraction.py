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


# Fenced code block open/close: ``` or ~~~ (3 or more of either char).
_FENCE_RE = re.compile(r'^(\s*)(`{3,}|~{3,})(.*)$')

# Indented code block: 4+ leading spaces or a leading tab.
_INDENT_BLOCK_RE = re.compile(r'^(    |\t)')

# Blockquote: optional leading whitespace then >.
_BLOCKQUOTE_RE = re.compile(r'^\s*>')


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
            if (
                m
                and fence_marker is not None
                and m.group(2).startswith(fence_marker[0])
                and len(m.group(2)) >= len(fence_marker)
            ):
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
