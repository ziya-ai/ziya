"""
Phantom diff references: prose that cites a diff this response never emitted.

The harness already polices two neighbouring failures.  A model that
*narrates* a tool call without making one trips the hallucination detector
(app/hallucination), and a model that emits a diff that does not apply gets
validation feedback and a correction pass (diff_validation_hook).  Between
them sat a class nothing checked: a response whose prose says "the diff
above", "apply the diff", "(diff above)" while the response contains no
```diff fence at all.  The model decided the change in its reasoning and
then produced a tool call (writing the test, the changelog) instead of the
text the diff had to be; the changelog line it wrote even cited the diff.

Measured across this project's own history, that shape recurred in eight
conversations between 2026-09-19 and 2026-10-12, each surfacing only when
the user asked "did you apply it?" (see tests/test_phantom_diff_reference.py
for the stored turn that motivated this module).  It is structural, not a
one-off: a Writable-path edit is a tool call, a non-writable one is response
text, and in a tool-call cadence the text step is the one that gets skipped.

``find_phantom_diff_reference`` is the detector; ``phantom_diff_feedback``
is the correction request the validation hook returns so the existing
feedback-and-restart path asks for the missing fence (or for a one-sentence
statement that the change was made some other way).
"""

from __future__ import annotations

import re
from typing import Optional

# Reasoning blocks are streamed inline and only sanitized to markers later
# (app/utils/sanitizer_util.py).  Planning language there -- "I'll put the
# diff below" -- is not a claim to the user, so it is removed before matching.
_THINKING_RE = re.compile(r"<thinking-data>.*?</thinking-data>", re.DOTALL)

# Any ```diff / ~~~diff opener, closed or not.  An unterminated fence means
# the model DID start one (the stream may have been cut); that is a
# different failure and must not be reported as a phantom.
_DIFF_FENCE_OPEN_RE = re.compile(r"(?m)^\s*(?:`{3,}|~{3,})\s*(?:diff|patch|udiff)\b")

# Claims that a diff is present in THIS response.  Deliberately narrow:
# "the diff from last turn" or "that diff had issues" (the model answering
# validation feedback) must not match, so every pattern asserts position
# in the current response ("above"/"below") or an act of providing it.
_REFERENCE_PATTERNS = [
    re.compile(r"\bdiffs?\b(?:[^.\n]{0,40}?)\b(?:above|below)\b", re.IGNORECASE),
    re.compile(r"\b(?:above|below)\b(?:[^.\n]{0,20}?)\bdiffs?\b", re.IGNORECASE),
    re.compile(
        r"\bdiffs?\b(?:[^.\n]{0,20}?)\b(?:I|I've|I have|that I)\s+"
        r"(?:provided|emitted|wrote|written|gave|given|posted|included)\b",
        re.IGNORECASE,
    ),
    re.compile(r"\bapply(?:ing)?\s+the\s+diffs?\b", re.IGNORECASE),
]


def _prose_only(content: str) -> str:
    """Response text with reasoning and every fenced block removed.

    A diff cited inside a code sample, a quoted tool result or a changelog
    body is content, not a claim; app.hallucination's region extraction
    already defines "outside every fence" for the parroting scan, so the
    same definition is used here.
    """
    text = _THINKING_RE.sub(" ", content or "")
    try:
        from app.hallucination.region_extraction import scannable_text
        return scannable_text(text)
    except Exception:  # noqa: BLE001 -- fall back to the raw text
        return text


def has_diff_fence(content: str) -> bool:
    """True when the response contains any diff-family fence opener."""
    return bool(_DIFF_FENCE_OPEN_RE.search(_THINKING_RE.sub(" ", content or "")))


def find_phantom_diff_reference(content: str) -> Optional[str]:
    """The first phrase in ``content`` that cites a diff the response lacks.

    Returns ``None`` when the response carries a diff fence (closed or not),
    or when no prose outside fences and reasoning claims one.  Otherwise the
    matched phrase, trimmed, for the feedback message and the log.
    """
    if not content or has_diff_fence(content):
        return None
    prose = _prose_only(content)
    for pat in _REFERENCE_PATTERNS:
        m = pat.search(prose)
        if m:
            return " ".join(m.group(0).split())[:80]
    return None


def phantom_diff_feedback(phrase: str) -> str:
    """Correction request for a response that cited a diff it never emitted.

    Worded so a false positive costs one sentence: a model that made the
    change through a direct write says so and stops.  A true positive gets
    the fence that was decided in reasoning but never written.
    """
    return (
        f"Your response refers to a diff (\"{phrase}\") but contains no "
        f"```diff fence. Nothing was emitted: a diff that exists only in your "
        f"reasoning has not been produced, and the user sees only the "
        f"response text. Either emit the diff now, as a ```diff fence for the "
        f"file it changes, or state in one sentence that the change was made "
        f"another way (a direct write) and no diff is needed."
    )
