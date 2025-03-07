import os
import subprocess
import json
import tempfile
import glob
from itertools import zip_longest
from io import StringIO
import time
from typing import Dict, Optional, Union, List, Tuple, Any
import whatthepatch
import re
from app.utils.logging_utils import logger
import difflib

MIN_CONFIDENCE = 0.72 # what confidence level we cut off forced diff apply after fuzzy match
MAX_OFFSET = 5        # max allowed line offset before considering a hunk apply failed

class PatchApplicationError(Exception):
    """Custom exception for patch application failures"""
    def __init__(self, message: str, details: Dict):
        super().__init__(message)
        self.details = details

def clean_input_diff(diff_content: str) -> str:
    """
    Initial cleanup of diff content before parsing, with strict hunk enforcement:
      - Once we've read old_count '-' lines and new_count '+' lines, we end the hunk
        and ignore extra '-'/'+' lines until the next hunk or file header.
      - Preserves original logic for skipping content after triple backticks, decoding '\\n', etc.

    This typically resolves leftover lines in:
      - 'function_collision' (extra blank line at end)
      - 'single_line_replace' (keeping the old line)

    Because once we’ve consumed the declared minus/plus lines, further minus/plus lines
    can't sneak into the final patch.
    """

    logger.debug(diff_content)

    result_lines = []

    # Remove any content after triple backticks
    if '```' in diff_content:
        diff_content = diff_content.split('```')[0]

    # Split into lines for processing
    lines = diff_content.splitlines()

    # Track the current file and hunk state
    current_file = None
    in_hunk = False
    skip_until_next_file = False

    # For the strict hunk approach
    old_count = 0
    new_count = 0
    minus_seen = 0
    plus_seen = 0

    def reset_hunk_state():
        nonlocal in_hunk, old_count, new_count, minus_seen, plus_seen
        in_hunk = False
        old_count = 0
        new_count = 0
        minus_seen = 0
        plus_seen = 0

    for line in lines:
        # Reset skip flag on new file header
        if line.startswith('diff --git'):
            skip_until_next_file = False
            current_file = None
            reset_hunk_state()
            result_lines.append(line)
            continue

        # Track file paths
        if line.startswith('--- '):
            parts = line.split(' ', 1)
            if len(parts) > 1:
                current_file = parts[1]
            else:
                current_file = None
            result_lines.append(line)
            continue

        if line.startswith('+++ '):
            result_lines.append(line)
            continue

        # Hunk header
        if line.startswith('@@'):
            # Close out any prior hunk
            reset_hunk_state()
            in_hunk = True
            skip_until_next_file = False

            result_lines.append(line)  # Keep the hunk header

            # Parse the line to find old_count/new_count
            match = re.match(r'^@@ -\d+(?:,(\d+))? \+\d+(?:,(\d+))? @@', line)
            if match:
                old_count = int(match.group(2)) if match.group(2) else 1
                new_count = int(match.group(2)) if match.group(2) else 1
            continue

        if skip_until_next_file:
            # We skip lines until next file/hunk
            continue

        # If we're inside a hunk, apply the strict approach
        if in_hunk:
            # Check if we've already read all minus and plus lines
            done_minus = (minus_seen >= old_count)
            done_plus = (plus_seen >= new_count)

            if line.startswith('-'):
                if not done_minus:
                    minus_seen += 1
                    result_lines.append(line)
                else:
                    # We have enough minus lines => ignore it
                    logger.debug(f"[clean_input_diff] ignoring extra '-' line: {line.rstrip()}")
                continue

            if line.startswith('+'):
                if not done_plus:
                    plus_seen += 1
                    result_lines.append(line)
                else:
                    # We have enough plus lines => ignore it
                    logger.debug(f"[clean_input_diff] ignoring extra '+' line: {line.rstrip()}")
                continue

            if line.startswith(' '):
                # context lines are always okay
                result_lines.append(line)
                continue

            # If we get here and it's not -, +, or space => presumably hunk is done
            reset_hunk_state()
            result_lines.append(line)
            continue

        # If not in a hunk, just pass the line along
        result_lines.append(line)

    # End of loop
    return '\n'.join(result_lines)


def normalize_diff(diff_content: str) -> str:
    """
    Normalize a diff using whatthepatch for proper parsing and reconstruction.
    Handles incomplete hunks, context issues, and line count mismatches.
    """
    logger.debug("Normalizing diff with whatthepatch")
    try:
        # Extract headers and hunk headers from original diff
        diff_lines = diff_content.splitlines()
        result = []
        i = 0
        while i < len(diff_lines):
            line = diff_lines[i]
            if line.startswith(('diff --git', 'index', '--- ', '+++ ')):
                result.append(line)
            elif line.startswith('@@'):
                result.append(line)
                i += 1
                lines_seen = 0
                while i < len(diff_lines) and diff_lines[i].startswith((' ', '+', '-')):
                    result.append(diff_lines[i])
                    i += 1
                continue
            i += 1

        return '\n'.join(result) + '\n'
    except Exception as e:
        logger.error(f"Error normalizing diff: {str(e)}")
        return diff_content

def is_new_file_creation(diff_lines: List[str]) -> bool:
    """Determine if a diff represents a new file creation."""
    if not diff_lines:
        return False

    logger.debug(f"Analyzing diff lines for new file creation ({len(diff_lines)} lines):")
    for i, line in enumerate(diff_lines[:10]):
        logger.debug(f"Line {i}: {line[:100]}")  # Log first 100 chars of each line

    # Look for any indication this is a new file
    for i, line in enumerate(diff_lines[:10]):
        # Case 1: Standard git diff new file
        if line.startswith('@@ -0,0'):
            logger.debug("Detected new file from zero hunk marker")
            return True

        # Case 2: Empty source file indicator
        if line == '--- /dev/null':
            logger.debug("Detected new file from /dev/null source")
            return True

        # Case 3: New file mode
        if 'new file mode' in line:
            logger.debug("Detected new file from mode marker")
            return True

    logger.debug("No new file indicators found")
    return False
def create_new_file(git_diff: str, base_dir: str) -> None:
    """Create a new file from a git diff."""
    logger.info(f"Processing new file diff with length: {len(git_diff)} bytes")

    try:
        # Parse the diff content
        diff_lines = git_diff.splitlines()

        # Find the file path line
        file_path = None
        for line in diff_lines:
            if line.startswith('diff --git'):
                file_path = line.split(' b/')[-1]
                break
        # Extract the file path from the diff --git line
        full_path = os.path.join(base_dir, file_path)

        # Create directory if it doesn't exist
        os.makedirs(os.path.dirname(full_path), exist_ok=True)

        # Extract the content (everything after the @@ line)
        content_lines = []

        in_content = False
        for line in diff_lines:
            if in_content and line.startswith('+'):
                content_lines.append(line[1:])
            elif line.startswith('@@'):
                in_content = True
        # Write the content
        content = '\n'.join(content_lines)
        with open(full_path, 'w', encoding='utf-8') as f:
            f.write(content)
            if not content.endswith('\n'):
                f.write('\n')
        logger.info(f"Successfully created new file: {file_path}")
    except Exception as e:
        logger.error(f"Error creating new file: {str(e)}, diff content: {git_diff[:200]}")
        raise

def inspect_line_content(file_path: str, line_number: int, context: int = 5) -> Dict[str, Any]:
    """Inspect the content around a specific line number."""
    try:
        with open(file_path, 'r') as f:
            lines = f.readlines()

        start = max(0, line_number - context - 1)
        end = min(len(lines), line_number + context)

        return {
            'lines': {
                i+1: {
                    'content': lines[i],
                    'hex': ' '.join(f'{ord(c):02x}' for c in lines[i])
                }
                for i in range(start, end)
            },
            'line_endings': {
                'total_lines': len(lines),
                'endings': {
                    'CRLF': sum(1 for line in lines if line.endswith('\r\n')),
                    'LF': sum(1 for line in lines if line.endswith('\n') and not line.endswith('\r\n')),
                    'CR': sum(1 for line in lines if line.endswith('\r') and not line.endswith('\r\n')),
                    'none': sum(1 for line in lines if not line.endswith('\n') and not line.endswith('\r'))
                }
            }
        }
    except Exception as e:
        logger.error(f"Error inspecting line content: {e}")
        return {'error': str(e)}

def analyze_diff_failure(diff: str, file_path: str, error_output: str) -> Dict[str, Any]:
    """Analyze why a diff failed to apply and provide diagnostic information."""
    try:
        # Remove line number if present in file_path
        clean_path = file_path.split(':')[0] if file_path else None
        file_content = open(clean_path, 'r').read() if clean_path else ""

        # Parse with unidiff for better analysis
        try:
            patch_analysis = {
                'files': 0,
                'hunks': 0,
                'additions': 0,
                'deletions': 0
            }
        except Exception as e:
            patch_analysis = {'parse_error': str(e)}

        # Extract context from error
        context_lines = []
        if 'while searching for:' in error_output:
            context_section = error_output.split('while searching for:')[1]
            context_section = context_section.split('error:')[0] if 'error:' in context_section else context_section
            context_lines = [line.strip() for line in context_section.splitlines() if line.strip()]

        analysis = {
            'patch_analysis': patch_analysis,
            'context_lines': context_lines,
            'file_state': {
                'exists': os.path.exists(clean_path),
                'size': os.path.getsize(clean_path) if os.path.exists(clean_path) else None,
                'line_count': len(file_content.splitlines()) if file_content else 0
            },
            'error_details': error_output
        }

        if context_lines:
            # Try to locate context in file
            file_lines = file_content.splitlines()
            for i in range(len(file_lines)):
                if i + len(context_lines) <= len(file_lines):
                    if all(file_lines[i+j].strip() == context_lines[j].strip()
                          for j in range(len(context_lines))):
                        analysis['context_found'] = {
                            'line_number': i + 1,
                            'surrounding_lines': file_lines[max(0, i-2):i+len(context_lines)+2]
                        }
                        break

        return analysis

    except Exception as e:
        logger.error(f"Error analyzing diff: {str(e)}")
        return {'error': str(e)}

def fix_hunk_context(lines: List[str]) -> List[str]:
    """
    Fix hunk headers to match actual content.
    Returns corrected lines.
    """
    result = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if not line.startswith('@@'):
            result.append(line)
            i += 1
            continue
        # Found a hunk header
        match = re.match(r'^@@ -(\d+),(\d+) \+(\d+),(\d+) @@', line)
        if not match:
            result.append(line)
            i += 1
            continue
        # Count actual lines in the hunk
        old_count = 0
        new_count = 0
        hunk_lines = []
        i += 1
        while i < len(lines) and not lines[i].startswith('@@'):
            if lines[i].startswith('-'):
                old_count += 1
            elif lines[i].startswith('+'):
                new_count += 1
            elif lines[i].startswith(' '):
                old_count += 1
                new_count += 1
            hunk_lines.append(lines[i])
            i += 1
        # Add corrected hunk header and lines
        result.append(f'@@ -{match.group(1)},{old_count} +{match.group(3)},{new_count} @@')
        result.extend(hunk_lines)
    return result

def normalize_whitespace_in_diff(diff_lines: List[str]) -> List[str]:
    """
    Normalize both leading and trailing whitespace in diff content while preserving
    essential indentation. Returns cleaned lines.
    """
    result = []
    i = 0
    while i < len(diff_lines):
        line = diff_lines[i]
        # Keep all header lines
        if line.startswith(('diff --git', 'index', '---', '+++', '@@')):
            result.append(line)
            i += 1
            continue
        # For content lines, normalize whitespace while preserving indentation
        if line.startswith(('+', '-', ' ')):
            prefix = line[0]  # Save the diff marker (+, -, or space)
            content = line[1:]  # Get the actual content

            # Normalize the content while preserving essential indentation
            normalized = content.rstrip()  # Remove trailing whitespace
            if normalized:
                # Count leading spaces for indentation
                indent = len(content) - len(content.lstrip())
                # Reconstruct the line with normalized whitespace
                result.append(f"{prefix}{' ' * indent}{normalized.lstrip()}")
        i += 1
    return result

def correct_git_diff(git_diff: str, original_file_path: str) -> str:
    """
    Correct a git diff using unidiff for parsing and validation.
    Maintains compatibility with existing function signature.
    """
    logger.info(f"Processing diff for {original_file_path}")

    try:

        # Clean up the diff content first
        cleaned_diff = clean_input_diff(git_diff)

        # Extract headers from original diff
        diff_lines = git_diff.splitlines()
        headers = []
        for line in diff_lines:
            if line.startswith(('diff --git', 'index', '--- ', '+++ ')):
                headers.append(line)
            elif line.startswith('@@'):
                break

        # Check for new file creation
        if is_new_file_creation(diff_lines):
            logger.info(f"Detected new file creation for {original_file_path}")
            return cleaned_diff

        # Modify hunk headers to be more lenient about line counts
        lines = cleaned_diff.splitlines()
        modified_lines = fix_hunk_context(lines)

        logger.info(f"Normalizing diff with whatthepatch")
        try:
            # Parse and normalize with whatthepatch
            try:
                parsed_patches = list(whatthepatch.parse_patch(cleaned_diff))
            except ValueError as e:
                logger.warning(f"whatthepatch parsing error: {str(e)}")
                return cleaned_diff

            if not parsed_patches:
                logger.warning("No valid patches found in diff")
                return cleaned_diff

            # Reconstruct normalized diff
            result = headers # start with original headers

            # Extract original hunks
            original_hunks = []
            current_hunk = []
            for line in cleaned_diff.splitlines():
                if line.startswith('@@'):
                    if current_hunk:
                        original_hunks.append(current_hunk)
                    current_hunk = [line]
                elif current_hunk and line.startswith(('+', '-', ' ')):
                    current_hunk.append(line)
            if current_hunk:
                original_hunks.append(current_hunk)
            # Process each hunk while preserving structure
            for hunk in original_hunks:
                hunk_header = hunk[0]
                match = re.match(r'^@@ -(\d+),\d+ \+(\d+),\d+ @@', hunk_header)
                if not match:
                    continue
                old_start = int(match.group(1))
                new_start = int(match.group(2))
                # Count actual changes in this hunk
                old_count = sum(1 for line in hunk[1:] if line.startswith(' ') or line.startswith('-'))
                new_count = sum(1 for line in hunk[1:] if line.startswith(' ') or line.startswith('+'))
                # Output corrected hunk
                result.append(f"@@ -{old_start},{old_count} +{new_start},{new_count} @@")
                result.extend(hunk[1:])
            normalized_diff = '\n'.join(result) + '\n'
            logger.info(f"Successfully normalized diff")
            return normalized_diff

        except Exception as e:
            logger.error(f"Error normalizing diff: {str(e)}")
            raise

    except Exception as e:
        logger.error(f"Error correcting diff: {str(e)}")
        raise

def validate_and_fix_diff(diff_content: str) -> str:
    """
    Validate diff format and ensure it has all required components.
    Fixes common issues with LLM-generated diffs.
    """
    logger.info("Validating and fixing diff format")

    # Split into lines while preserving empty lines
    lines = diff_content.splitlines(True)
    result = []
    in_hunk = False

    for i, line in enumerate(lines):
        # Preserve all header lines exactly
        if line.startswith(('diff --git', '--- ', '+++ ')):
            result.append(line)
            continue

        # Handle hunk headers
        if line.startswith('@@'):
            in_hunk = True
            result.append(line)
            continue

        # Handle hunk content
        if in_hunk:
            if not line.startswith((' ', '+', '-', '\n')):
                # End of hunk reached
                in_hunk = False
                if not line.endswith('\n'):
                    result.append('\n')  # Ensure proper line ending
            else:
                result.append(line)
                continue

        # Add any non-hunk lines
        if not in_hunk:
            result.append(line)

    # Ensure the diff ends with a newline
    if result and not result[-1].endswith('\n'):
        result.append('\n')

    return ''.join(result)

def prepare_unified_diff(diff_content: str) -> str:
    """
    Convert a git diff to a simple unified diff format that the patch command expects.
    """
    logger.info("Preparing unified diff")
    result = []
    lines = diff_content.splitlines()

    # Find the actual file paths
    i = 0
    in_hunk = False
    while i < len(lines):
        line = lines[i]

        # Keep header lines exactly as they are
        if line.startswith(('diff --git', 'index')):
            result.append(line)
            i += 1
            continue

        # File paths
        if line.startswith('--- '):
            result.append(line)
            i += 1
            continue
        if line.startswith('+++ '):
            result.append(line)
            i += 1
            continue

        # Hunk header
        if line.startswith('@@ '):
            in_hunk = True
            result.append(line)
            i += 1
            continue

        # Hunk content
        if in_hunk:
            if line.startswith((' ', '+', '-')):
                result.append(line)
            elif not line.strip():  # Empty line within hunk
                result.append(' ' + line)  # Add context marker for empty lines
            else:
                in_hunk = False  # End of hunk reached
            i += 1
            continue

        i += 1

    # Ensure exactly one newline at the end
    while result and not result[-1].strip():
        result.pop()
    result.append('')  # Add single newline at end

    return '\n'.join(result)


def remove_reject_file_if_exists(file_path: str):
    """
    Remove .rej file if it exists, to clean up after partial patch attempts.
    """
    rej_file = file_path + '.rej'
    if os.path.exists(rej_file):
        try:
            os.remove(rej_file)
            logger.info(f"Removed reject file: {rej_file}")
        except OSError as e:
            logger.warning(f"Could not remove reject file {rej_file}: {e}")

class PatchApplicationError(Exception):
    pass

def apply_diff_with_difflib(file_path: str, diff_content: str) -> None:
    """
    Forced-hybrid approach that also throws PatchApplicationError if we cannot
    match a chunk with at least minimal confidence or if the minus lines do not match
    in strict mode. No silent fails.

    1) Read 'file_path' lines.
    2) parse hunks with parse_unified_diff_exact_plus(diff_content, file_path)
    3) For each hunk, do Phase A or Phase B, forcibly remove old_count lines.
    4) If we can't match, raise PatchApplicationError.
    5) Write result back to 'file_path'.
    """

    # 1) read the original lines
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            original_lines = f.readlines()
    except FileNotFoundError:
        original_lines = []

    # 2) apply forced-hybrid logic with error throwing
    final_lines = apply_diff_with_difflib_hybrid_forced(file_path, diff_content, original_lines)

    # 3) write result back to file
    with open(file_path, 'w', encoding='utf-8') as f:
        f.writelines(final_lines)
        logger.info(
            f"Successfully applied forced-hybrid diff (with exceptions on mismatch) to {file_path}. "
            f"Wrote {len(final_lines)} lines."
        )

def is_hunk_already_applied(file_lines: List[str], hunk: Dict[str, Any], pos: int) -> bool:
    """
    Check if a hunk has already been applied at the given position.
    Returns True only if ALL changes in the hunk are already present.
    Checks if the target state matches exactly.
    """
    if pos >= len(file_lines):
        logger.debug(f"Position {pos} beyond file length {len(file_lines)}")
        return False

    # Get the lines we're working with
    window_size = max(len(hunk['old_block']), len(hunk['new_lines']))
    available_lines = file_lines[pos:pos + window_size]

    # Count actual changes needed (excluding context lines)
    changes_needed = 0
    changes_found = 0

    # Map of line positions to their expected states
    expected_states = {}
    
    for old_line, new_line in zip_longest(hunk['old_block'], hunk['new_lines'], fillvalue=None):
        if old_line != new_line:
            changes_needed += 1
            
    # Check each line in the window
    for i, actual_line in enumerate(available_lines):
        if i < len(hunk['new_lines']):
            new_line = hunk['new_lines'][i]
            old_line = hunk['old_block'][i] if i < len(hunk['old_block']) else None
            
            # Line matches target state
            if actual_line.rstrip() == new_line.rstrip():
                changes_found += 1
                continue
                
            # Line matches original state and needs change
            if old_line and actual_line.rstrip() == old_line.rstrip():
                # This is a line that still needs changing
                continue
                
            # Line doesn't match either state
            return False

    # Calculate what percentage of changes are already applied
    if changes_needed > 0 and changes_found > 0:
        applied_ratio = changes_found / changes_needed
        logger.debug(f"Hunk changes: needed={changes_needed}, found={changes_found}, ratio={applied_ratio:.2f}")

        # Consider it applied if we found all changes
        if applied_ratio >= 1.0:  # Must match exactly, or have all needed changes+
            logger.debug(f"All changes already present at pos {pos}")
            return True
        elif applied_ratio > 0:
            logger.debug(f"Partial changes found ({applied_ratio:.2f}) - will apply remaining changes")
            return False

    # If we get here, no changes were found
    if changes_needed > 0:
        return False

    # Default case - nothing to apply
    logger.debug("No changes needed")
    return True

def apply_diff_with_difflib_hybrid_forced(file_path: str, diff_content: str, original_lines: list[str]) -> list[str]:
    # parse hunks
    hunks = list(parse_unified_diff_exact_plus(diff_content, file_path))
    logger.debug(f"Parsed hunks for difflib: {json.dumps([{'old_start': h['old_start'], 'old_count': len(h['old_block']), 'new_start': h['new_start'], 'new_count': len(h['new_lines'])} for h in hunks], indent=2)}")
    already_applied_hunks = set()
    stripped_original = [ln.rstrip('\n') for ln in original_lines]

    final_lines = stripped_original.copy()
    offset = 0
    applied_content = set()
    for hunk_idx, h in enumerate(hunks, start=1):
        def calculate_initial_positions():
            """Calculate initial positions and counts for the hunk."""
            old_start = h['old_start'] - 1
            old_count = h['old_count']
            initial_remove_pos = clamp(old_start + offset, 0, len(final_lines))

            # Adjust counts based on available lines
            available_lines = len(final_lines) - initial_remove_pos
            actual_old_count = min(old_count, available_lines)
            end_remove = initial_remove_pos + actual_old_count

            # Final position adjustment
            remove_pos = clamp(initial_remove_pos, 0, len(stripped_original) - 1)

            return {
                'remove_pos': remove_pos,
                'old_count': old_count,
                'actual_old_count': actual_old_count,
                'end_remove': end_remove
            }

        def try_strict_match(positions):
            """Attempt a strict match of the hunk content."""
            remove_pos = positions['remove_pos']

            if remove_pos + len(h['old_block']) <= len(final_lines):
                file_slice = final_lines[remove_pos : remove_pos + positions['old_count']]
                if h['old_block'] and len(h['old_block']) >= positions['actual_old_count']:
                    old_block_minus = h['old_block'][:positions['old_count']]
                    if file_slice == old_block_minus:
                        logger.debug(f"Hunk #{hunk_idx}: strict match at pos={remove_pos}")
                        return True, remove_pos
                    logger.debug(f"Hunk #{hunk_idx}: strict match failed at pos={remove_pos}")
                else:
                    logger.debug(f"Hunk #{hunk_idx}: old_block is smaller than old_count => strict match not possible")
            return False, remove_pos

        def try_fuzzy_match(positions):
            """Attempt a fuzzy match if strict match fails."""
            remove_pos = positions['remove_pos']
            logger.debug(f"Hunk #{hunk_idx}: Attempting fuzzy near line {remove_pos}")

            best_pos, best_ratio = find_best_chunk_position(stripped_original, h['old_block'], remove_pos)

            # First check if changes are already applied (with high confidence threshold)
            if any(new_line in stripped_original for new_line in h['new_lines']):
                already_applied = sum(1 for line in h['new_lines'] if line in stripped_original)
                if already_applied / len(h['new_lines']) >= 0.98:  # Require near-exact match
                    logger.info(f"Hunk #{hunk_idx} appears to be already applied")
                    return None, remove_pos  # Signal skip to next hunk

            # Then check if we have enough confidence in our match position
            if best_ratio <= MIN_CONFIDENCE:
                msg = (f"Hunk #{hunk_idx} => low confidence match (ratio={best_ratio:.2f}) near {remove_pos}, "
                       f"can't safely apply chunk. Failing.")
                logger.error(msg)
                raise PatchApplicationError(msg, {
                    "status": "error",
                    "type": "low_confidence",
                    "hunk": hunk_idx,
                    "confidence": best_ratio
                })

            logger.debug(f"Hunk #{hunk_idx}: fuzzy best pos={best_pos}, ratio={best_ratio:.2f}")
            return (best_pos + offset if best_pos is not None else None), remove_pos

        logger.debug(f"Processing hunk #{hunk_idx} with offset {offset}")

        # Create a unique key for this hunk based on its content
        already_found = False
        hunk_key = (
            tuple(h['old_block']),
            tuple(h['new_lines'])
        )
        if hunk_key in already_applied_hunks:
            continue

        # First check if this hunk is already applied anywhere in the file
        for pos in range(len(stripped_original)):
            if is_hunk_already_applied(stripped_original, h, pos):
                # Verify we have the exact new content, not just similar content
                window = stripped_original[pos:pos+len(h['new_lines'])]
                if all(line.rstrip() == new_line.rstrip() for line, new_line in zip(window, h['new_lines'])):
                    logger.info(f"Hunk #{hunk_idx} already present at position {pos}")
                    already_applied_hunks.add(hunk_key)
                    logger.debug(f"Verified hunk #{hunk_idx} is already applied")
                    already_found = True
                    break
                # Content doesn't match exactly, continue looking
                continue

        if already_found:
            continue

        # Calculate initial positions
        positions = calculate_initial_positions()
        
        # Try strict match first
        strict_ok, remove_pos = try_strict_match(positions)
        
        # If strict match fails, try fuzzy match
        if not strict_ok:
            result = try_fuzzy_match(positions)
            if result is None:
                # Skip this hunk as it's already applied
                continue  # Skip this hunk (already applied)
            new_pos, old_pos = result
            if new_pos is not None:  # Only update position if we got a valid match
                remove_pos = new_pos

        # Use actual line counts from the blocks
        old_count = len(h['old_block'])
        logger.debug(f"Replacing {old_count} lines with {len(h['new_lines'])} lines at pos={remove_pos}")
        
        # Replace exactly the number of lines we counted
        final_lines[remove_pos:remove_pos + old_count] = h['new_lines']
        logger.debug(f"  final_lines after insertion: {final_lines}")

        # Calculate net change based on actual lines removed and added
        actual_removed = min(positions['old_count'], len(h['old_block']))
        logger.debug(f"Removal calculation: min({len(h['old_block'])}, {len(final_lines)} - {remove_pos})")
        logger.debug(f"Old block lines: {h['old_block']}")
        logger.debug(f"New lines: {h['new_lines']}")
        logger.debug(f"Remove position: {remove_pos}")
        logger.debug(f"Final lines length: {len(final_lines)}")
        net_change = len(h['new_lines']) - positions['actual_old_count']
        offset += net_change

    # Remove trailing empty line if present
    while final_lines and final_lines[-1] == '':
        final_lines.pop()
    
    # Add newlines to all lines
    result_lines = [
        ln + '\n' if not ln.endswith('\n') else ln
        for ln in final_lines
    ]
    logger.debug(f"Final result lines: {result_lines}")
    
    return result_lines

def strip_leading_dotslash(rel_path: str) -> str:
    """
    Remove leading '../' or './' segments from the relative path
    so it matches patch lines that are always 'frontend/...', not '../frontend/...'.
    """

    # Repeatedly strip leading '../' or './'
    pattern = re.compile(r'^\.\.?/')
    while pattern.match(rel_path):
        rel_path = rel_path[rel_path.index('/')+1:]
    return rel_path

def parse_unified_diff_exact_plus(diff_content: str, target_file: str) -> list[dict]:
    """
    Same logic: we gather old_block and new_lines. If we can't parse anything, we return an empty list.
    The calling code might handle that or raise an error if no hunks are found.
    """

    lines = diff_content.splitlines()
    logger.debug(f"Parsing diff with {len(lines)} lines:\n{diff_content}")
    hunks = []
    current_hunk = None
    in_hunk = False
    skip_file = True
    seen_hunks = set()

    # fixme: import ziya project directory if specified on invocation cli
    rel_path = os.path.relpath(target_file, os.getcwd())
    rel_path = strip_leading_dotslash(rel_path)

    i = 0
    while i < len(lines):
        line = lines[i]
        logger.debug(f"parse_unified_diff_exact_plus => line[{i}]: {line!r}")

        if line.startswith('diff --git'):
            i += 1
            continue

        if line.startswith(('--- ', '+++ ')):
            i += 1
            continue

        # Handle index lines and other git metadata
        if line.startswith('index ') or line.startswith('new file mode ') or line.startswith('deleted file mode '):
            i += 1
            continue

        if line.startswith('@@ '):
            match = re.match(r'^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@(?:\s+Hunk #(\d+))?', line)
            hunk_num = int(match.group(5)) if match and match.group(5) else len(hunks) + 1
            if match:
                old_start = int(match.group(1))
                # Validate line numbers
                if old_start < 1:
                    logger.warning(f"Invalid hunk header - old_start ({old_start}) < 1")
                    old_start = 1

                # Use default of 1 for count if not specified
                old_count = int(match.group(2)) if match.group(2) else 1

                new_start = int(match.group(3))
                new_count = int(match.group(4)) if match.group(4) else 1

                # Use original hunk number if present in header
                if match.group(5):
                    hunk_num = int(match.group(5))

                hunk = {
                    'old_start': old_start,
                    'old_count': old_count,
                    'new_start': new_start,
                    'new_count': new_count,
                    'number': hunk_num,
                    'old_block': [],
                    'original_hunk': hunk_num,  # Store original hunk number
                    'new_lines': []
                }

                # Start collecting content for this hunk
                current_lines = []
                in_hunk = True
                hunks.append(hunk)
                current_hunk = hunk

            i += 1
            continue

        seen_hunks = set()
        if in_hunk:
            # End of hunk reached if we see a line that doesn't start with ' ', '+', '-', or '\'
            if not line.startswith((' ', '+', '-', '\\')):
                in_hunk = False
                if current_hunk:
                    # Check if this hunk is complete and unique
                    if len(current_hunk['old_block']) == current_hunk['old_count'] and \
                       len(current_hunk['new_lines']) == current_hunk['new_count']:
                        hunk_key = (tuple(current_hunk['old_block']), tuple(current_hunk['new_lines']))
                        if hunk_key not in seen_hunks:
                            seen_hunks.add(hunk_key)
                            hunks.append(current_hunk)
                    current_hunk = None
                i += 1
                continue
            if current_hunk:
                if line.startswith('-'):
                    text = line[1:]
                    current_hunk['old_block'].append(text)
                    current_hunk['old_count'] = len(current_hunk['old_block'])
                elif line.startswith('+'):
                    text = line[1:]
                    current_hunk['new_lines'].append(text)
                    current_hunk['new_count'] = len(current_hunk['new_lines'])
                elif line.startswith(' '):
                    text = line[1:]
                    if (not current_hunk['old_block'] or
                        current_hunk['old_block'][-1] != text):
                        current_hunk['old_block'].append(text)
                    if (not current_hunk['new_lines'] or
                        current_hunk['new_lines'][-1] != text):
                        current_hunk['new_lines'].append(text)

        i += 1
    return hunks

def find_best_chunk_position(file_lines: list[str], old_block: list[str], approximate_line: int) -> tuple[int, float]:
    # Adjust approximate_line if it's outside file bounds
    if approximate_line >= len(file_lines):
        approximate_line = len(file_lines) - 1
    elif approximate_line < 0:
        approximate_line = 0
        
    # Look for exact context matches first
    context_lines = [line for line in old_block if line.startswith(' ')]
    
    """
    Return (best_pos, best_ratio). If best_ratio < MIN_CONFIDENCE, we raise or handle outside.
    """
    block_str = '\n'.join(old_block)
    file_len = len(file_lines)
    block_len = len(old_block)
    
    search_start = 0
    search_end = file_len - block_len + 1
    if search_end < search_start:
        search_start = 0
        search_end = max(0, file_len - block_len + 1)

    best_pos = approximate_line
    best_ratio = 0.0
    import difflib
    matcher = difflib.SequenceMatcher(None)

    # First try exact matches with context
    for pos in range(search_start, search_end + 1):
        if pos + block_len > file_len:
            continue

        # Check if we have an exact match of the first and last lines
        if (old_block[0] == file_lines[pos] and
            old_block[-1] == file_lines[pos + len(old_block) - 1]):
            window = file_lines[pos:pos+block_len]
            window_str = '\n'.join(window)
            matcher.set_seqs(block_str, window_str)
            ratio = matcher.ratio()
            if ratio > 0.9:  # High confidence exact match
                return pos, ratio

    # If no high-confidence exact match, try fuzzy matching
    for pos in range(search_start, search_end + 1):
        window = file_lines[pos:pos+block_len]
        window_str = '\n'.join(window)
        matcher.set_seqs(block_str, window_str)
        ratio = matcher.ratio()
        if ratio > best_ratio:
            best_ratio = ratio
            best_pos = pos
        if best_ratio >= 0.98:
            break

    logger.debug(f"find_best_chunk_position => best ratio={best_ratio:.2f} at pos={best_pos}, approximate_line={approximate_line}")
    return best_pos, best_ratio


def clamp(value: int, low: int, high: int) -> int:
    """Simple clamp utility to ensure we stay in range."""
    return max(low, min(high, value))

class HunkData:
    """
    Stores data for a single hunk in the unified diff: header, start_line, old_lines, new_lines, etc.
    Also includes optional context fields if needed (context_before, context_after).
    """
    def __init__(self, header='', start_line=1, old_lines=None, new_lines=None,
                 context_before=None, context_after=None):
        self.header = header
        self.start_line = start_line
        self.old_lines = old_lines or []
        self.new_lines = new_lines or []
        self.context_before = context_before or []
        self.context_after = context_after or []

    def __repr__(self):
        return (f"<HunkData start_line={self.start_line} "
                f"old={len(self.old_lines)} new={len(self.new_lines)}>")


##########################################################
# 2) FIND SECTION BOUNDS
##########################################################

def parse_unified_diff(diff_content: str) -> List[HunkData]:
    # ...
    # Return a list of HunkData. Or use your existing parser logic:
    lines = diff_content.splitlines()
    hunks: List[HunkData] = []
    in_hunk = False
    current_hunk = None

    for line in lines:
        if line.startswith(('diff --git', 'index ', 'new file mode', '--- ', '+++ ')):
            continue
        if line.startswith('@@'):
            # close old hunk if any
            if in_hunk and current_hunk:
                hunks.append(current_hunk)
            current_hunk = HunkData(header=line, start_line=1)
            match = re.match(r'^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@', line)
            if match:
                current_hunk.start_line = int(match.group(1))
            else:
                logger.warning(f"Invalid hunk header: {line}")
            in_hunk = True
            continue

        if in_hunk and current_hunk:
            if line.startswith('+'):
                current_hunk.new_lines.append(line[1:])
            elif line.startswith('-'):
                current_hunk.old_lines.append(line[1:])
            else:
                # Context line
                content = line[1:] if line.startswith(' ') else line
                current_hunk.old_lines.append(content)
                current_hunk.new_lines.append(content)
        else:
            # Lines outside hunks
            pass

    if in_hunk and current_hunk:
        hunks.append(current_hunk)

    return hunks

class PatchApplicationError(Exception):
    def __init__(self, msg, details=None):
        super().__init__(msg)
        self.details = details or {}



def find_section_bounds(
    pos: int,
    lines: List[str],
    is_header: bool = False,
    hunk_header: str = ''
) -> Tuple[int, int, Optional[str]]:
    """
    This reproduces the logic in your snippet for scanning backward or forward
    to find function definitions, used to identify the nearest “section” or function.
    """
    logger.debug(f"[find_section_bounds] pos={pos}, total lines={len(lines)}")

    if not lines:
        logger.debug("Empty file, return trivial bounds.")
        return 0, 0, None

    # Basic fallback if we can’t find anything
    if pos >= len(lines):
        pos = len(lines) - 1

    # For demonstration, we search backward for “def ”
    start = pos
    while start >= 0 and not lines[start].lstrip().startswith('def '):
        start -= 1
    if start < 0:
        # No function found, fallback
        return 0, len(lines), None

    # That’s presumably the start of the function
    section_name = extract_function_name(lines[start])
    end = start + 1
    while end < len(lines) and not lines[end].lstrip().startswith('def '):
        end += 1

    logger.debug(f"[find_section_bounds] Found function {section_name}, range={start}-{end}")
    return start, end, section_name


def extract_function_name(line: str) -> str:
    """
    Helper to parse 'def something(...)' from a line and return 'something'.
    """
    line = line.strip()
    if not line.startswith('def '):
        return ''
    # e.g., def foo(bar):
    after_def = line[4:].split('(')[0]
    return after_def.strip()

def cleanup_patch_artifacts(base_dir: str, file_path: str) -> None:
    """
    Clean up .rej and .orig files that might be left behind by patch application.

    Args:
        base_dir: The base directory where the codebase is located
        file_path: The path to the file that was patched
    """
    try:
        # Get the directory containing the file
        file_dir = os.path.dirname(os.path.join(base_dir, file_path))

        # Find and remove .rej and .orig files
        for pattern in ['*.rej', '*.orig']:
            for artifact in glob.glob(os.path.join(file_dir, pattern)):
                logger.info(f"Removing patch artifact: {artifact}")
                os.remove(artifact)
    except Exception as e:
        logger.warning(f"Error cleaning up patch artifacts: {str(e)}")

def use_git_to_apply_code_diff(git_diff: str, file_path: str) -> None:
    """
    Apply a git diff to the user's codebase.
    Main entry point for patch application.

    If ZIYA_FORCE_DIFFLIB environment variable is set, bypasses system patch
    and uses difflib directly.

    Args:
        git_diff (str): The git diff to apply
        file_path (str): Path to the target file
    """
    logger.info("Starting diff application process...")
    logger.debug("Original diff content:")
    logger.debug(git_diff)
    changes_written = False
    results = {
        "succeeded": [],
        "failed": [],
        "already_applied": []
    }

    # Correct the diff using existing functionality
    if file_path:
        git_diff = correct_git_diff(git_diff, file_path)
    else:
        raise ValueError("Could not determine target file path")

    user_codebase_dir = os.environ.get("ZIYA_USER_CODEBASE_DIR")
    if not user_codebase_dir:
        raise ValueError("ZIYA_USER_CODEBASE_DIR environment variable is not set")

    # Split into lines for analysis
    diff_lines = git_diff.splitlines()

    # Extract target file path first
    file_path = None
    for line in diff_lines:
        if line.startswith('diff --git'):
            _, _, path = line.partition(' b/')
            file_path = os.path.join(user_codebase_dir, path)
            break

    # Handle new file creation
    if is_new_file_creation(diff_lines):
        create_new_file(git_diff, user_codebase_dir)
        cleanup_patch_artifacts(user_codebase_dir, file_path)
        return
        
    # If force difflib flag is set, skip system patch entirely
    if os.environ.get('ZIYA_FORCE_DIFFLIB'):
        logger.info("Force difflib mode enabled, bypassing system patch")
        try:
            apply_diff_with_difflib(file_path, git_diff)
            return
        except Exception as e:
            raise PatchApplicationError(str(e), {"status": "error", "type": "difflib_error"})

    results = {"succeeded": [], "already_applied": [], "failed": []}

    # Read original content before any modifications
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            original_content = f.read()
    except FileNotFoundError:
        original_content = ""

    try:
        # Check if file exists before attempting patch
        if not os.path.exists(file_path) and not is_new_file_creation(diff_lines):
            raise PatchApplicationError(f"Target file does not exist: {file_path}", {
                "status": "error",
                "type": "missing_file",
                "file": file_path
            })
        logger.info("Starting patch application pipeline...")
        logger.debug("About to run patch command with:")
        logger.debug(f"CWD: {user_codebase_dir}")
        logger.debug(f"Input length: {len(git_diff)} bytes")
        changes_written = False
        # Do a dry run to see what we're up against on first pass
        patch_result = subprocess.run(
            ['patch', '-p1', '--forward', '--no-backup-if-mismatch', '--reject-file=-', '--batch', '--ignore-whitespace', '--verbose', '--dry-run', '-i', '-'],
            input=git_diff,
            encoding='utf-8',
            cwd=user_codebase_dir,
            capture_output=True,
            text=True,
            timeout=10
        )
        logger.debug(f"stdout: {patch_result.stdout}")
        logger.debug(f"stderr: {patch_result.stderr}")
        logger.debug(f"Return code: {patch_result.returncode}")

        hunk_status = {}
        patch_output = ""
        file_was_modified = False
        has_line_mismatch = False
        has_large_offset = False
        has_fuzz = False
        patch_reports_success = False

        # Parse the dry run output
        dry_run_status = parse_patch_output(patch_result.stdout)
        hunk_status = dry_run_status
        already_applied = (not "No file to patch" in patch_result.stdout and "Reversed (or previously applied)" in patch_result.stdout and
                         "failed" not in patch_result.stdout.lower())
        logger.debug("Returned from dry run, processing results...")
        logger.debug(f"Dry run status: {dry_run_status}")

        # If patch indicates changes are already applied, return success
        if already_applied:
            logger.info("All changes are already applied")
            return {"status": "success", "details": {
                "succeeded": [],
                "failed": [],
                "failed": [],
                "already_applied": list(dry_run_status.keys())
            }}

        # Apply successful hunks with system patch if any
        # fixme: we should probably be iterating success only, but this will also hit already applied cases
        if any(success for success in dry_run_status.values()):
            logger.info(f"Applying successful hunks ({sum(1 for v in dry_run_status.values() if v)}/{len(dry_run_status)}) with system patch...")
            patch_result = subprocess.run(
                ['patch', '-p1', '--forward', '--no-backup-if-mismatch', '--reject-file=-', '--batch', '--ignore-whitespace', '--verbose', '-i', '-'],
                input=git_diff,
                encoding='utf-8',
                cwd=user_codebase_dir,
                capture_output=True,
                text=True,
                timeout=10
            )

            # Actually write the successful changes
            if "misordered hunks" in patch_result.stderr:
                logger.warning("Patch reported misordered hunks - falling back to difflib")
                # Skip to difflib application
                apply_diff_with_difflib(file_path, git_diff)
                return
            elif patch_result.returncode == 0:
                logger.info("Successfully applied some hunks with patch, writing changes")
                # Verify changes were actually written
                changes_written = True

            else:
                logger.warning("Patch application had mixed results")

            patch_output = patch_result.stdout
            logger.debug(f"Raw (system) patch stdout:\n{patch_output}")
            logger.debug(f"Raw (system) patch stdout:\n{patch_result.stderr}")
            hunk_status = parse_patch_output(patch_output)

        # Record results from patch stage
        for hunk_num, success in dry_run_status.items():
            if success:
                if "Reversed (or previously applied)" in patch_output and f"Hunk #{hunk_num}" in patch_output:
                    logger.info(f"Hunk #{hunk_num} was already applied")
                    results["already_applied"].append(hunk_num)
                else:
                    logger.info(f"Hunk #{hunk_num} applied successfully")
                    results["succeeded"].append(hunk_num)
                    changes_written = True
            else:
                logger.info(f"Hunk #{hunk_num} failed to apply")
                results["failed"].append(hunk_num)

        if results["succeeded"] or results["already_applied"]:
            logger.info(f"Successfully applied {len(results['succeeded'])} hunks, "
                      f"{len(results['already_applied'])} were already applied")
            changes_written = True

        # If any hunks failed, extract them to pass onto next pipeline stage
        if results["failed"]:
            logger.info(f"Extracting {len(results['failed'])} failed hunks for next stage")
            git_diff = extract_remaining_hunks(git_diff, {h: False for h in results["failed"]})
        else:
            logger.info("Exiting pipeline die to full success condition.")
            return {"status": "success", "details": results}

        # Proceed with git apply if we have any failed hunks
        if results["failed"]:
            logger.debug("Some failed hunks reported, processing..")
            if not git_diff.strip():
                logger.warning("No valid hunks remaining to process")
                return {"status": "partial", "details": results}
            temp_path = None
            logger.info("Proceeding with git apply for remaining hunks")
            try:
                with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', suffix='.diff', delete=False) as temp_file:
                    temp_file.write(git_diff)
                    temp_path = temp_file.name

                git_result = subprocess.run(
                    ['git', 'apply', '--verbose', '--ignore-whitespace',
                     '--ignore-space-change', '--whitespace=nowarn',
                     '--check', temp_path],
                    cwd=user_codebase_dir,
                    capture_output=True,
                    text=True
                )

                if "patch does not apply" not in git_result.stderr:
                    logger.info("Changes already applied according to git apply --check")
                    return {"status": "success", "details": {
                        "succeeded": [],
                        "failed": [],
                        "already_applied": results["failed"]
                    }}

                git_result = subprocess.run(
                    ['git', 'apply', '--verbose', '--ignore-whitespace',
                     '--ignore-space-change', '--whitespace=nowarn',
                     '--reject', temp_path],
                    cwd=user_codebase_dir,
                    capture_output=True,
                    text=True
                )

                logger.debug(f"Git apply stdout:\n{git_result.stdout}")
                logger.debug(f"Git apply stderr:\n{git_result.stderr}")

                if git_result.returncode == 0:
                    logger.info("Git apply succeeded")
                    # Move hunks from failed to succeeded
                    for hunk_num in results["failed"][:]:
                        results["failed"].remove(hunk_num)
                        results["succeeded"].append(hunk_num)
                    changes_written = True
                    return {"status": "success", "details": results}
                elif "already applied" in git_result.stderr:
                    # Move hunks from failed to already_applied
                    for hunk_num in results["failed"][:]:
                        results["failed"].remove(hunk_num)
                        results["already_applied"].append(hunk_num)
                        logger.info(f"Marking hunk {hunk_num} as already applied and continuing")
                else:
                    logger.info("Git apply failed, moving to difflib stage...")
                    # Continue to difflib
            finally:
                if os.path.exists(temp_path):
                    os.unlink(temp_path)

            # If git apply failed, try difflib with the same hunks we just tried
            logger.info("Attempting to apply changes with difflib")
            try:
                logger.info("Starting difflib application...")
                # Parse the remaining hunks for difflib
                if git_diff:
                    logger.debug(f"Passing to difflib:\n{git_diff}")
                    try:
                        apply_diff_with_difflib(file_path, git_diff)
                        # If difflib succeeds, move remaining failed hunks to succeeded
                        for hunk_num in results["failed"][:]:
                            results["failed"].remove(hunk_num)
                            results["succeeded"].append(hunk_num)
                        changes_written = True
                        return {"status": "success", "details": results}
                    except Exception as e:
                        if isinstance(e, PatchApplicationError) and e.details.get("type") == "already_applied":
                            # Move failed hunks to already_applied
                            for hunk_num in results["failed"][:]:
                                results["failed"].remove(hunk_num)
                                results["already_applied"].append(hunk_num)
                            return {"status": "success", "details": results}
                        logger.error(f"Difflib application failed: {str(e)}")
                        raise
            except PatchApplicationError as e:
                logger.error(f"Difflib application failed: {str(e)}")
                if e.details.get("type") == "already_applied":
                    return {"status": "success", "details": results}
                if changes_written:
                    return {"status": "partial", "details": results}
                raise
        else:
            logger.debug("Unreachable? No hunks reported failure, exiting pipeline after system patch stage.")

    except Exception as e:
        logger.error(f"Error applying patch: {str(e)}")
        raise
    finally:
        cleanup_patch_artifacts(user_codebase_dir, file_path)

    # Return final status
    if len(results["failed"]) == 0:
        return {"status": "success", "details": results}
    elif changes_written:
        return {"status": "partial", "details": results}
    return {"status": "error", "details": results}

def parse_patch_output(patch_output: str) -> Dict[int, bool]:
    """Parse patch command output to determine which hunks succeeded/failed.
    Returns a dict mapping hunk number to success status."""
    hunk_status = {}
    logger.debug(f"Parsing patch output:\n{patch_output}")

    in_patch_output = False
    current_hunk = None
    for line in patch_output.splitlines():
        if "Patching file" in line:
            in_patch_output = True
            continue
        if not in_patch_output:
            continue

        # Track the current hunk number
        hunk_match = re.search(r'Hunk #(\d+)', line)
        if hunk_match:
            current_hunk = int(hunk_match.group(1))

        # Check for significant adjustments that should invalidate "success"
        if current_hunk is not None:
            if "succeeded at" in line:
                hunk_status[current_hunk] = True
                logger.debug(f"Hunk {current_hunk} succeeded")
            elif "failed" in line:
                logger.debug(f"Hunk {current_hunk} failed")

        # Match lines like "Hunk #1 succeeded at 6."
        match = re.search(r'Hunk #(\d+) (succeeded at \d+(?:\s+with fuzz \d+)?|failed)', line)
        if match:
            hunk_num = int(match.group(1))
            # Consider both clean success and fuzzy matches as successful
            success = 'succeeded' in match.group(2)
            hunk_status[hunk_num] = success
            logger.debug(f"Found hunk {hunk_num}: {'succeeded' if success else 'failed'}")

    logger.debug(f"Final hunk status: {hunk_status}")
    return hunk_status

def extract_remaining_hunks(git_diff: str, hunk_status: Dict[int,bool]) -> str:
    """Extract hunks that weren't successfully applied."""
    logger.debug("Extracting remaining hunks from diff")

    logger.debug(f"Hunk status before extraction: {json.dumps(hunk_status, indent=2)}")

    # Parse the original diff into hunks
    lines = git_diff.splitlines()
    hunks = []
    current_hunk = []
    headers = []
    hunk_count = 0
    in_hunk = False

    for line in lines:
        if line.startswith(('diff --git', '--- ', '+++ ')):
            headers.append(line)
        elif line.startswith('@@'):
            hunk_count += 1
            if current_hunk:
                if current_hunk:
                    hunks.append((hunk_count - 1, current_hunk))

            # Only start collecting if this hunk failed
            if hunk_count in hunk_status and not hunk_status[hunk_count]:
                logger.debug(f"Including failed hunk #{hunk_count}")
                current_hunk = [f"{line} Hunk #{hunk_count}"]
                in_hunk = True
            else:
                logger.debug(f"Skipping successful hunk #{hunk_count}")
                current_hunk = []
                in_hunk = False
        elif in_hunk:
            current_hunk.append(line)
            if not line.startswith((' ', '+', '-', '\\')):
                # End of hunk reached
                if current_hunk:
                    hunks.append(current_hunk)
                current_hunk = []
                in_hunk = False

    if current_hunk:
        hunks.append((hunk_count, current_hunk))

    # Build final result with proper spacing
    result = []
    result.extend(headers)
    for _, hunk_lines in hunks:
        result.extend(hunk_lines)

    if not result:
        logger.warning("No hunks to extract")
        return ''

    final_diff = '\n'.join(result) + '\n'
    logger.debug(f"Extracted diff for remaining hunks:\n{final_diff}")
    return final_diff
