import os
import subprocess
import json
from io import StringIO
import time
from typing import Dict, Optional, Union, List, Tuple, Any
import whatthepatch
import re
from app.utils.logging_utils import logger

class PatchApplicationError(Exception):
    """Custom exception for patch application failures"""
    def __init__(self, message: str, details: Dict):
        super().__init__(message)
        self.details = details

def clean_input_diff(diff_content: str) -> str:
    """Initial cleanup of diff content before parsing."""
    logger.debug(diff_content)

    # Remove any content after triple backticks
    if '```' in diff_content:
        diff_content = diff_content.split('```')[0]
    # Handle escaped newlines
    if '\\n' in diff_content:
        try:
            # Remove any outer quotes
            diff_content = diff_content.strip("'\"")
            # Convert escaped newlines to real newlines
            diff_content = diff_content.encode().decode('unicode_escape')
        except Exception as e:
            logger.warning(f"Failed to decode escaped newlines: {e}")
    return diff_content

def normalize_diff(diff_content: str) -> str:
    """
    Normalize a diff using whatthepatch for proper parsing and reconstruction.
    Handles incomplete hunks, context issues, and line count mismatches.
    """
    logger.info("Normalizing diff with whatthepatch")
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
                # Keep the original hunk header and its content
                result.append(line)
                i += 1
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

    logger.debug("Analyzing diff lines for new file creation:")
    for i, line in enumerate(diff_lines[:5]):
        logger.debug(f"Line {i}: {line}")

    patterns = {
        'git_new': diff_lines[0].startswith('diff --git') and 'b/dev/null' not in diff_lines[0],
        'new_mode': any(line == 'new file mode 100644' for line in diff_lines[:3]),
        'null_source': any(line == '--- /dev/null' for line in diff_lines[:4]),
        'new_target': any(line.startswith('+++ b/') for line in diff_lines[:4]),
        'zero_hunk': any('@@ -0,0 +1,' in line for line in diff_lines),
        'dev_null_source': any('diff --git a/dev/null b/' in line for line in diff_lines[:1])
    }

    logger.debug(f"New file patterns detected: {patterns}")
    return patterns['dev_null_source'] or (patterns['new_mode'] and patterns['null_source'])

def create_new_file(git_diff: str, base_dir: str) -> None:
    """Create a new file from a git diff."""
    logger.info(f"Processing new file diff with length: {len(git_diff)} bytes")
    
    try:
        # Parse the diff content
        diff_lines = git_diff.splitlines()

        # Extract the file path from the diff --git line
        file_path = diff_lines[0].split(' b/')[-1]
        full_path = os.path.join(base_dir, file_path)

        # Create directory if it doesn't exist
        os.makedirs(os.path.dirname(full_path), exist_ok=True)

        # Extract the content (everything after the @@ line)
        content_lines = []
        for i, line in enumerate(diff_lines):
            if line.startswith('@@'):
                content_lines = [l[1:] for l in diff_lines[i+1:] if l.startswith('+')]
                break

        # Write the content
        content = '\n'.join(content_lines)
        with open(full_path, 'w', encoding='utf-8') as f:
            f.write(content)
            if not content.endswith('\n'):
                f.write('\n')
        logger.info(f"Successfully created new file: {file_path}")
    except Exception as e:
        logger.error(f"Error creating new file: {str(e)}")
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

        # Debug: Log the diff at various stages
        logger.info("Original diff:")
        logger.info(git_diff)
        
        # Extract headers from original diff
        diff_lines = git_diff.splitlines()
        headers = []
        for line in diff_lines:
            if line.startswith(('diff --git', 'index', '--- ', '+++ ')):
                headers.append(line)
            elif line.startswith('@@'):
                break

        # Check for new file creation
        if is_new_file_creation(cleaned_diff.splitlines()):
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
            logger.debug(f"Normalized diff:\n{normalized_diff}")
            logger.info(f"Successfully normalized diff")
            return normalized_diff

        except Exception as e:
            logger.error(f"Error normalizing diff: {str(e)}")
            raise
        
    except Exception as e:
        logger.error(f"Error correcting diff: {str(e)}")
        raise

def apply_system_patch(diff_content: str, target_dir: str) -> bool:
    """
    Apply patch using system patch command.
    Returns True if successful, False otherwise.
    """
    logger.info("Attempting to apply with system patch command...")
    try:
        # Debug: Log the exact content we're sending to patch
        logger.info("Patch input content:")
        logger.info(diff_content)
        # Ensure we have string input and encode it just once
        if isinstance(diff_content, bytes):
            diff_content = diff_content.decode('utf-8')
        result = subprocess.run(
            ['patch', '-p1', '--forward', '--ignore-whitespace'],
            input=diff_content,
            cwd=target_dir,
            capture_output=True,
            text=True,
            timeout=10
        )
        logger.info(f"Patch stdout: {result.stdout}")
        logger.info(f"Patch stderr: {result.stderr}")
        success = result.returncode == 0
        logger.info(f"Patch {'succeeded' if success else 'failed'} with return code {result.returncode}")
        return success, result
    except Exception as e:
        logger.error(f"System patch error output: {str(e)}")
        logger.error(f"System patch failed: {str(e)}")
        return False

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

def apply_diff_with_difflib(file_path: str, diff_content: str) -> None:
    """
    Apply changes using difflib when patch command fails.
    Uses fuzzy matching for line positions while maintaining structural integrity.

    Args:
        file_path: Path to the file to modify
        diff_content: Git diff content to apply
    """
    import difflib
    import re
    from typing import List, Dict, Any, Tuple

    class DiffMatchConfig:
        """Configuration for controlling how aggressively to match diffs"""
        MIN_CONFIDENCE = 0.65  # Minimum ratio to accept a match
        MAX_LINE_DISTANCE = 10  # Maximum lines to search away from expected position
        REQUIRE_EXACT_INDENT = False  # Require matching indentation
        ALLOW_PARTIAL_CONTEXT = False  # Allow matching with incomplete context
        MIN_CONTEXT_LINES = 2  # Minimum number of context lines required
        MAX_REWRITE_SIZE = 80  # Maximum number of lines that can be rewritten in one hunk

    def find_best_match(needle: List[str], haystack: List[str],
                       start_pos: int,
                       context_lines: int = 3,
                       expected_indent: Optional[int] = None,
                       hunk_size: int = 0) -> int:
        """
        Find the best matching position for a chunk of code using fuzzy matching.
        Returns the best matching line number (0-based).
        """
        if not needle or not haystack:
            return start_pos

        if hunk_size > DiffMatchConfig.MAX_REWRITE_SIZE:
            raise ValueError(f"Hunk size {hunk_size} exceeds maximum allowed size {DiffMatchConfig.MAX_REWRITE_SIZE}")

        if len(needle) < DiffMatchConfig.MIN_CONTEXT_LINES and not DiffMatchConfig.ALLOW_PARTIAL_CONTEXT:
            raise ValueError(f"Insufficient context lines ({len(needle)} < {DiffMatchConfig.MIN_CONTEXT_LINES})")

        # Create a matcher object
        matcher = difflib.SequenceMatcher(None)
        best_ratio = 0
        best_pos = start_pos

        # Limit search range to configured distance
        search_start = max(0, start_pos - DiffMatchConfig.MAX_LINE_DISTANCE)
        search_end = min(len(haystack), start_pos + DiffMatchConfig.MAX_LINE_DISTANCE)

        # Convert needle to string for matching
        needle_str = '\n'.join(needle)

        def check_indentation(window_lines: List[str]) -> bool:
            if not DiffMatchConfig.REQUIRE_EXACT_INDENT or expected_indent is None:
                return True
            try:
                window_indent = len(window_lines[0]) - len(window_lines[0].lstrip())
                return window_indent == expected_indent
            except (IndexError, AttributeError):
                return False

        if len(needle) < DiffMatchConfig.MIN_CONTEXT_LINES and not DiffMatchConfig.ALLOW_PARTIAL_CONTEXT:
            raise ValueError(f"Insufficient context lines: {len(needle)} < {DiffMatchConfig.MIN_CONTEXT_LINES}")

        # Look for best match within reasonable range
        for i in range(search_start, search_end):
            # Get a window of lines the same size as needle
            window = haystack[i:i + len(needle)]
            window_str = '\n'.join(window)

            matcher.set_seqs(needle_str, window_str)
            if not check_indentation(window):
                continue

            ratio = matcher.ratio()

            if ratio > best_ratio:
                best_ratio = ratio
                best_pos = i

            # If we find an excellent match, stop searching
            if ratio > 0.95:
                break
        # If our best match isn't confident enough, raise an error
        if best_ratio < DiffMatchConfig.MIN_CONFIDENCE:
            logger.warning(f"Low confidence match for hunk (ratio: {best_ratio:.2f})")
            return -1

        return best_pos

    # Parse the diff to extract changes
    diff_lines = diff_content.splitlines()
    hunks = []  # List to store all hunks
    current_hunk: Dict[str, Any] = {}
    current_lines: List[str] = []
    in_hunk = False

    def save_current_hunk() -> None:
        if current_hunk and (current_hunk.get('old') or current_hunk.get('new')):
            # Include some context lines in the hunk
            # Clean up any duplicate context lines
            if current_hunk.get('context_before') and current_hunk.get('old'):
                while (current_hunk['context_before'] and current_hunk['old'] and 
                       current_hunk['context_before'][-1] == current_hunk['old'][0]):
                    current_hunk['old'].pop(0)
                    current_hunk['new'].pop(0)
            
            hunks.append({
                'start_line': current_hunk['start_line'],
                'old_lines': current_hunk['old'],
                'old_lines': current_hunk['old'],
                'new_lines': current_hunk['new'],
                'context_before': current_hunk.get('context_before', []),
                'context_after': current_hunk.get('context_after', [])
            })

    context_lines: List[str] = []
    for line in diff_lines:
        if line.startswith('@@'):
            if in_hunk:
                save_current_hunk()

            # Parse hunk header
            match = re.match(r'^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@', line)
            if not match:
                logger.warning(f"Invalid hunk header: {line}")
                continue
            # Start new hunk
            in_hunk = True
            current_hunk = {
                'start_line': int(match.group(1)),
                'old': [],
                'new': [],
                'context_before': [line.rstrip('\n') for line in context_lines[-3:]] 
                                if context_lines else [],
                'context_after': []
            }
            context_lines = []

        # Handle hunk content
        elif in_hunk:
            if line.startswith(' '):  # Context line
                current_hunk['old'].append(line[1:].rstrip('\n'))
                current_hunk['new'].append(line[1:].rstrip('\n'))
                context_lines.append(line[1:])
            elif line.startswith('-'):  # Removal
                current_hunk['old'].append(line[1:].rstrip('\n'))
            elif line.startswith('+'):  # Addition
                current_hunk['new'].append(line[1:])
            elif not line.strip():  # Empty line within hunk
                current_hunk['old'].append('')
                current_hunk['new'].append('')
                context_lines.append('')
            else:  # End of hunk
                in_hunk = False
                current_hunk['context_after'] = context_lines[:3]
                save_current_hunk()
                current_hunk = {}
        else:
            # Keep track of context lines between hunks
            if line.startswith(' '):
                context_lines.append(line[1:].rstrip('\n'))

    # Save the last hunk if we have one
    if in_hunk:
        current_hunk['context_after'] = context_lines[:3]
        save_current_hunk()

    # Read the current file
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            current_lines = f.readlines()
    except FileNotFoundError:
        current_lines = []

    # Strip line endings for comparison but keep original lines
    stripped_lines = [line.rstrip('\n') for line in current_lines]

    # Apply hunks in order
    result_lines = current_lines[:]
    last_end = 0
    hunk_results = []
    for hunk in hunks:
        # Find the best matching position for this hunk
        context_to_match = (hunk['context_before'] +
                          hunk['old_lines'][:2] +
                          hunk['old_lines'][-2:] +
                          hunk['context_after'])

        suggested_start = max(last_end, hunk['start_line'] - 1)
        
        # Calculate expected indentation from the original lines
        expected_indent = None
        if hunk['old_lines']:
            expected_indent = len(hunk['old_lines'][0]) - len(hunk['old_lines'][0].lstrip())

        try:
            match_pos = find_best_match(
                context_to_match,
                stripped_lines,
                suggested_start,
                expected_indent=expected_indent,
                hunk_size=len(hunk['new_lines'])
            )


            if match_pos == -1:
                hunk_results.append({
                    'status': 'failed',
                    'reason': 'low_confidence',
                    'start_line': hunk['start_line']
                })
                continue

            actual_start = match_pos

            # Verify that we're not overlapping with previous changes
            if actual_start < last_end:
                actual_start = last_end
            
            # Calculate the exact range to replace
            old_lines_count = len(hunk['old_lines'])
            new_lines = hunk['new_lines']

            # Apply the changes
            new_lines_with_endings = [
                line if line.endswith('\n') else line + '\n'
                for line in new_lines
            ]

            # Update the file content
            # Only replace the exact number of lines we're changing
            if old_lines_count > 0:
                result_lines[actual_start:actual_start + old_lines_count] = new_lines_with_endings
            else:
                # For pure additions, insert at the position
                result_lines[actual_start:actual_start] = new_lines_with_endings
            last_end = actual_start + len(new_lines_with_endings)

            hunk_results.append({
                'status': 'success',
                'line': actual_start + 1
            })

        except ValueError as e:
            hunk_results.append({
                'status': 'failed',
                'reason': 'error',
                'error': str(e),
                'start_line': hunk['start_line']
            })
            continue

    # Write the changes back to the file
    success_count = sum(1 for result in hunk_results if result['status'] == 'success')
    total_hunks = len(hunk_results)

    try:
        with open(file_path, 'w', encoding='utf-8') as f:
            f.writelines(result_lines)

        status_message = (
            f"Applied {success_count}/{total_hunks} hunks successfully. "
            f"Details: {json.dumps(hunk_results, indent=2)}"
        )
        if total_hunks == 0:
            raise PatchApplicationError("No hunks to apply", {
                'status': 'error',
                'type': 'no_hunks',
                'summary': "No hunks found in diff"
            })
        elif success_count == 0:
            raise PatchApplicationError("Failed to apply any hunks", {
                'status': 'error',
                'type': 'complete_failure',
                'hunks': hunk_results,
                'summary': f"0/{total_hunks} hunks applied"
            })
            logger.warning(f"Partial success: {status_message}")
            raise PatchApplicationError("Partial success applying changes", {
                'status': 'partial',
                'type': 'partial_success',
                'hunks': hunk_results,
                'summary': f"{success_count}/{total_hunks} hunks applied"
            })
        elif success_count == total_hunks:
            logger.info(f"Complete success: {status_message}")
            return {'status': 'success', 'hunks': hunk_results, 'summary': f"{success_count}/{total_hunks} hunks applied"}
        else:
            # This shouldn't happen, but let's catch it just in case
            raise PatchApplicationError("Invalid hunk count", {
                'status': 'error',
                'type': 'invalid_count',
                'hunks': hunk_results,
                'summary': f"Success count {success_count} exceeds total hunks {total_hunks}"
            })

    except Exception as e:
        logger.error(f"Error writing changes to {file_path}: {str(e)}")
        raise

    # Clean up any .rej files
    rej_file = file_path + '.rej'
    if os.path.exists(rej_file):
        try:
            os.remove(rej_file)
            logger.info(f"Removed reject file: {rej_file}")
        except OSError as e:
            logger.warning(f"Could not remove reject file {rej_file}: {e}")

def use_git_to_apply_code_diff(git_diff: str) -> None:
    """
    Apply a git diff to the user's codebase.
    Main entry point for patch application.
    """
    logger.info("Starting diff application process...")
    logger.debug("Original diff content:")
    logger.debug(git_diff)

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

    if not file_path:
        raise ValueError("Could not determine target file path")

    # Handle new file creation
    if is_new_file_creation(diff_lines):
        create_new_file(git_diff, user_codebase_dir)
        return

    try:
        # Try system patch first
        patch_result = subprocess.run(
            ['patch', '-p1', '--forward', '--ignore-whitespace'],
            input=git_diff,
            cwd=user_codebase_dir,
            capture_output=True,
            text=True,
            timeout=10
        )

        if patch_result.returncode == 0:
            logger.info("System patch succeeded")
            return

        # If patch fails, try git apply
        logger.warning("System patch failed, trying git apply...")
        timestamp = int(time.time() * 1000)
        temp_file = os.path.join(user_codebase_dir, f'temp_{timestamp}.diff')

        try:
            with open(temp_file, 'w', newline='\n') as f:
                f.write(git_diff)

            git_result = subprocess.run(
                ['git', 'apply', '--verbose', '--ignore-whitespace',
                 '--ignore-space-change', '--whitespace=nowarn',
                 '--reject', temp_file],
                cwd=user_codebase_dir,
                capture_output=True,
                text=True
            )

            if git_result.returncode == 0:
                logger.info("Git apply succeeded")
                return

            # If both patch and git apply fail, try difflib
            logger.warning("Git apply failed, trying difflib...")
            try:
                apply_diff_with_difflib(file_path, git_diff)
            except PatchApplicationError as e:
                if e.details.get('status') == 'partial':
                    logger.warning(f"Partial success: {e.details.get('summary', '')}")
                    # Re-raise to let the endpoint handle the partial success
                    raise
                else:
                    logger.error(f"Failed to apply changes: {str(e)}")
                    raise
            logger.info("Difflib apply succeeded")
            return

        finally:
            if os.path.exists(temp_file):
                os.remove(temp_file)

    except Exception as e:
        logger.error(f"Error applying patch: {str(e)}")
        raise
