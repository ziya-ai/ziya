"""
CLI diff applicator - Interactive diff application for terminal.
"""
import logging
import os
import re
import sys
from typing import List, Tuple, Optional, Set
from app.utils.logging_utils import logger
from app.utils.interruptible_input import interruptible_sigint as _interruptible_sigint


def _render_failure_diagnostics(failures: list) -> str:
    """Format hunk failure list into a concise diagnostic string for CLI output.

    Returns an empty string when there is nothing useful to show (e.g. empty
    failures list), so callers can do ``if rendered: return False, rendered``.
    """
    if not failures:
        return ""
    parts = []
    for i, failure in enumerate(failures, 1):
        msg = failure.get("message", "Unknown error")
        details = failure.get("details") or {}
        confidence = details.get("confidence")
        hunk_num = details.get("hunk_number") or details.get("hunk") or i
        line = f"  Hunk #{hunk_num}: {msg}"
        # confidence may arrive as a float (0.0–1.0), a numeric string, or a
        # non-numeric token ('N/A', 'low') depending on which pipeline stage
        # produced the failure. Only percent-format a real number — a bare
        # `{confidence:.0%}` on a str raises "Unknown format code 'f' for
        # object of type 'str'" and crashes the failure reporter itself,
        # masking the actual hunk-mismatch message.
        if confidence is not None:
            try:
                line += f" (confidence: {float(confidence):.0%})"
            except (TypeError, ValueError):
                line += f" (confidence: {confidence})"
        parts.append(line)
    header = f"Failed to apply ({len(failures)} hunk{'s' if len(failures) != 1 else ''} unmatched):"
    return "\n".join([header] + parts)


def _hunk_ranges_from_diff(diff_content: str) -> List[Tuple[int, int]]:
    """Extract (start, end) line ranges in the *new* file from a unified diff.

    Reads ``@@ -a,b +c,d @@`` headers and returns ``(c, c+d-1)`` for each
    hunk.  Returns an empty list if no headers are found.
    """
    ranges: List[Tuple[int, int]] = []
    for m in re.finditer(r"^@@\s+-\d+(?:,\d+)?\s+\+(\d+)(?:,(\d+))?\s+@@", diff_content, re.MULTILINE):
        start = int(m.group(1))
        length = int(m.group(2)) if m.group(2) else 1
        ranges.append((start, start + max(length - 1, 0)))
    return ranges


def _error_line_numbers(error_msg: str) -> List[int]:
    """Extract line numbers from compiler diagnostics like ``foo.tsx(123,4):``.

    Also matches the ``: line N`` form used by the basic JS bracket
    validator.  Returns an empty list when nothing is found.
    """
    lines: Set[int] = set()
    for m in re.finditer(r"\((\d+),\d+\)", error_msg):
        lines.add(int(m.group(1)))
    for m in re.finditer(r"\bline\s+(\d+)\b", error_msg, re.IGNORECASE):
        lines.add(int(m.group(1)))
    return sorted(lines)


def _cascading_error_hint(diff_content: str, error_msg: str, threshold: int = 100) -> str:
    """Return a hint when error lines are far from every diff hunk.

    Downstream parse errors after a structural break (e.g. an
    object-literal key emitted at statement position) often surface
    hundreds of lines below the actual edit, which makes the model
    chase phantom problems near the reported line numbers instead of
    re-examining its diff.  When the minimum distance from any error
    line to any hunk range exceeds ``threshold``, prepend a hint
    pointing the model back at the diff itself.

    Returns an empty string when there's nothing to flag.
    """
    hunks = _hunk_ranges_from_diff(diff_content)
    err_lines = _error_line_numbers(error_msg)
    if not hunks or not err_lines:
        return ""

    def _dist(line: int) -> int:
        return min(
            0 if start <= line <= end else min(abs(line - start), abs(line - end))
            for start, end in hunks
        )

    min_dist = min(_dist(L) for L in err_lines)
    if min_dist < threshold:
        return ""

    hunk_desc = ", ".join(f"{s}-{e}" for s, e in hunks)
    err_desc = ", ".join(str(L) for L in err_lines[:5]) + ("..." if len(err_lines) > 5 else "")
    return (
        f"NOTE: The reported error lines ({err_desc}) are {min_dist}+ lines away "
        f"from your diff's hunks ({hunk_desc}).  This is almost always a "
        f"cascading parse error caused by a structural break introduced by "
        f"the diff itself (e.g. a misplaced token at statement position).  "
        f"Re-examine the diff content rather than the reported line numbers.\n\n"
    )


class DiffBlock:
    """Represents a single diff block from markdown."""
    
    def __init__(self, content: str, start_pos: int, end_pos: int):
        self.content = content
        self.start_pos = start_pos
        self.end_pos = end_pos
        self.file_path = self._extract_file_path()
    
    def _extract_file_path(self) -> Optional[str]:
        """Extract target file path from diff.
        
        For modifications/creations, returns the +++ path.
        For deletions (+++ /dev/null), returns the --- source path.
        """
        source_path = None
        for line in self.content.split('\n'):
            if line.startswith('--- a/'):
                source_path = line[6:].strip()
            elif line.startswith('--- ') and not line.startswith('--- /dev/null'):
                path = line[4:].strip()
                if path.startswith('a/'):
                    path = path[2:]
                if path and path != '/dev/null':
                    source_path = path
            elif line.startswith('+++ /dev/null') or line.startswith('+++ b/dev/null'):
                # File deletion — return the source path
                self.is_deletion = True
                return source_path
            elif line.startswith('+++ b/'):
                return line[6:].strip()
            elif line.startswith('+++ '):
                path = line[4:].strip()
                if path.startswith('b/'):
                    path = path[2:]
                if path and path != '/dev/null':
                    return path
        return None
    
    def get_preview(self, lines: int = 10) -> str:
        """Get a preview of the diff."""
        diff_lines = self.content.split('\n')
        preview_lines = diff_lines[:lines]
        if len(diff_lines) > lines:
            preview_lines.append(f'... ({len(diff_lines) - lines} more lines)')
        return '\n'.join(preview_lines)


class CLIDiffApplicator:
    """
    Interactive diff applicator for CLI mode.
    
    Extracts diffs from markdown responses and prompts user
    to apply them one at a time.
    """
    
    def __init__(self):
        self.applied_count = 0
        self.skipped_count = 0
        self.partial_count = 0
        self.failed_count = 0
        self.diff_results = []  # List of (file_path, status, message) tuples
    
    def extract_diffs(self, markdown: str) -> List[DiffBlock]:
        """
        Extract all diff blocks from markdown response.

        Walks the response in two passes: (1) properly fenced
        triple-backtick ``diff`` fenced blocks (the canonical form), then
        (2) bare unified-diff blocks the model emitted without a
        fence.  The bare-diff fallback only fires when no fenced
        block was found AND the bare block has the full structural
        signature of a real unified diff.

        Args:
            markdown: The markdown content to parse
        
        Returns:
            List of DiffBlock objects
        """
        # Extract diff blocks by tracking fence open/close rather than regex,
        # so backtick fences inside the diff content don't cause early termination.
        diffs = []
        lines = markdown.split('\n')
        i = 0
        while i < len(lines):
            line = lines[i]
            stripped = line.strip()
            # Match opening fence: 3+ backticks followed by 'diff', with
            # optional trailing text (```diff python, ```diff # comment).
            # Models frequently emit junk after the language tag; the
            # content is still an intended diff block, so extract it here
            # with proper fence-delimited boundaries rather than letting
            # the bare-diff fallback recover it (which fires only when no
            # fenced block was found, and recovers at most one block).
            if re.match(r'^`{3,}diff(?:\s|$)', stripped):
                fence_len = len(stripped.split('diff')[0])  # number of backticks
                start_pos = sum(len(l) + 1 for l in lines[:i])
                i += 1
                content_lines = []
                # Collect until matching closing fence (same or more backticks, nothing else)
                closed = False
                while i < len(lines):
                    close_stripped = lines[i].strip()
                    if re.match(r'^`{' + str(fence_len) + r',}\s*$', close_stripped):
                        closed = True
                        break
                    content_lines.append(lines[i])
                    i += 1
                end_pos = sum(len(l) + 1 for l in lines[:i + 1])
                diff_content = '\n'.join(content_lines).strip()
                # An unterminated diff fence (no closing ```) means the stream
                # was truncated mid-emission. The collector above ran to EOF and
                # swallowed all trailing prose into the diff body. Rather than
                # capture runaway content, trim the partial block back to its
                # diff-shaped lines so following prose is not consumed.
                if diff_content and not closed:
                    diff_content = self._trim_to_diff_shape(content_lines)
                if diff_content:
                    diffs.append(DiffBlock(content=diff_content, start_pos=start_pos, end_pos=end_pos))
            i += 1

        # Fallback: model omitted the fence.  Only attempt recovery
        # when no fenced block was found, to avoid double-extracting
        # the same content.  Strict structural validation
        # (``_extract_bare_unified_diff``) keeps prose that merely
        # quotes a diff from being treated as one.
        if not diffs:
            bare = self._extract_bare_unified_diff(markdown, lines)
            if bare is not None:
                content, start_pos, end_pos = bare
                diffs.append(DiffBlock(content=content, start_pos=start_pos, end_pos=end_pos))
                logger.info(
                    "Recovered unfenced unified diff (%d chars) — "
                    "model omitted the ```diff fence", len(content),
                )

        logger.debug(f"Extracted {len(diffs)} diff blocks from response")
        return diffs

    @staticmethod
    def _trim_to_diff_shape(content_lines: List[str]) -> str:
        """Trim a runaway (unterminated-fence) diff body to its leading
        diff-shaped run.

        When a diff fence is never closed (truncated stream), the collector
        swallows trailing prose. This keeps only the contiguous leading
        lines that look like a unified diff (header lines, hunk headers, and
        body lines), stopping at the first line that breaks the diff shape.
        """
        kept: List[str] = []
        seen_hunk = False
        for ln in content_lines:
            if re.match(r'^@@ -\d+(?:,\d+)? \+\d+(?:,\d+)? @@', ln):
                seen_hunk = True
                kept.append(ln)
                continue
            if not seen_hunk:
                if ln.startswith(('diff --git ', '--- ', '+++ ', 'index ',
                                  'new file mode', 'deleted file mode',
                                  'similarity index', 'rename ')):
                    kept.append(ln)
                    continue
                break
            if ln.startswith(('+', '-', ' ', '\\')):
                kept.append(ln)
                continue
            break
        return '\n'.join(kept).strip()

    @staticmethod
    def _extract_bare_unified_diff(
        markdown: str, lines: List[str],
    ) -> Optional[Tuple[str, int, int]]:
        """Recover an un-fenced unified diff from a model response.

        Strict — accepts only blocks with the full structural shape
        of a real ``git diff`` so prose that quotes diffs is not
        mistaken for an applicable one:

          1. ``diff --git a/PATH b/PATH`` line at column 0
          2. ``--- ...`` line shortly after, at column 0
          3. ``+++ ...`` line shortly after, at column 0
          4. At least one ``@@ -N,M +N,M @@`` hunk header
          5. Every body line after the first hunk header begins with
             ``+``, ``-``, space, backslash, or another ``@@``.
             First non-conforming line ends the block.

        Returns ``(content, start_pos, end_pos)`` on success, or
        ``None`` when no clean bare diff is found.
        """
        # 1. Find ``diff --git a/X b/Y`` at column 0.
        start = None
        for idx, line in enumerate(lines):
            if line.startswith('diff --git a/') and ' b/' in line:
                start = idx
                break
        if start is None:
            return None

        # 2 & 3. Within the next few lines, locate ``---`` and ``+++``
        #        headers at column 0 (allow ``index``/mode lines between).
        minus_idx = plus_idx = None
        for idx in range(start + 1, min(start + 6, len(lines))):
            ln = lines[idx]
            if minus_idx is None and ln.startswith('--- '):
                minus_idx = idx
                continue
            if minus_idx is not None and ln.startswith('+++ '):
                plus_idx = idx
                break
        if minus_idx is None or plus_idx is None:
            return None

        # 4. The first hunk header must follow within a few lines.
        hunk_idx = None
        for idx in range(plus_idx + 1, min(plus_idx + 4, len(lines))):
            if re.match(r'^@@ -\d+(?:,\d+)? \+\d+(?:,\d+)? @@', lines[idx]):
                hunk_idx = idx
                break
        if hunk_idx is None:
            return None

        # 5. Body — collect contiguous diff-shaped lines after the first
        #    hunk header.  First line that isn't ``+``, ``-``, ` ``,
        end = hunk_idx + 1
        while end < len(lines):
            ln = lines[end]
            if ln == '':
                # Blank line is ambiguous: tolerate a single blank line
                # between hunks but not a paragraph break out of the diff.
                # If the next non-blank line is diff-shaped, keep going.
                next_nonblank = next(
                    (lines[k] for k in range(end + 1, len(lines))
                     if lines[k] != ''),
                    None,
                )
                if next_nonblank is None:
                    end += 1
                    break
                if next_nonblank.startswith(
                    ('+', '-', ' ', '\\', '@@', 'diff --git ')
                ):
                    end += 1
                    continue
                break
            if ln.startswith(('+', '-', ' ', '\\')) or \
               re.match(r'^@@ -\d+(?:,\d+)? \+\d+(?:,\d+)? @@', ln) or \
               ln.startswith('diff --git '):
                end += 1
                continue
            break

        # Compute byte offsets in the original markdown.
        start_pos = sum(len(l) + 1 for l in lines[:start])
        end_pos = sum(len(l) + 1 for l in lines[:end])
        content = '\n'.join(lines[start:end]).rstrip('\n')
        if not content:
            return None
        return content, start_pos, end_pos
    
    @staticmethod
    def _parse_hunk_ranges(diff_content: str) -> List[Tuple[int, int]]:
        """
        Parse @@ headers to get the original-file line ranges this diff touches.
        
        Returns a list of (start, end) tuples (inclusive, 1-based).
        For new file creation (start=0, count=0) returns an empty list
        since there's no original-side range to compare.
        """
        ranges = []
        for match in re.finditer(r'@@ -(\d+)(?:,(\d+))? \+', diff_content):
            start = int(match.group(1))
            count = int(match.group(2)) if match.group(2) is not None else 1
            if start == 0 and count == 0:
                # New file creation — no original lines to overlap with
                continue
            end = start + max(count - 1, 0)
            ranges.append((start, end))
        return ranges
    
    @staticmethod
    def _ranges_overlap(a: List[Tuple[int, int]], b: List[Tuple[int, int]]) -> bool:
        """Return True if any range in *a* overlaps any range in *b*."""
        for a_start, a_end in a:
            for b_start, b_end in b:
                if a_start <= b_end and b_start <= a_end:
                    # Calculate actual overlap size. Adjacent hunks sharing
                    # only a few lines of context are complementary changes,
                    # not revisions. Require >50% of the smaller hunk to
                    # overlap before treating it as a superseding revision.
                    overlap_start = max(a_start, b_start)
                    overlap_end = min(a_end, b_end)
                    overlap_size = overlap_end - overlap_start + 1
                    smaller_hunk = min(a_end - a_start + 1, b_end - b_start + 1)
                    if smaller_hunk > 0 and overlap_size / smaller_hunk > 0.5:
                        return True
        return False
    
    @staticmethod
    def _is_sequential_pair(earlier_diff: str, later_diff: str) -> bool:
        """Check if two overlapping diffs are sequential (first prepares
        for the second) rather than the later superseding the earlier.

        Heuristic: if the earlier diff is predominantly subtractive in the
        overlapping region (removing code to make way) and the later diff
        adds new code, they're complementary steps, not revisions.
        """
        earlier_adds = 0
        earlier_removes = 0
        later_adds = 0
        for line in earlier_diff.splitlines():
            if line.startswith('@@') or line.startswith('diff ') or line.startswith('---') or line.startswith('+++'):
                continue
            if line.startswith('+'):
                earlier_adds += 1
            elif line.startswith('-'):
                earlier_removes += 1
        for line in later_diff.splitlines():
            if line.startswith('@@') or line.startswith('diff ') or line.startswith('---') or line.startswith('+++'):
                continue
            if line.startswith('+'):
                later_adds += 1
        # Earlier is predominantly a deletion and later adds new content
        return earlier_removes > 0 and earlier_adds <= 1 and later_adds > 0

    def _deduplicate_diffs(self, diffs: List[DiffBlock]) -> List[DiffBlock]:
        """
        When the model revises a diff, both the original and the corrected
        version end up in the response.  Detect this by checking for
        overlapping hunk ranges within the same file and keep only the
        later (corrected) version.
        
        Two diffs for the same file that target *different* line ranges
        are treated as independent changes and both kept.
        
        Diffs whose file path cannot be determined are always kept.
        """
        if len(diffs) <= 1:
            return diffs
        
        # Pre-parse hunk ranges for every diff
        parsed_ranges = [self._parse_hunk_ranges(d.content) for d in diffs]
        
        # Debug: show what dedup is comparing
        for idx, d in enumerate(diffs):
            print(f"\033[90m  [dedup] diff {idx}: file={d.file_path} ranges={parsed_ranges[idx]}\033[0m")

        # Walk backwards: for each diff, check if a *later* diff for the
        # same file has overlapping hunks.  If so, mark the earlier one
        # as superseded.
        superseded: Set[int] = set()
        for i in range(len(diffs)):
            if diffs[i].file_path is None:
                continue
            for j in range(i + 1, len(diffs)):
                if diffs[j].file_path != diffs[i].file_path:
                    continue
                if not parsed_ranges[i] and not parsed_ranges[j]:
                    # Both are new-file diffs for the same path — later wins
                    superseded.add(i)
                    break
                if self._ranges_overlap(parsed_ranges[i], parsed_ranges[j]):
                    # Exact duplicate — drop the earlier one
                    if diffs[i].content.strip() == diffs[j].content.strip():
                        # Identical content: drop the later duplicate (j),
                        # keeping the first occurrence (i).
                        superseded.add(j)
                        break
                    if self._is_sequential_pair(diffs[i].content, diffs[j].content):
                        continue  # complementary, not superseding
                    # j came later in the conversation — it supersedes i.
                    superseded.add(i)
                    break
        
        if not superseded:
            return diffs
        
        for idx in sorted(superseded):
            path = diffs[idx].file_path
            print(
                f"\033[33m⊘ Skipping earlier diff for {path} "
                f"(superseded by a later revision)\033[0m"
            )
        
        return [d for i, d in enumerate(diffs) if i not in superseded]
    
    def _print_diff_preview(self, diff: DiffBlock, number: int, total: int):
        """Print a preview of the diff."""
        print(f"\n\033[36m{'─' * 60}\033[0m")
        print(f"\033[1;36mDiff {number}/{total}\033[0m")
        
        if diff.file_path:
            print(f"\033[90mFile: {diff.file_path}\033[0m")
        else:
            print(f"\033[33mWarning: Could not detect file path\033[0m")
        
        print(f"\033[36m{'─' * 60}\033[0m")
        
        # Show preview
        preview = diff.get_preview(15)
        print(preview)
        
        # Show truncation indicator if needed
        total_lines = len(diff.content.split('\n'))
        if total_lines > 15:
            print(f"\033[90m... ({total_lines - 15} more lines)\033[0m")
        
        print(f"\033[36m{'─' * 60}\033[0m")
        return total_lines > 15
    
    def _prompt_user_action(self, show_view: bool = True, remaining: int = 1) -> str:
        """
        Prompt user for action on current diff.
        
        Returns:
            User's choice: 'a' (apply), 'A' (apply all), 's' (skip), 'v' (view), 'q' (quit)
        """
        while True:
            try:
                with _interruptible_sigint():
                    response = input(
                        "\n\033[1mAction:\033[0m "
                        "\033[32m[a]\033[0mpply"
                        + (" / \033[32m[A]\033[0mpply all" if remaining > 1 else "")
                        + " / "
                        "\033[33m[s]\033[0mkip / "
                        + ("\033[36m[v]\033[0miew full / " if show_view else "")
                        +
                        "\033[31m[q]\033[0muit? "
                    ).strip()
                
                if response == 'A' and remaining > 1:
                    return 'A'
                choice = response.lower()
                valid = ['a', 's', 'q'] + (['v'] if show_view else [])
                if choice in valid:
                    return choice
                else:
                    print("\033[90mInvalid choice. Please enter a, s, v, or q.\033[0m")
            except (EOFError, KeyboardInterrupt):
                print()
                return 'q'
    
    def _apply_diff(self, diff: DiffBlock) -> Tuple[bool, str]:
        """
        Apply a diff using the existing diff application utilities.
        
        Args:
            diff: The diff to apply
            
        Returns:
            (success, message) tuple
        """
        if not diff.file_path:
            return False, "Could not determine file path"
        
        # Handle file deletion diffs
        if getattr(diff, 'is_deletion', False):
            codebase_dir = os.environ.get("ZIYA_USER_CODEBASE_DIR", os.getcwd())
            full_path = os.path.join(codebase_dir, diff.file_path)
            if os.path.exists(full_path):
                try:
                    os.remove(full_path)
                    return True, f"Deleted file: {diff.file_path}"
                except OSError as e:
                    return False, f"Could not delete {diff.file_path}: {e}"
            else:
                return True, f"File already absent: {diff.file_path}"
        
        # Temporarily suppress logging from diff application to avoid ugly output
        # Save original log levels
        diff_logger = logging.getLogger('app.utils.diff_utils')
        original_level = diff_logger.level
        
        # Suppress all diff_utils logs in CLI mode
        if os.environ.get("ZIYA_MODE") == "chat":
            diff_logger.setLevel(logging.CRITICAL + 1)  # Suppress everything
        
        try:
            # Get the full file path
            codebase_dir = os.environ.get("ZIYA_USER_CODEBASE_DIR", os.getcwd())
            full_path = os.path.join(codebase_dir, diff.file_path)
            
            # Use the atomic application function
            from app.utils.diff_utils.application.git_diff import apply_diff_atomically
            
            result = apply_diff_atomically(full_path, diff.content)
            
            # apply_diff_atomically returns None when it can't handle the diff
            if result is None:
                # Fall back to per-hunk application — one bad hunk shouldn't
                # reject the entire diff when others may apply cleanly.
                partial_result = self._apply_hunks_individually(full_path, diff.content, diff.file_path)
                if partial_result is not None:
                    return partial_result
                return False, "Diff could not be parsed or applied"
            
            status = result.get("status")
            if status == "success":
                details = result.get("details", {})
                if details.get("new_file"):
                    return True, f"Created new file: {diff.file_path}"
                elif details.get("already_applied"):
                    return True, f"Changes already applied to {diff.file_path}"
                else:
                    return True, f"Successfully applied to {diff.file_path}"
            elif status == "partial":
                # Some hunks may have applied, others failed
                failures = result.get("failures", [])
                applied_count = result.get("applied_hunks", 0)
                failed_count = len(failures)
                
                if applied_count == 0:
                    # Nothing applied — this is a complete failure, not partial
                    msg_parts = [f"Failed to apply to {diff.file_path}"]
                    msg_parts.append(f"(0/{failed_count} hunks succeeded)")
                    if failures:
                        first_failure = failures[0].get("message", "Unknown error")
                        msg_parts.append(f"First failure: {first_failure}")
                    return False, " - ".join(msg_parts)
                else:
                    # Genuine partial success
                    msg_parts = [f"Partially applied to {diff.file_path}"]
                    msg_parts.append(f"({applied_count} hunks succeeded, {failed_count} failed)")
                    if failures:
                        first_failure = failures[0].get("message", "Unknown error")
                        msg_parts.append(f"First failure: {first_failure}")
                    return True, " - ".join(msg_parts)
            else:
                # Clean, user-friendly error message
                error_details = result.get("details", {})
                failures = result.get("failures", [])

                # Render structured diagnostics for low-confidence / fuzzy-verify
                # failures so the user (and any LLM driving the CLI) sees *why*
                # the hunk didn't match, not just that it didn't.
                rendered = _render_failure_diagnostics(failures)
                if rendered:
                    return False, rendered

                # Generic error message
                error = error_details.get("message") or error_details.get("error") or "Unknown error"
                # Prepend a cascading-error hint when the failure is a
                # language validation error pointing far from the diff.
                hint = _cascading_error_hint(diff.content, str(error))
                if "hunks failed" in str(error).lower():
                    return False, hint + "Some changes couldn't be applied (file content mismatch)"
                
                return False, f"{hint}Failed: {error}"
                
        except Exception as e:
            # Before giving up completely, try per-hunk fallback
            try:
                partial_result = self._apply_hunks_individually(full_path, diff.content, diff.file_path)
                if partial_result is not None:
                    return partial_result
            except Exception:
                pass
            return False, f"Error: {str(e).split(':')[0]}"
        finally:
            # Restore original log level
            diff_logger.setLevel(original_level)
    
    def _apply_hunks_individually(
        self, full_path: str, diff_content: str, file_path: str
    ) -> Optional[Tuple[bool, str]]:
        """
        Fallback: apply each hunk in a multi-hunk diff independently.
        
        When atomic (all-at-once) application fails, this tries each hunk
        in sequence against the current file state. Hunks that match are
        applied; hunks that fail are reported but don't block the others.
        
        Returns:
            (success, message) tuple if any hunks were processed, or None
            if the diff couldn't be parsed into individual hunks.
        """
        try:
            from app.utils.diff_utils.application.git_diff import apply_diff_atomically
        except ImportError:
            return None
        
        # Split the diff into individual hunks by @@ headers
        lines = diff_content.split('\n')
        header_lines = []
        hunk_starts = []
        
        for i, line in enumerate(lines):
            if line.startswith('@@'):
                hunk_starts.append(i)
            elif not hunk_starts:
                header_lines.append(line)
        
        if len(hunk_starts) <= 1:
            # Single hunk — no point retrying individually
            return None
        
        header = '\n'.join(header_lines)
        
        # Extract each hunk as a separate diff
        hunks = []
        for idx, start in enumerate(hunk_starts):
            end = hunk_starts[idx + 1] if idx + 1 < len(hunk_starts) else len(lines)
            hunk_lines = lines[start:end]
            hunks.append('\n'.join(hunk_lines))
        
        succeeded = 0
        failed = 0
        first_failure = ""
        
        # Apply each hunk as a standalone diff
        for i, hunk_body in enumerate(hunks, 1):
            single_hunk_diff = header + '\n' + hunk_body
            
            try:
                result = apply_diff_atomically(full_path, single_hunk_diff)
                if result and result.get("status") in ("success", "partial"):
                    applied = result.get("applied_hunks", 1) if result.get("status") == "partial" else 1
                    if applied > 0:
                        succeeded += 1
                    else:
                        failed += 1
                        if not first_failure:
                            failures = result.get("failures", [])
                            first_failure = failures[0].get("message", f"Hunk #{i} failed") if failures else f"Hunk #{i} failed"
                else:
                    failed += 1
                    if not first_failure:
                        failures = (result or {}).get("failures", [])
                        first_failure = failures[0].get("message", f"Hunk #{i} failed") if failures else f"Hunk #{i} failed"
            except Exception as e:
                failed += 1
                if not first_failure:
                    first_failure = f"Hunk #{i}: {str(e)[:80]}"
        
        if succeeded == 0:
            return None  # Nothing worked — let caller report the original error
        
        if failed == 0:
            return True, f"Successfully applied to {file_path}"
        
        # Partial success
        msg = f"Partially applied to {file_path} - ({succeeded} hunks succeeded, {failed} failed) - First failure: {first_failure}"
        return True, msg

    def process_response(self, response: str) -> bool:
        """
        Process a response containing diffs and prompt user for actions.
        
        Args:
            response: The full response text
            
        Returns:
            True if processing completed normally, False if user quit
        """

        # Reset counters for this response
        self.applied_count = 0
        self.skipped_count = 0
        self.partial_count = 0
        self.failed_count = 0
        self.diff_results = []

        # Extract all diffs
        diffs = self.extract_diffs(response)
        
        print(f"\033[90m[trace] extract_diffs found {len(diffs)} block(s), "
              f"with_path={sum(1 for d in diffs if d.file_path)}, pathless={sum(1 for d in diffs if not d.file_path)}\033[0m", file=sys.stderr)

        if not diffs:
            # No diffs to process
            return True
        
        # Drop diffs with no detectable file path — these are typically
        # illustrative snippets, not applicable changes.
        pathless = [d for d in diffs if not d.file_path]
        if pathless:
            print(
                f"\033[90m⊘ Skipping {len(pathless)} diff(s) with no detectable file path\033[0m"
            )
            diffs = [d for d in diffs if d.file_path]
            if not diffs:
                return True
        
        # Drop earlier diffs whose hunks were superseded by later revisions
        diffs = self._deduplicate_diffs(diffs)
        
        print(f"\n\033[1mFound {len(diffs)} diff(s) in response\033[0m")
        
        accept_all = False

        # Process each diff one at a time
        for i, diff in enumerate(diffs, 1):
            is_truncated = self._print_diff_preview(diff, i, len(diffs))
            
            # Prompt for action
            while True:
                remaining = len(diffs) - i + 1
                action = 'a' if accept_all else self._prompt_user_action(show_view=is_truncated, remaining=remaining)
                
                if action == 'q':
                    print(f"\n\033[90mStopping. Processed {i-1}/{len(diffs)} diffs.\033[0m")
                    self._print_summary()
                    return False
                
                elif action == 'A':
                    accept_all = True
                    action = 'a'

                elif action == 's':
                    print(f"\033[33m⊘ Skipped\033[0m")
                    self.skipped_count += 1
                    self.diff_results.append((diff.file_path, "skipped", "Skipped by user"))
                    break
                
                elif action == 'v':
                    # Show full diff
                    print(f"\n\033[36m{'─' * 60}\033[0m")
                    print(diff.content)
                    print(f"\033[36m{'─' * 60}\033[0m")
                    # Continue loop to prompt again
                    continue
                
                elif action == 'a':
                    # Apply the diff
                    print(f"\033[90mApplying...\033[0m")
                    success, message = self._apply_diff(diff)
                    
                    if success:
                        # Distinguish partial application (some hunks failed)
                        # from full success. _apply_diff returns success=True
                        # for both, but partial results begin with the
                        # "Partially applied" prefix and warrant a yellow
                        # indicator so the user notices remaining failures.
                        is_partial = message.startswith("Partially applied")
                        if is_partial:
                            print(f"\033[33m⚠ {message}\033[0m")
                        else:
                            print(f"\033[32m✓ {message}\033[0m")
                        if is_partial:
                            self.partial_count += 1
                        else:
                            self.applied_count += 1
                        status_tag = "partial" if is_partial else "applied"
                        self.diff_results.append((diff.file_path, status_tag, message))
                    else:
                        print(f"\033[31m✗ {message}\033[0m")
                        self.failed_count += 1
                        self.diff_results.append((diff.file_path, "failed", message))
                    break
        
        # Print final summary
        self._print_summary()
        return True
    
    def _print_summary(self):
        """Print summary of diff processing."""
        total = self.applied_count + self.partial_count + self.skipped_count + self.failed_count
        if total == 0:
            return
        
        print(f"\n\033[1mDiff Summary:\033[0m")
        if self.applied_count > 0:
            print(f"  \033[32m✓ {self.applied_count} applied\033[0m")
        if self.partial_count > 0:
            print(f"  \033[33m⚠ {self.partial_count} partial\033[0m")
        if self.skipped_count > 0:
            print(f"  \033[33m⊘ {self.skipped_count} skipped\033[0m")
        if self.failed_count > 0:
            print(f"  \033[31m✗ {self.failed_count} failed\033[0m")
        print()
