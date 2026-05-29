"""Truncation helper for task-executor artifact summaries.

The d3 task post-mortem highlighted that ``task_executor`` was
silently slicing the model's final summary at 2000 chars — cutting
mid-sentence with no indication, so the user couldn't tell whether
the model had stopped or the system had truncated.

This module raises the cap to a useful size (large enough to hold
a multi-section iteration log without ever truncating in normal
use) and, when the cap *is* hit, prefers a soft cut at a paragraph
or sentence boundary and appends an explicit truncation marker
with the elided character count.

Pure function, no I/O — tested in ``tests/test_artifact_summary_truncation.py``.
"""

from __future__ import annotations

# Default cap (chars).  Sized to comfortably hold a multi-section
# iteration log (the kind of summary the d3 task tried to emit)
# without truncation in normal cases, while still bounding the
# pathological worst case where a model emits megabytes of text.
SUMMARY_CAP: int = 50_000

# How far back from the cap we'll look for a paragraph or sentence
# boundary before giving up and hard-cutting.  Chosen so the search
# is bounded but generous enough that real prose almost always
# finds a clean boundary.
_SOFT_BOUNDARY_WINDOW: int = 1_500


def truncate_summary(text: str, cap: int | None = None) -> str:
    """Return ``text`` truncated to ``cap`` characters with a marker.

    If ``len(text) <= cap`` the text is returned unchanged.  When
    truncation is necessary, the cut is moved back to the nearest
    paragraph break (``\\n\\n``) within ``_SOFT_BOUNDARY_WINDOW``
    of the cap, falling back to a sentence break (``. ``) and
    finally to a hard slice.  A standardised marker line is then
    appended so the truncation is visually obvious.

    ``cap`` of ``None``, ``0`` or negative falls back to ``SUMMARY_CAP``.
    """
    if not isinstance(text, str) or not text:
        return text or ""
    effective_cap = cap if (cap and cap > 0) else SUMMARY_CAP
    full_len = len(text)
    if full_len <= effective_cap:
        return text

    # Search for a paragraph break first — most readable cut point.
    search_start = max(0, effective_cap - _SOFT_BOUNDARY_WINDOW)
    cut = text.rfind("\n\n", search_start, effective_cap)
    if cut < 0:
        # Fall back to a sentence break.  ``rfind`` gives the last
        # occurrence within [start, end); add 2 to land *after* the
        # period+space rather than before it.
        cut = text.rfind(". ", search_start, effective_cap)
        if cut >= 0:
            cut += 2
    if cut < 0:
        # No clean boundary — hard cut at the cap.
        cut = effective_cap

    head = text[:cut].rstrip()
    elided = full_len - cut
    marker = (
        f"\n\n[summary truncated by Ziya: showed {cut:,} of "
        f"{full_len:,} chars; {elided:,} chars elided]"
    )
    return head + marker
