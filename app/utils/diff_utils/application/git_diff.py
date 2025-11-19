"""
Utilities for applying git diffs.
"""

import os
import subprocess
import tempfile
import glob
import json
import re
import io
from typing import Dict, List, Any, Optional

from app.utils.logging_utils import logger
from ..validation.validators import is_hunk_already_applied
from ..core.exceptions import PatchApplicationError
from ..parsing.diff_parser import extract_target_file_from_diff, split_combined_diff
from ..validation.validators import is_new_file_creation
from ..parsing.diff_parser import parse_unified_diff_exact_plus
from ..file_ops.file_handlers import create_new_file, cleanup_patch_artifacts, remove_reject_file_if_exists
# Remove circular import
# from .patch_apply import apply_diff_with_difflib

def debug_patch_issues(patch_content: str) -> None:
    """
    Debug common issues in patch files that might cause git apply to fail.
    
    Args:
        patch_content: The patch content to debug
    """
    lines = patch_content.splitlines()
    issues = []
    
    # Check for basic structure
    if not any(line.startswith('diff --git') for line in lines):
        issues.append("Missing 'diff --git' header")
    
    if not any(line.startswith('--- ') for line in lines):
        issues.append("Missing '--- ' header")
        
    if not any(line.startswith('+++ ') for line in lines):
        issues.append("Missing '+++ ' header")
    
    if not any(line.startswith('@@ ') for line in lines):
        issues.append("Missing '@@ ' hunk header")
    
    # Check for line ending consistency
    crlf_count = patch_content.count('\r\n')
    lf_count = patch_content.count('\n') - crlf_count
    if crlf_count > 0 and lf_count > 0:
        issues.append(f"Mixed line endings: {crlf_count} CRLF, {lf_count} LF")
    
    # Check for malformed hunk headers
    for i, line in enumerate(lines):
        if line.startswith('@@ '):
            if not re.match(r'^@@ -\d+(?:,\d+)? \+\d+(?:,\d+)? @@', line):
                issues.append(f"Malformed hunk header at line {i+1}: {line}")
    
    # Check for lines in hunks without proper prefix
    in_hunk = False
    for i, line in enumerate(lines):
        if line.startswith('@@ '):
            in_hunk = True
            continue
        
        if in_hunk and line and not line.startswith((' ', '+', '-', '\\')):
            if line.startswith('diff ') or line.startswith('index ') or line.startswith('--- ') or line.startswith('+++ '):
                in_hunk = False
            else:
                issues.append(f"Line in hunk without proper prefix at line {i+1}: {line}")
    
    # Log all issues
    if issues:
        logger.warning(f"Found {len(issues)} issues in patch:")
        for issue in issues:
            logger.warning(f"  - {issue}")
    else:
        logger.info("No obvious issues found in patch format")
 
def sanitize_patch_for_git_apply(patch_content: str) -> str:
    """
    Sanitize a patch to make it compatible with git apply.
    
    Args:
        patch_content: The original patch content
        
    Returns:
        Sanitized patch content
    """
    # Normalize line endings to LF
    patch_content = patch_content.replace('\r\n', '\n')
    
    lines = patch_content.splitlines()
    sanitized_lines = []
    in_hunk = False
    
    for line in lines:
        # Fix patch headers (no spaces allowed at start of these lines)
        if line.startswith('diff --git') or line.startswith('index ') or \
           line.startswith('--- ') or line.startswith('+++ ') or \
           line.startswith('@@ '):
            in_hunk = line.startswith('@@ ')
            # Ensure no trailing whitespace in headers
            sanitized_lines.append(line.rstrip())
            continue
            
        # Handle hunk content
        if in_hunk:
            # Ensure lines start with proper prefix: ' ', '+', '-', or '\'
            if not line.startswith((' ', '+', '-', '\\')):
                # If we're in a hunk and the line doesn't start with a valid prefix,
                # this is likely the start of a new section
                in_hunk = False
            else:
                sanitized_lines.append(line)
                continue
        
        # Non-hunk lines
        sanitized_lines.append(line)
    
    # Ensure the patch ends with a newline
    sanitized = '\n'.join(sanitized_lines)
    if not sanitized.endswith('\n'):
        sanitized += '\n'
        
    return sanitized
 
def normalize_patch_with_whatthepatch(patch_content: str) -> str:
    """
    Normalize a patch using whatthepatch to ensure it's valid for git apply.
    
    Args:
        patch_content: The original patch content
        
    Returns:
        Normalized patch content
    """
    try:
        import whatthepatch
        
        # First try to parse the patch
        try:
            patches = list(whatthepatch.parse_patch(patch_content))
        except ValueError as e:
            logger.warning(f"whatthepatch parsing error: {str(e)}")
            # If parsing fails, try to handle embedded diff markers
            return handle_embedded_diff_markers(patch_content)
                
        if not patches:
            logger.warning("No valid patches found in diff")
            return patch_content
        
        # Fall back to custom sanitization which is more reliable
        return sanitize_patch_for_git_apply(patch_content)
        
    except Exception as e:
        logger.error(f"Error normalizing diff: {str(e)}")
        # Fall back to custom sanitization
        return sanitize_patch_for_git_apply(patch_content)
 
def handle_embedded_diff_markers(diff_content: str) -> str:
    """
    Handle diffs that contain embedded diff markers (lines starting with '---' or '+++')
    that could confuse the parser.
    
    Args:
        diff_content: The diff content to process
        
    Returns:
        The processed diff content
    """
    lines = diff_content.splitlines()
    result = []
    
    # Track if we're in a hunk
    in_hunk = False
    in_header = True
    
    for i, line in enumerate(lines):
        # Always keep diff headers
        if line.startswith(('diff --git', 'index')):
            result.append(line)
            in_header = True
            in_hunk = False
            continue
            
        # File path headers
        if line.startswith(('--- ', '+++ ')):
            result.append(line)
            in_header = True
            in_hunk = False
            continue
            
        # Hunk headers
        if line.startswith('@@'):
            result.append(line)
            in_header = False
            in_hunk = True
            continue
            
        # Handle content lines
        if in_hunk:
            # Only include lines that start with proper diff markers
            if line.startswith((' ', '+', '-')):
                result.append(line)
            elif line.startswith('\\'):  # No newline marker
                result.append(line)
            else:
                # We've reached the end of the hunk
                in_hunk = False
                # If this is a new header, process it in the next iteration
                if line.startswith(('diff --git', '--- ', '+++ ', '@@')):
                    i -= 1  # Back up to reprocess this line
                    continue
        elif not in_header:
            # We're outside a hunk and not in a header
            if line.startswith(('diff --git', '--- ', '+++ ', '@@')):
                i -= 1  # Back up to reprocess this line as a header
                continue
    
    return '\n'.join(result)

def clean_input_diff(diff_content: str) -> str:
    """
    Initial cleanup of diff content before parsing, with strict hunk enforcement.
    
    Args:
        diff_content: The diff content to clean
        
    Returns:
        The cleaned diff content
    """
    logger.debug(diff_content)

    result_lines = []

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
                old_count = int(match.group(1)) if match.group(1) else 1
                new_count = int(match.group(1)) if match.group(1) else 1
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
    result = '\n'.join(result_lines)
    # Preserve trailing newline if original had one
    if diff_content.endswith('\n'):
        result += '\n'
    return result

def correct_git_diff(git_diff: str, file_path: str) -> str:
    """
    Correct a git diff using unidiff for parsing and validation.
    Handles embedded diff markers and other edge cases.
    
    Args:
        git_diff: The git diff content to correct
        file_path: The target file path
        
    Returns:
        The corrected git diff
    """
    logger.info(f"Processing diff for {file_path}")

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

        # Modify hunk headers to be more lenient about line counts
        lines = cleaned_diff.splitlines()

        try:
            # Parse and normalize with whatthepatch
            logger.info(f"Normalizing diff with whatthepatch")
            try:
                import whatthepatch
                parsed_patches = list(whatthepatch.parse_patch(cleaned_diff))
            except ValueError as e:
                logger.warning(f"whatthepatch parsing error: {str(e)}")
                # If parsing fails, try to handle embedded diff markers
                from .hunk_utils import handle_embedded_diff_markers
                return handle_embedded_diff_markers(cleaned_diff)
                
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
                elif current_hunk:
                    # Include all lines after hunk header until next hunk or end
                    if line.startswith(('+', '-', ' ', '')):
                        current_hunk.append(line)
                    elif line.startswith('diff '):
                        # New file, stop current hunk
                        break
            if current_hunk:
                original_hunks.append(current_hunk)
                
            # Process each hunk while preserving structure
            for hunk in original_hunks:
                hunk_header = hunk[0]
                match = re.match(r'^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@', hunk_header)
                if not match:
                    continue
                old_start = int(match.group(1))
                new_start = int(match.group(3))
                # Count actual changes in this hunk
                old_count = sum(1 for line in hunk[1:] if line.startswith((' ', '-')))
                new_count = sum(1 for line in hunk[1:] if line.startswith((' ', '+')))
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

def create_diff_from_hunk(hunk: Dict[str, Any], file_path: str) -> str:
    """
    Create a unified diff from a single hunk.
    
    Args:
        hunk: The hunk to convert to a diff
        file_path: Path to the file
        
    Returns:
        A unified diff containing just this hunk
    """
    # Create a minimal diff header
    diff_lines = []
    diff_lines.append(f"diff --git a/{file_path} b/{file_path}")
    diff_lines.append(f"--- a/{file_path}")
    diff_lines.append(f"+++ b/{file_path}")
    
    # Create the hunk header
    header = f"@@ -{hunk['old_start']},{hunk['old_lines']} +{hunk['new_start']},{hunk['new_lines']} @@"
    diff_lines.append(header)
    
    # Add the hunk content
    for line in hunk['old_block']:
        diff_lines.append(f"-{line}")
    for line in hunk['new_lines']:
        diff_lines.append(f"+{line}")
    
    return '\n'.join(diff_lines)

def reduce_hunk_context(hunk: Dict[str, Any], context_lines: int = 1) -> Dict[str, Any]:
    """
    Reduce the context lines in a hunk to make it more likely to apply.
    
    Args:
        hunk: The hunk to modify
        context_lines: Number of context lines to keep (0 for no context)
        
    Returns:
        A new hunk with reduced context
    """
    # Create a copy of the hunk to modify
    reduced_hunk = {
        'old_start': hunk['old_start'],
        'old_lines': hunk['old_lines'],
        'new_start': hunk['new_start'],
        'new_lines': hunk['new_lines'],
        'number': hunk['number'],
        'original_hunk': hunk.get('original_hunk', hunk['number'])
    }
    
    # Extract the changes from the old block
    old_block = hunk['old_block']
    new_lines = hunk['new_lines']
    
    # Identify which lines are context (present in both old and new)
    context_indices = []
    for i, line in enumerate(old_block):
        if line in new_lines:
            context_indices.append(i)
    
    # Keep only the specified number of context lines
    if context_lines == 0:
        # No context, just keep the changed lines
        reduced_old_block = [line for i, line in enumerate(old_block) if i not in context_indices]
        reduced_new_lines = [line for line in new_lines if line not in old_block]
    else:
        # Keep limited context
        reduced_old_block = []
        reduced_new_lines = []
        # TODO: Implement context reduction logic
        # This is more complex and would require tracking which lines are
        # context vs. changes and keeping only N context lines
    
    reduced_hunk['old_block'] = reduced_old_block
    reduced_hunk['new_lines'] = reduced_new_lines
    
    return reduced_hunk

def parse_patch_output(patch_output: str, stderr: str = "") -> Dict[int, Dict[str, Any]]:

    """
     Parse patch command output to determine which hunks succeeded/failed.
    Args:
        patch_output: The output from the patch command
        stderr: The stderr output from the patch command (optional)
        
    Returns:
        A dictionary mapping hunk number to status information
    """
    hunk_status = {}
    logger.debug(f"Parsing patch output:\n{patch_output}")
    if stderr:
        logger.debug(f"Stderr output:\n{stderr}")
        
    # Check if the patch was detected as already applied or reversed
    reversed_or_applied = "Reversed (or previously applied)" in patch_output
        
    # First check for corrupt patch errors in stderr
    if stderr and "corrupt patch" in stderr:
        logger.warning(f"Corrupt patch detected in stderr: {stderr}")
        # Mark all hunks as failed
        for i in range(1, 10):  # Assume up to 10 hunks for safety
            hunk_status[i] = {
               "status": "failed",
               "error": "corrupt_patch",
               "details": f"Corrupt patch detected: {stderr.strip()}"
            }
        return hunk_status

    in_patch_output = False
    current_hunk = None
    
    # Check for malformed patch errors in stderr
    malformed_hunks = set()
    if stderr:
        # Check for general malformed patch errors (without hunk number)
        if "malformed patch" in stderr.lower():
            logger.warning(f"Malformed patch detected in stderr: {stderr}")
            # If no specific hunk number, mark all hunks as failed
            if "Hunk #" not in stderr:
                # Return empty dict to indicate general failure
                # The caller should check return code
                return {}
        
        # Extract hunk numbers from malformed patch errors
        malformed_pattern = re.compile(r'malformed patch at line \d+:.*?Hunk #(\d+)')
        for match in malformed_pattern.finditer(stderr):
            hunk_num = int(match.group(1))
            malformed_hunks.add(hunk_num)
            logger.debug(f"Found malformed patch for Hunk #{hunk_num}")
            hunk_status[hunk_num] = {
                "status": "failed",
                "error": "malformed_patch",
                "details": f"Malformed patch detected in hunk #{hunk_num}"
            }
    
    # Process stdout for success/failure information
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
            # If this hunk was marked as malformed, it's definitely failed for this stage
            if current_hunk in malformed_hunks:
                # Only update if not already set
                if current_hunk not in hunk_status:
                    hunk_status[current_hunk] = {
                        "status": "failed",
                        "error": "malformed_patch",
                        "details": f"Malformed patch detected in hunk #{current_hunk}"
                    }
                logger.debug(f"Hunk {current_hunk} failed due to malformed patch")
                continue
                
            if "succeeded at" in line:
                position_match = re.search(r'succeeded at (\d+)', line)
                position = int(position_match.group(1)) if position_match else None
                
                # Check if there was fuzz applied
                fuzz_match = re.search(r'with fuzz (\d+)', line)
                fuzz = int(fuzz_match.group(1)) if fuzz_match else 0

                # If the patch was detected as reversed or already applied, we need to be more careful
                # Don't automatically mark as already_applied - we need to verify the actual file content
                status = "needs_verification" if reversed_or_applied else "succeeded"
                hunk_status[current_hunk] = {
                    "status": status,
                    "position": position,
                    "fuzz": fuzz,
                    "reversed_or_applied_detected": reversed_or_applied
                }
                logger.debug(f"Hunk {current_hunk} {status} at position {position}" +
                            (f" with fuzz {fuzz}" if fuzz > 0 else ""))
            elif "failed" in line:
                position_match = re.search(r'FAILED at (\d+)', line)
                position = int(position_match.group(1)) if position_match else None
                
                hunk_status[current_hunk] = {
                    "status": "failed",
                    "position": position,
                    "error": "application_failed"
                }
                logger.debug(f"Hunk {current_hunk} failed at position {position}")
            elif "already applied" in line:
                position_match = re.search(r'already applied at position (\d+)', line)
                position = int(position_match.group(1)) if position_match else None
                
                hunk_status[current_hunk] = {
                    "status": "already_applied",
                    "position": position
                }
                logger.debug(f"Hunk {current_hunk} already applied at position {position}")

        # Match lines like "Hunk #1 succeeded at 6."
        match = re.search(r'Hunk #(\d+) (succeeded at \d+(?:\s+with fuzz \d+)?|failed|is already applied)', line)
        if match:
            hunk_num = int(match.group(1))
            result = match.group(2)
            
            # Skip if we've already processed this hunk above
            if hunk_num in hunk_status:
                continue
                
            # Process based on the result
            if 'succeeded' in result:
                position_match = re.search(r'at (\d+)', result)
                position = int(position_match.group(1)) if position_match else None
                
                fuzz_match = re.search(r'with fuzz (\d+)', result)
                fuzz = int(fuzz_match.group(1)) if fuzz_match else 0

                # If the patch was detected as reversed or already applied, we need to be more careful
                # Don't automatically mark as already_applied - we need to verify the actual file content
                status = "needs_verification" if reversed_or_applied else "succeeded"
                hunk_status[hunk_num] = {
                    "status": status,
                    "position": position,
                    "fuzz": fuzz,
                    "reversed_or_applied_detected": reversed_or_applied
                }
                logger.debug(f"Hunk {hunk_num} {status} at position {position}" + 
                            (f" with fuzz {fuzz}" if fuzz > 0 else ""))
            elif 'failed' in result:
                position_match = re.search(r'at (\d+)', result)
                position = int(position_match.group(1)) if position_match else None
                
                hunk_status[hunk_num] = {
                    "status": "failed",
                    "position": position,
                    "error": "application_failed"
                }
                logger.debug(f"Hunk {hunk_num} failed at position {position}")
            elif 'already applied' in result:
                position_match = re.search(r'at position (\d+)', result)
                position = int(position_match.group(1)) if position_match else None
                
                hunk_status[hunk_num] = {
                    "status": "already_applied",
                    "position": position
                }
                logger.debug(f"Hunk {hunk_num} already applied at position {position}")

    logger.debug(f"Final hunk status: {json.dumps(hunk_status, indent=2)}")
    return hunk_status

def apply_diff_atomically(file_path: str, git_diff: str) -> Dict[str, Any]:
    """
    Apply a git diff atomically to avoid race conditions with file watchers.
    
    This function applies all hunks in memory and writes the result in a single operation,
    preventing partial updates that can trigger file watchers prematurely.
    
    Args:
        file_path: Path to the file to modify
        git_diff: The git diff to apply
        
    Returns:
        Dict with status information about the operation
    """
    logger.info(f"Applying diff atomically to {file_path}")
    
    # Read the original file content
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            original_content = f.read()
            original_lines = original_content.splitlines(True)  # Keep line endings
    except FileNotFoundError:
        if is_new_file_creation(git_diff.splitlines()):
            # Handle new file creation
            user_codebase_dir = os.environ.get("ZIYA_USER_CODEBASE_DIR", "")
            create_new_file(git_diff, user_codebase_dir)
            return {"status": "success", "details": {"new_file": True, "changes_written": True}}
        else:
            return {"status": "error", "details": {"error": f"File not found: {file_path}"}}
    
    # Parse the hunks
    try:
        hunks = list(parse_unified_diff_exact_plus(git_diff, file_path))
        if not hunks:
            return {"status": "error", "details": {"error": "No valid hunks found in diff"}}
    except Exception as e:
        logger.error(f"Error parsing diff: {str(e)}")
        return {"status": "error", "details": {"error": f"Error parsing diff: {str(e)}"}}
    
    # Check for malformed hunks first
    malformed_hunks = []
    for i, hunk in enumerate(hunks, 1):
        hunk_id = hunk.get('number', i)
        
        # Check if the hunk is malformed
        if 'header' in hunk and '@@ -' in hunk['header']:
            header_match = re.match(r'^@@ -(\d+),(\d+) \+(\d+),(\d+) @@', hunk['header'])
            if not header_match:
                logger.warning(f"Malformed hunk header detected: {hunk['header']}")
                malformed_hunks.append(hunk_id)
                continue
        
        # Check if essential hunk data is missing
        if not hunk.get('old_block') or not hunk.get('new_lines'):
            logger.warning(f"Malformed hunk detected: missing old_block or new_lines")
            malformed_hunks.append(hunk_id)
            continue
    
    # If any hunks are malformed, return an error
    if malformed_hunks:
        logger.warning(f"Found {len(malformed_hunks)} malformed hunks, aborting")
        return {"status": "error", "details": {"error": "Malformed hunks detected", "malformed_hunks": malformed_hunks}}
    
    # Check if all hunks are already applied
    all_already_applied = True
    already_applied_hunks = []
    
    for i, hunk in enumerate(hunks, 1):
        hunk_applied = False
        for pos in range(len(original_lines) + 1):  # +1 to allow checking at EOF
            if is_hunk_already_applied(original_lines, hunk, pos, ignore_whitespace=True):
                already_applied_hunks.append(i)
                hunk_applied = True
                break
        if not hunk_applied:
            all_already_applied = False
            break
    
    if all_already_applied:
        logger.info("All hunks are already applied")
        return {"status": "success", "details": {"already_applied": already_applied_hunks, "changes_written": False}}
    
    # Apply the diff in memory using difflib
    try:
        from io import StringIO
        from .patch_apply import apply_diff_with_difflib_hybrid_forced
        from .language_integration import verify_changes_with_language_handler
        
        # Apply the diff to the content
        modified_lines = apply_diff_with_difflib_hybrid_forced(file_path, git_diff, original_lines)
        modified_content = ''.join(modified_lines)
        
        # Check if content actually changed
        if modified_content == original_content:
            logger.warning("No changes were made to the content")
            return {"status": "success", "details": {"already_applied": already_applied_hunks, "changes_written": False}}
        
        # Verify changes with language handler
        is_valid, error_details = verify_changes_with_language_handler(file_path, original_content, modified_content)
        if not is_valid:
            logger.error(f"Language validation failed: {error_details}")
            return {"status": "error", "details": error_details}
        
        # Write the modified content back to the file in a single operation
        with open(file_path, 'w', encoding='utf-8') as f:
            f.write(modified_content)
        
        logger.info(f"Successfully wrote changes to {file_path}")
        return {"status": "success", "details": {"succeeded": list(range(1, len(hunks) + 1)), "changes_written": True}}
        
    except Exception as e:
        logger.error(f"Error applying diff: {str(e)}")
        return {"status": "error", "details": {"error": str(e)}}
    finally:
        # Clean up any temporary files
        cleanup_patch_artifacts(os.path.dirname(file_path), file_path)

def extract_remaining_hunks(git_diff: str, hunk_status: Dict[int, bool]) -> str:
    """
    Extract hunks that weren't successfully applied.
    
    Args:
        git_diff: The git diff content
        hunk_status: Dictionary mapping hunk number to success status
        
    Returns:
        A diff containing only the hunks that failed to apply
    """
    logger.debug("Extracting remaining hunks from diff")

    logger.debug(f"Hunk status before extraction: {json.dumps({str(k): v for k, v in hunk_status.items()}, indent=2)}")

    # Parse the original diff into hunks
    lines = git_diff.splitlines()
    hunks = []
    current_hunk = []
    file_headers = []
    current_file_headers = []
    hunk_count = 0
    in_hunk = False
    in_file = False
    current_file = None

    # Group hunks by file
    file_to_hunks = {}

    for line in lines:
        # Track file boundaries
        if line.startswith('diff --git'):
            # Save previous file headers and hunks
            if current_file and current_file_headers:
                file_to_hunks[current_file] = {
                    'headers': current_file_headers.copy(),
                    'hunks': []
                }
                
            # Start new file
            in_file = True
            current_file = line
            current_file_headers = [line]
            continue
        
        # Collect file headers
        if line.startswith(('--- ', '+++ ', 'index ')):
            if in_file:
                current_file_headers.append(line)
                if line.startswith('+++ '):
                    # Extract file path as key
                    current_file = line
            continue
            
        # Process hunk headers
        if line.startswith('@@'):
            hunk_count += 1
            if current_hunk:
                if current_file in file_to_hunks:
                    file_to_hunks[current_file]['hunks'].append((hunk_count - 1, current_hunk))
                else:
                    file_to_hunks[current_file] = {
                        'headers': current_file_headers.copy(),
                        'hunks': [(hunk_count - 1, current_hunk)]
                    }

            # Only start collecting if this hunk failed
            if hunk_count in hunk_status and not hunk_status[hunk_count]:
                logger.debug(f"Including failed hunk #{hunk_count}")
                current_hunk = [line] # Keep original header
                in_hunk = True
            else:
                logger.debug(f"Skipping successful hunk #{hunk_count}")
                current_hunk = []
                in_hunk = False
            continue
            
        # Collect hunk content
        if in_hunk:
            current_hunk.append(line)
            # Check for end of hunk (non-context, non-diff line)
            if not line.startswith((' ', '+', '-', '\\')):
                # End of hunk reached
                if current_hunk and current_file:
                    if current_file in file_to_hunks:
                        file_to_hunks[current_file]['hunks'].append((hunk_count, current_hunk))
                    else:
                        file_to_hunks[current_file] = {
                            'headers': current_file_headers.copy(),
                            'hunks': [(hunk_count, current_hunk)]
                        }
                current_hunk = []
                in_hunk = False

    # Add the last hunk if we have one
    if current_hunk and current_file:
        if current_file in file_to_hunks:
            file_to_hunks[current_file]['hunks'].append((hunk_count, current_hunk))
        else:
            file_to_hunks[current_file] = {
                'headers': current_file_headers.copy(),
                'hunks': [(hunk_count, current_hunk)]
            }

    # Build final result with proper file structure
    result = []
    
    # Now build the result with proper file structure
    for file_key, file_data in file_to_hunks.items():
        # Only include files that have at least one failed hunk
        if not file_data['hunks']:
            continue
            
        # Add file headers
        result.extend(file_data['headers'])
        
        # Add all hunks for this file
        for hunk_id, hunk_lines in file_data['hunks']:
            # Skip empty hunks
            if not hunk_lines:
                continue
            result.extend(hunk_lines)

    if not result:
        logger.warning("No hunks to extract")
        return ''

    final_diff = '\n'.join(result)
    if not final_diff.endswith('\n'):
        final_diff += '\n'
    logger.debug(f"Extracted diff for remaining hunks:\n{final_diff}")
    return final_diff

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
    
    # Split combined diffs if present
    individual_diffs = split_combined_diff(git_diff)
    if len(individual_diffs) > 1:
        # Find the diff that matches our target file
        matching_diff = next((diff for diff in individual_diffs 
                            if extract_target_file_from_diff(diff) == file_path), None)
        if matching_diff:
            git_diff = matching_diff
    
    changes_written = False
    results = {
        "succeeded": [],
        "failed": [],
        "already_applied": [],
        "hunk_statuses": {}
    }
    
    # Correct the diff using existing functionality
    if file_path:
        git_diff = correct_git_diff(git_diff, file_path)
    else:
        file_path = extract_target_file_from_diff(git_diff)
        if not file_path:
            raise ValueError("Could not determine target file path")
    
    # Use atomic application to avoid race conditions with file watchers
    atomic_result = apply_diff_atomically(file_path, git_diff)
    if atomic_result:
        return atomic_result

    user_codebase_dir = os.environ.get("ZIYA_USER_CODEBASE_DIR")
    if not user_codebase_dir:
        raise ValueError("ZIYA_USER_CODEBASE_DIR environment variable is not set")

    logger.info(f"git_diff before splitlines: length={len(git_diff)}, bytes={git_diff.encode('utf-8')[:50]}")
    # Split into lines for analysis
    diff_lines = git_diff.splitlines()
    logger.info(f"git_diff after splitlines: length={len(git_diff)}, bytes={git_diff.encode('utf-8')[:50]}")
    logger.info(f"diff_lines length: {len(diff_lines)}")

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
                "already_applied": list(dry_run_status.keys()),
                "changes_written": False
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
            results["changes_written"] = changes_written
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

                # Debug log the complete git apply --check output
                logger.info("git apply --check stdout:")
                logger.info(git_result.stdout)
                logger.info("git apply --check stderr:")
                logger.info(git_result.stderr)
                logger.info("git apply --check return code: %d", git_result.returncode)

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
                logger.debug(f"Git apply return code: {git_result.returncode}")

                if git_result.returncode == 0:
                    logger.info("Git apply succeeded")
                    # Move hunks from failed to succeeded
                    results["succeeded"].extend([h for h in hunk_status.keys() if hunk_status[h]])
                    changes_written = True
                    results["changes_written"] = changes_written
                    return {"status": "success", "details": results}
                elif "already applied" in git_result.stderr:
                    # Handle mixed case of already applied and other failures
                    logger.info("Found mix of already applied and other hunks")
                    for hunk_num in results["failed"][:]:
                        if str(hunk_num) in git_result.stderr and "already applied" in git_result.stderr:
                            results["failed"].remove(hunk_num)
                            results["already_applied"].append(hunk_num)
                            logger.info(f"Marking hunk {hunk_num} as already applied")
                        else:
                            # Keep in failed list to try with difflib
                            logger.info(f"Keeping hunk {hunk_num} for difflib attempt")
            finally:
                logger.info(f"After git apply: succeeded={len(results['succeeded'])}, "
                          f"failed={len(results['failed'])}, already_applied={len(results['already_applied'])}")
                if os.path.exists(temp_path):
                    os.unlink(temp_path)

            # If git apply failed (non-zero return code, including partial), try difflib with the same hunks we just tried
            logger.info("Attempting to apply changes with difflib")
            try:
                logger.info(f"Starting difflib application with diff content:\n{git_diff}")
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
                        results["changes_written"] = changes_written
                        return {"status": "success", "details": results}
                    except Exception as e:
                        if isinstance(e, PatchApplicationError) and e.details.get("type") == "already_applied":
                            # Move failed hunks to already_applied
                            for hunk_num in results["failed"][:]:
                                results["failed"].remove(hunk_num)
                                results["already_applied"].append(hunk_num)
                            results["changes_written"] = False
                            return {"status": "success", "details": results}
                        logger.error(f"Difflib application failed: {str(e)}")
                        raise
            except PatchApplicationError as e:
                logger.error(f"Difflib application failed: {str(e)}")
                if e.details.get("type") == "already_applied":
                    results["changes_written"] = False
                    return {"status": "success", "details": results}
                if changes_written:
                    # Return partial success with specific hunk details
                    return {"status": "partial", "details": {
                        "succeeded": results["succeeded"],
                        "failed": results["failed"]
                    }}
                raise
        else:
            logger.debug("Unreachable? No hunks reported failure, exiting pipeline after system patch stage.")

    except Exception as e:
        logger.error(f"Error applying patch: {str(e)}")
        raise
    finally:
        cleanup_patch_artifacts(user_codebase_dir, file_path)

    # Return final status
    results["changes_written"] = changes_written
    if len(results["failed"]) == 0:
        return {"status": "success", "details": results}
    elif changes_written:
        return {"status": "partial", "details": results}
    return {"status": "error", "details": results}
