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


def _synthesize_missing_hunk_headers(diff_text: str) -> str:
    """Repair bare / context-anchored hunk headers that lack a numeric range.

    A diff whose hunk header is a bare ``@@`` or ``@@ def foo`` (no
    ``-old,count +new,count`` range) parses to zero hunks downstream, so the
    pipeline reports "parsed to zero hunks". In the GUI this is repaired by the
    frontend's synthesizeMissingHunkHeaders(); in CLI mode that step never runs,
    so the bare header reaches the backend raw. This is the backend port of that
    logic: it emits a 1-based placeholder range with body-derived line counts
    and a ``ZIYA_NOPOS`` sentinel so the parser (synthesized_pos flag) and
    applier (MAX_OFFSET bypass) locate the hunk purely by context. The exact
    counts are not load-bearing: with ZIYA_NOPOS the applier matches by context
    and ignores the offset, and ``_recount_hunks`` runs afterward anyway. Any
    section hint after the ``@@`` is preserved as the locator.
    """
    lines = diff_text.split('\n')
    # Fast path: nothing to do unless some @@ line fails the numeric pattern.
    if not any(l.startswith('@@') and not _HUNK_HEADER_RE.match(l) for l in lines):
        return diff_text

    out: List[str] = []
    for i, line in enumerate(lines):
        if line.startswith('@@') and not _HUNK_HEADER_RE.match(line):
            # Pull any section hint out of the bare "@@ ... [@@]" marker.
            hint = re.sub(r'@@.*$', '', re.sub(r'^@@+', '', line)).strip()
            old_count = 0
            new_count = 0
            for j in range(i + 1, len(lines)):
                b = lines[j]
                if b.startswith('@@') or b.startswith('diff --git'):
                    break
                # Trailing newline artifact from split() — ignore.
                if b == '' and j == len(lines) - 1:
                    continue
                marker = b[:1]
                if marker == '+':
                    new_count += 1
                elif marker == '-':
                    old_count += 1
                elif b.startswith('\\'):
                    pass  # "\ No newline" — counts for neither
                else:
                    old_count += 1
                    new_count += 1  # context (incl. blank lines)
            out.append(
                f'@@ -1,{old_count} +1,{new_count} @@ ZIYA_NOPOS'
                + (f' {hint}' if hint else '')
            )
            logger.info(
                "diff_preprocessor: synthesized ZIYA_NOPOS header for bare hunk"
                " (hint=%r, -1,%d +1,%d)", hint, old_count, new_count
            )
        else:
            out.append(line)
    return '\n'.join(out)


def preprocess_diff(diff_text: str, original_file_lines: Optional[List[str]] = None) -> tuple:
    """Run all preprocessing passes on *diff_text*.

    Returns (cleaned_diff, converted) where *converted* is True when
    additive-to-replace conversion was applied (body lines changed, not
    just header recounting).
    """
    # Pass 0: synthesize numeric placeholder headers for bare "@@" hunks so the
    # downstream parser sees a parseable range instead of yielding zero hunks.
    diff_text = _synthesize_missing_hunk_headers(diff_text)
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

        # A diff that ends with a trailing newline yields a final '' element
        # from split('\n').  When this hunk runs to the end of the diff, that
        # '' is a split artifact, not a real context line — counting it inflates
        # old_count/new_count by 1 and produces a header git apply rejects when
        # the hunk has no trailing context to absorb the offset (e.g. a pure
        # replacement).  Exclude it from the count while leaving it in `body`
        # so the re-joined output keeps its trailing newline.
        count_body = (
            body[:-1]
            if (i == len(lines) and body and body[-1] == '')
            else body
        )

        ctx = 0
        rem = 0
        add = 0
        for bline in count_body:
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
        # When a pure-addition hunk declares more old-side lines than the body
        # shows (small mismatch, not truncation), the model counted trailing blank
        # lines in old_count but omitted them from the visible context.  Append
        # one blank removal line so patch consumes that orphaned trailing blank
        # instead of leaving it behind.  The context match offset is typically
        # within fuzz=2 so the hunk still applies cleanly.
        last_add_idx = max((i for i, l in enumerate(body) if l.startswith('+')), default=-1)
        has_trailing_context = any(
            not l.startswith(('+', '-', '\\')) and l != ''
            for l in body[last_add_idx + 1:]
        )
        if rem == 0 and 0 < (header_old_count - body_old) < 10 and not has_trailing_context:
            # Insert before the first addition — unified diff requires removals
            # to precede additions within a hunk (appending after + lines is
            # rejected as malformed by the OS patch binary).
            first_add = next((i for i, l in enumerate(body) if l.startswith('+')), len(body))
            body.insert(first_add, '-')
            rem = 1
            # Strip the trailing split artifact now — with the removal line
            # inserted, that phantom '' context line would produce a wrong
            # header count and a malformed trailing context match at EOF.
            if body and body[-1] == '':
                body.pop()
            # Recount from the cleaned body so header stays consistent.
            ctx = sum(1 for l in body if not l.startswith(('+', '-', '\\')) and l != '')
            add = sum(1 for l in body if l.startswith('+'))
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

