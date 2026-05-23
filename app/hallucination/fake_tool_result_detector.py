"""Detector for fabricated tool-*result* payloads inside fenced code blocks.

Layer C of the hallucination defense.  Layers A (shingle parroting) and B
(fake shell session) cover other shapes; this module covers the case where
the model invents a tool *result* dict — Python or JSON — and embeds it
inside a fenced block (often ``python``, ``json``, or untagged) as if the
tool had executed.

Live example from a real session log::

    Encrypted. Let me write a quick script to decrypt and search:```python
    {'success': True, 'message': 'Created /tmp/find_noisy_chat.py (1,605 bytes)',
     'path': '/tmp/find_noisy_chat.py', 'bytes_written': 1605}
    </```|python

No real ``file_write`` had executed in that session, so Layer A had no
fingerprint to match.  Layer B's shell heuristics did not apply because the
fence was tagged ``python`` and the body had no ``$`` prompt.  This module
catches that gap.

Heuristic: any fenced block whose first non-blank line is a Python-dict or
JSON-shaped payload containing canonical Ziya tool-result keys (``success``,
``path``, ``bytes_written``, ``message``, ``error``, ``tool_input``,
``stdout``, ``stderr``, ``returncode``, ``exit_code``) — when no real tool
with a matching shape has executed in the current iteration — is treated as
a fabricated tool-result echo.

Specificity is high: legitimate user code very rarely contains a literal
``{'success': True, 'bytes_written': N, 'path': '...'}`` shape outside of
test fixtures, and even there the surrounding context is recognizable.  The
detector also requires *at least two* canonical keys to fire, which keeps
single-key incidental dicts (``{'success': fn()}``, ``{'path': p}``) from
triggering.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Optional, Sequence

logger = logging.getLogger(__name__)

# Canonical keys that appear in real Ziya tool results.  The tool-result
# schema is consistent across MCP shell tools, the file_* family, and the
# fake-tool dispatcher in streaming_tool_executor.py.
_TOOL_RESULT_KEYS = (
    'success', 'path', 'bytes_written', 'message', 'error',
    'tool_input', 'stdout', 'stderr', 'returncode', 'exit_code',
    'output', 'result', 'command', 'cwd',
)

# A first-line dict/JSON probe — matches both ``{'success': True, ...}``
# (Python repr) and ``{"success": true, ...}`` (JSON).  We do not try to
# fully parse — we just want to know whether the line *looks like* a
# tool-result dict that opens with one of our canonical keys.
_DICT_OPEN_RE = re.compile(
    r'^\s*\{\s*[\'"](?P<key>' + '|'.join(_TOOL_RESULT_KEYS) + r')[\'"]\s*:'
)

# Standalone "key: value" probe for the second-line check (handles cases
# where the model splits the dict across lines).
_DICT_KEY_RE = re.compile(
    r'^\s*[\'"]?(?P<key>' + '|'.join(_TOOL_RESULT_KEYS) + r')[\'"]?\s*[:=]'
)

# Fences we *do not* probe — these are reserved for legitimate structural
# content (diffs, patches) where false positives would be especially costly.
_SKIP_FENCE_LANGS = frozenset({'diff', 'patch', 'tool'})


@dataclass
class FakeToolResultMatch:
    """Result of a positive detection."""
    confidence: str          # 'high' | 'medium' | 'low'
    fence_lang: str          # The language tag (or '' for untagged)
    matched_keys: tuple      # Canonical keys we recognized in the body
    snippet: str             # Up to ~200 chars of the matched body for logging
    reason: str              # Human-readable explanation


def detect_fake_tool_result(
    fence_lang: str,
    body: str,
    *,
    real_tool_results_seen: Optional[Sequence[str]] = None,
) -> Optional[FakeToolResultMatch]:
    """Scan a fenced block body for the fabricated-tool-result signature.

    Args:
        fence_lang: The language tag from the opening fence (``python``,
            ``json``, ``''`` for untagged, etc.).
        body: The raw body inside the fence, between opener and closer.
        real_tool_results_seen: Optional sequence of tool names whose real
            results have already been registered this iteration.  When
            provided, we suppress detection if the matched keys plausibly
            belong to one of those tools (we only have name-level
            granularity — finer matching belongs in Layer A's shingle
            index).

    Returns:
        A :class:`FakeToolResultMatch` when the body looks fabricated, else
        ``None``.
    """
    if not body or not body.strip():
        return None
    if fence_lang.lower() in _SKIP_FENCE_LANGS:
        return None

    lines = [ln for ln in body.split('\n') if ln.strip()]
    if not lines:
        return None

    first = lines[0]
    second = lines[1] if len(lines) > 1 else ''

    # Primary signal: first non-blank line opens a dict/JSON literal whose
    # leading key is one of our canonical tool-result keys.
    primary = _DICT_OPEN_RE.match(first)
    if not primary:
        return None

    matched = [primary.group('key')]

    # Collect additional keys from the rest of the body.  We bound the
    # scan to keep this O(body) — a real tool-result dict is small; if the
    # block is huge it almost certainly is not what we are looking for.
    scan_lines = lines[:50]
    for ln in scan_lines[1:]:
        m = _DICT_KEY_RE.match(ln)
        if m and m.group('key') not in matched:
            matched.append(m.group('key'))

    # Also catch single-line dicts where multiple keys appear on the first
    # line (the most common shape from real tool dispatchers).
    inline_keys = re.findall(
        r'[\'"](?P<k>' + '|'.join(_TOOL_RESULT_KEYS) + r')[\'"]\s*:',
        first,
    )
    for k in inline_keys:
        if k not in matched:
            matched.append(k)

    if len(matched) < 2:
        return None  # Single canonical key is not enough — could be incidental.

    # Confidence shaping.  Higher confidence when:
    #   * The fence is tagged ``python`` or ``json`` (the languages the
    #     model most often picks when faking a result).
    #   * The body fits on one line (typical of repr() output).
    #   * The first key is ``success`` (overwhelmingly the leading key in
    #     real Ziya tool results).
    confidence = 'medium'
    lang_l = fence_lang.lower()
    if matched[0] == 'success':
        confidence = 'high'
    if lang_l in ('python', 'json') and matched[0] == 'success':
        confidence = 'high'
    if len(matched) >= 4:
        confidence = 'high'
    if lang_l == '':
        # Untagged fence is more ambiguous — keep below 'high' unless the
        # signal is very strong.
        if confidence == 'high' and len(matched) < 4:
            confidence = 'medium'

    # Suppress if the keys plausibly belong to a tool that *did* run.  This
    # is a coarse check — Layer A is the precise one — but it covers the
    # case where the model is legitimately echoing real output verbatim.
    if real_tool_results_seen:
        names = {n.lower() for n in real_tool_results_seen}
        if 'file_write' in names and {'path', 'bytes_written'}.issubset(matched):
            return None
        if 'run_shell_command' in names and {'stdout', 'returncode'}.issubset(matched):
            return None

    snippet = body[:200].replace('\n', '\\n')
    reason = (
        f"first non-blank line is a dict literal opening with canonical "
        f"tool-result key {matched[0]!r}; total recognized keys "
        f"{tuple(matched)}; fence_lang={fence_lang!r}"
    )
    return FakeToolResultMatch(
        confidence=confidence,
        fence_lang=fence_lang,
        matched_keys=tuple(matched),
        snippet=snippet,
        reason=reason,
    )


__all__ = ['FakeToolResultMatch', 'detect_fake_tool_result']
