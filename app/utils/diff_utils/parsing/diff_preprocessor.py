"""
Diff preprocessor: sanitize malformed diffs before entering the pipeline.

Two transformations:
1. additive_to_replace  – When a hunk has ONLY additions (no `-` lines) and an
   added line is very similar to the preceding context line, convert the context
   line to a `-` line (turning the addition into a proper replacement).
2. recount_hunks – Recalculate old_count/new_count from actual hunk body lines
   so that `patch` / `git apply` do not choke on wrong counts.
"""

import re
from difflib import SequenceMatcher
from typing import List, Optional

from app.utils.logging_utils import logger

_HUNK_HEADER_RE = re.compile(r'^@@ -(\d+),?(\d*) \+(\d+),?(\d*) @@(.*)$')
_DIFF_GIT_RE = re.compile(r'^diff --git ')


def preprocess_diff(diff_text: str, original_file_lines: Optional[List[str]] = None) -> tuple:
    """Run all preprocessing passes on *diff_text*.

    Returns (cleaned_diff, converted) where *converted* is True when
    additive-to-replace conversion was applied (body lines changed, not
    just header recounting).
    """
    result, converted = _additive_to_replace(diff_text, original_file_lines)
    result = _recount_hunks(result)
    return result, converted


# ---------------------------------------------------------------------------
# Pass 1: additive-insert  ->  replace
# ---------------------------------------------------------------------------

def _similarity(a: str, b: str) -> float:
    """Return 0-1 similarity ratio between two stripped lines."""
    return SequenceMatcher(None, a.strip(), b.strip()).ratio()

_SIMILARITY_THRESHOLD = 0.85


def _additive_to_replace(diff_text: str, original_file_lines: Optional[List[str]] = None) -> str:
    """
    Detect hunks that are pure additions where each added line is very similar
    to its preceding context line, and convert the context->addition pair into
    a removal->addition (i.e. a replacement).

    When conversions happen, subsequent hunk new_start offsets are adjusted
    to account for the reduced net line change.
    """
    lines = diff_text.split('\n')
    out: List[str] = []
    i = 0
    # Tracks the accumulated shift in new_start caused by conversions
    # within the current file.  Reset on each diff --git header.
    ns_adjust = 0
    any_converted = False

    while i < len(lines):
        if _DIFF_GIT_RE.match(lines[i]):
            ns_adjust = 0
            out.append(lines[i])
            i += 1
            continue

        m = _HUNK_HEADER_RE.match(lines[i])
        if not m:
            out.append(lines[i])
            i += 1
            continue

        # Matched a hunk header – collect body
        old_start = int(m.group(1))
        new_start = int(m.group(3))
        suffix = m.group(5)
        i += 1
        body_start = i
        while i < len(lines) and not _HUNK_HEADER_RE.match(lines[i]) and not _DIFF_GIT_RE.match(lines[i]):
            i += 1
        body = lines[body_start:i]

        # Count original additions/removals before any conversion
        orig_add = sum(1 for b in body if b.startswith('+'))
        orig_rem = sum(1 for b in body if b.startswith('-'))
        orig_net = orig_add - orig_rem

        if orig_rem == 0:
            # No removal lines — scan for context+addition pairs where the
            # added line closely resembles its preceding context line.
            #
            # Only apply when ALL non-trivial additions are paired with a
            # similar context line — this distinguishes the "forgot minus"
            # pattern from legitimate pure-addition hunks.
            new_body: List[str] = []
            convert_count = 0
            total_nontrivial_adds = 0
            for bline in body:
                if bline.startswith('+') and new_body:
                    add_content = bline[1:].strip()
                    if len(add_content) >= 4:
                        total_nontrivial_adds += 1
                        prev_idx = len(new_body) - 1
                        while prev_idx >= 0 and new_body[prev_idx].startswith(('+', '-', '\\')):
                            prev_idx -= 1
                        if prev_idx >= 0:
                            prev_ctx = new_body[prev_idx]
                            ctx_content = prev_ctx[1:].strip() if len(prev_ctx) > 0 else ''
                            if len(ctx_content) >= 4 and \
                               not prev_ctx.startswith(('+', '-', '\\')) and \
                               _similarity(prev_ctx, bline[1:]) >= _SIMILARITY_THRESHOLD:
                                new_body[prev_idx] = '-' + prev_ctx[1:]
                                convert_count += 1
                new_body.append(bline)

            # Count non-trivial context lines to check ratio
            total_nontrivial_ctx = sum(
                1 for b in body
                if not b.startswith(('+', '-', '\\')) and len(b.strip()) >= 4
            )
            # Require: all non-trivial adds paired AND conversions cover
            # at least half the non-trivial context (filters out single
            # matches buried in large context blocks).
            converted = (
                convert_count > 0
                and total_nontrivial_adds > 0
                and convert_count >= total_nontrivial_adds
                and convert_count * 2 >= total_nontrivial_ctx
            )
            if converted:
                body = new_body
                any_converted = True
                logger.info("diff_preprocessor: converted additive-insert to replace in hunk at line %d", old_start)
        else:
            converted = False

        # Emit header — only recount and adjust offsets when conversion happened
        new_net = orig_net
        if converted:
            # Conversion happened — recount and adjust new_start
            ctx = 0
            r = 0
            a = 0
            for bline in body:
                if bline.startswith('-'):
                    r += 1
                elif bline.startswith('+'):
                    a += 1
                elif bline.startswith('\\'):
                    pass
                else:
                    ctx += 1
            old_count = ctx + r
            new_count = ctx + a
            new_net = a - r
            adjusted_ns = new_start + ns_adjust
            out.append(f'@@ -{old_start},{old_count} +{adjusted_ns},{new_count} @@{suffix}')
        else:
            # No conversion — preserve original header, just apply ns_adjust
            adjusted_ns = new_start + ns_adjust
            orig_old_count = m.group(2)
            orig_new_count = m.group(4)
            oc_str = f',{orig_old_count}' if orig_old_count else ''
            nc_str = f',{orig_new_count}' if orig_new_count else ''
            out.append(f'@@ -{old_start}{oc_str} +{adjusted_ns}{nc_str} @@{suffix}')
        out.extend(body)

        ns_adjust += (new_net - orig_net)

    return '\n'.join(out), any_converted

# ---------------------------------------------------------------------------
# Pass 2: recount hunk headers from actual body lines
# ---------------------------------------------------------------------------

def _recount_hunks(diff_text: str) -> str:
    """
    Walk every hunk header and recompute old_count / new_count from the
    actual body lines.  This fixes diffs where the LLM emitted wrong counts.
    """
    lines = diff_text.split('\n')
    out: List[str] = []
    i = 0

    while i < len(lines):
        m = _HUNK_HEADER_RE.match(lines[i])
        if not m:
            out.append(lines[i])
            i += 1
            continue

        # Matched a hunk header – collect body and recount
        old_start = int(m.group(1))
        header_old_count = int(m.group(2)) if m.group(2) else 1
        new_start = int(m.group(3))
        suffix = m.group(5)
        i += 1
        body_start = i
        while i < len(lines) and not _HUNK_HEADER_RE.match(lines[i]) and not _DIFF_GIT_RE.match(lines[i]):
            i += 1
        body = lines[body_start:i]

        ctx = 0
        rem = 0
        add = 0
        for bline in body:
            if bline.startswith('-'):
                rem += 1
            elif bline.startswith('+'):
                add += 1
            elif bline.startswith('\\'):
                pass
            else:
                ctx += 1

        body_old = ctx + rem
        body_new = ctx + add
        # Detect truncated diffs: when the header claims significantly more
        # old-side lines than the body contains, the LLM omitted trailing
        # context.  Preserve the header count so downstream full-file
        # replacement detection can still trigger.  Only for large
        # truncations (10+ missing lines) to avoid interfering with
        # normal small-hunk count mismatches.
        if header_old_count > body_old and body_old < header_old_count * 0.8 and (header_old_count - body_old) >= 10:
            old_count = header_old_count
            # The missing lines are omitted context — they appear on both sides
            new_count = body_new + (header_old_count - body_old)
        else:
            old_count = body_old
            new_count = body_new
        out.append(f'@@ -{old_start},{old_count} +{new_start},{new_count} @@{suffix}')
        out.extend(body)

    return '\n'.join(out)

