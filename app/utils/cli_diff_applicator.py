"""
CLI diff applicator - Interactive diff application for terminal.
"""
import logging
import os
import re
from typing import List, Tuple, Optional, Set
from app.utils.logging_utils import logger


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
        self.failed_count = 0
        self.diff_results = []  # List of (file_path, status, message) tuples
    
    def extract_diffs(self, markdown: str) -> List[DiffBlock]:
        """
        Extract all diff blocks from markdown response.
        
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
            # Match opening fence: 3+ backticks followed by 'diff'
            if re.match(r'^`{3,4}diff\s*$', stripped):
                fence_len = len(stripped.split('diff')[0])  # number of backticks
                start_pos = sum(len(l) + 1 for l in lines[:i])
                i += 1
                content_lines = []
                # Collect until matching closing fence (same or more backticks, nothing else)
                while i < len(lines):
                    close_stripped = lines[i].strip()
                    if re.match(r'^`{' + str(fence_len) + r',}\s*$', close_stripped):
                        break
                    content_lines.append(lines[i])
                    i += 1
                end_pos = sum(len(l) + 1 for l in lines[:i + 1])
                diff_content = '\n'.join(content_lines).strip()
                if diff_content:
                    diffs.append(DiffBlock(content=diff_content, start_pos=start_pos, end_pos=end_pos))
            i += 1
        
        logger.debug(f"Extracted {len(diffs)} diff blocks from response")
        return diffs
    
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
                    if self._is_sequential_pair(diffs[i].content, diffs[j].content):
                        continue  # complementary, not superseding
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
    
    def _prompt_user_action(self, show_view: bool = True) -> str:
        """
        Prompt user for action on current diff.
        
        Returns:
            User's choice: 'a' (apply), 's' (skip), 'v' (view), 'q' (quit)
        """
        while True:
            try:
                response = input(
                    "\n\033[1mAction:\033[0m "
                    "\033[32m[a]\033[0mpply / "
                    "\033[33m[s]\033[0mkip / "
                    + ("\033[36m[v]\033[0miew full / " if show_view else "")
                    +
                    "\033[31m[q]\033[0muit? "
                ).strip().lower()
                
                valid = ['a', 's', 'q'] + (['v'] if show_view else [])
                if response in valid:
                    return response
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
                # Partial success - some hunks applied
                failures = result.get("failures", [])
                applied_count = result.get("applied_hunks", 0)
                failed_count = len(failures)
                
                # Build a helpful message
                msg_parts = [f"Partially applied to {diff.file_path}"]
                msg_parts.append(f"({applied_count} hunks succeeded, {failed_count} failed)")
                
                # Show first failure reason
                if failures:
                    first_failure = failures[0].get("message", "Unknown error")
                    msg_parts.append(f"First failure: {first_failure}")
                
                return True, " - ".join(msg_parts)
            else:
                # Clean, user-friendly error message
                error_details = result.get("details", {})
                failures = result.get("failures", [])
                
                # Check for common failure patterns
                if failures:
                    has_fuzzy_fail = any(f.get("details", {}).get("type") == "fuzzy_verification_failed" for f in failures)
                    has_low_confidence = any(f.get("details", {}).get("type") == "low_confidence" for f in failures)
                    
                    if has_fuzzy_fail or has_low_confidence:
                        return False, "Content doesn't match current file (file may have been modified)"
                
                # Generic error message
                error = error_details.get("message") or error_details.get("error") or "Unknown error"
                if "hunks failed" in str(error).lower():
                    return False, "Some changes couldn't be applied (file content mismatch)"
                
                return False, f"Failed: {error}"
                
        except Exception as e:
            return False, f"Error: {str(e).split(':')[0]}"
        finally:
            # Restore original log level
            diff_logger.setLevel(original_level)
    
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
        self.failed_count = 0
        self.diff_results = []

        # Extract all diffs
        diffs = self.extract_diffs(response)
        
        if not diffs:
            # No diffs to process
            return True
        
        # Drop earlier diffs whose hunks were superseded by later revisions
        diffs = self._deduplicate_diffs(diffs)
        
        print(f"\n\033[1mFound {len(diffs)} diff(s) in response\033[0m")
        
        # Process each diff one at a time
        for i, diff in enumerate(diffs, 1):
            is_truncated = self._print_diff_preview(diff, i, len(diffs))
            
            # Prompt for action
            while True:
                action = self._prompt_user_action(show_view=is_truncated)
                
                if action == 'q':
                    print(f"\n\033[90mStopping. Processed {i-1}/{len(diffs)} diffs.\033[0m")
                    self._print_summary()
                    return False
                
                elif action == 's':
                    print(f"\033[33m⊘ Skipped\033[0m")
                    self.skipped_count += 1
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
                        print(f"\033[32m✓ {message}\033[0m")
                        self.applied_count += 1
                        self.diff_results.append((diff.file_path, "applied", message))
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
        total = self.applied_count + self.skipped_count + self.failed_count
        if total == 0:
            return
        
        print(f"\n\033[1mDiff Summary:\033[0m")
        if self.applied_count > 0:
            print(f"  \033[32m✓ {self.applied_count} applied\033[0m")
        if self.skipped_count > 0:
            print(f"  \033[33m⊘ {self.skipped_count} skipped\033[0m")
        if self.failed_count > 0:
            print(f"  \033[31m✗ {self.failed_count} failed\033[0m")
        print()
