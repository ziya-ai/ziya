import os
import subprocess
import json
from app.utils.logging_utils import logger
from typing import List, Dict, Any
import time
import re

HUNK_HEADER_REGEX = re.compile(r'^@@[ ]?([+-]?\d+)(?:,(\d+))?[ ]?([+-]?\d+)(?:,(\d+))?[ ]?@@')

def sanitize_diff(diff: str) -> str:
    """Sanitize diff content to handle common issues"""
    # Remove any trailing whitespace from lines
    lines = [line.rstrip() for line in diff.splitlines()]

    # Ensure proper line endings
    diff = '\n'.join(lines)

    # Ensure diff ends with newline
    if not diff.endswith('\n'):
        diff += '\n'

    return diff

def inspect_line_content(file_path: str, line_number: int, context: int = 5) -> Dict[str, Any]:
    """Inspect the content around a specific line number, including hex dump"""
    try:
        with open(file_path, 'r') as f:
            lines = f.readlines()

        start = max(0, line_number - context - 1)
        end = min(len(lines), line_number + context)

        return {
            'lines': {
                i+1: {'content': lines[i], 'hex': ' '.join(f'{ord(c):02x}' for c in lines[i])}
                for i in range(start, end)
            },
            'line_endings': diagnose_line_endings(''.join(lines[start:end]))
        }
    except Exception as e:
        logger.error(f"Error inspecting line content: {e}")
        return {'error': str(e)}

def diagnose_line_endings(content: str) -> Dict[str, Any]:
    """Analyze line endings and whitespace in content"""
    lines = content.splitlines(keepends=True)
    return {
        'total_lines': len(lines),
        'endings': {
            'CRLF \\r\\n': sum(1 for line in lines if line.endswith('\r\n')),
            'LF \\n': sum(1 for line in lines if line.endswith('\n') and not line.endswith('\r\n')),
            'CR \\r': sum(1 for line in lines if line.endswith('\r') and not line.endswith('\r\n')),
            'no_ending': sum(1 for line in lines if not line.endswith('\n') and not line.endswith('\r')),
        }
    }

def analyze_diff_failure(diff: str, file_path: str, error_output: str) -> Dict[str, Any]:
    """
    Analyze why a diff failed to apply and provide diagnostic information.
    """
    def extract_context_from_error(error_text: str) -> List[str]:
        """Extract the context lines from git's error message"""
        start = error_text.find('while searching for:')
        if start == -1:
            return []

        # Find the end of the context (either 'error:' or end of string)
        end = error_text.find('error:', start)
        if end == -1:
            end = len(error_text)

        context = error_text[start + len('while searching for:'):end].strip()
        return [line.strip() for line in context.split('\n') if line.strip()]

    try:
        # Remove line number if present in file_path
        clean_path = file_path.split(':')[0] if file_path else None
        file_content = open(clean_path, 'r').read() if clean_path else ""

        # Get the problematic hunk from the error output
        hunk_match = re.search(r'@@ [^@]+ @@.*?(?=@@|\Z)', error_output, re.DOTALL)
        if not hunk_match:
            return {'error': 'Could not identify failing hunk'}

        failing_hunk = hunk_match.group(0)
        context_lines = [line[1:] for line in failing_hunk.split('\n') if line.startswith(' ')]

        # Analyze line endings
        diff_endings = diagnose_line_endings(diff)
        file_endings = diagnose_line_endings(file_content)

        # Extract context lines from error output
        context_lines = extract_context_from_error(error_output)

        if not context_lines:
            return {
                "error": "Could not extract context lines from error output",
                "error_output": error_output,
                "diff_endings": diff_endings,
                "file_endings": file_endings
            }

        # Find these lines in the actual file
        file_lines = file_content.split('\n')
        context_found = False
        context_location = None
        closest_match = None
        closest_match_line = None

        for i in range(len(file_lines)):
            # Look for exact matches first
            if i + len(context_lines) <= len(file_lines) and all(
                file_lines[i+j].strip() == context_lines[j].strip()
                for j in range(len(context_lines))
            ):
                context_found = True
                context_location = i + 1
                break

        return {
            'context_lines': context_lines,
            'context_found': context_found,
            'expected_location': error_output.split('patch failed:')[1].split(':')[1].strip() if 'patch failed:' in error_output else None,
            'actual_location': context_location,
            'diff_line_endings': diff_endings,
            'file_excerpt': {
                'expected_location': file_lines[int(error_output.split(':')[1].split()[0])-3:int(error_output.split(':')[1].split()[0])+3] if 'patch failed:' in error_output else [],
                'actual_location': file_lines[context_location-3:context_location+3] if context_location else [],
                'first_lines': file_lines[:5],
            },
            'file_line_endings': file_endings,
            'error_output': error_output
        }

    except Exception as e:
        logger.error(f"Error analyzing diff: {str(e)}")
        return {'error': str(e)}

def use_git_to_apply_code_diff(git_diff: str):
    """
    Apply a git diff to the user's codebase.

    This function takes a git diff as a string and applies it to the user's codebase
    specified by the ZIYA_USER_CODEBASE_DIR environment variable. It creates a temporary
    file with the diff content and uses the 'git apply' command to apply the changes.

    Args:
        git_diff (str): A string containing the git diff to be applied.

    Note:
        This function assumes that the git command-line tool is available in the system path.
        It uses the --ignore-whitespace and --ignore-space-change options with 'git apply'
        to handle potential whitespace issues.
    """

    user_codebase_dir = os.environ.get("ZIYA_USER_CODEBASE_DIR")
    if not user_codebase_dir:
        raise ValueError("ZIYA_USER_CODEBASE_DIR environment variable is not set")

    # Sanitize the diff content
    git_diff = sanitize_diff(git_diff)

    def create_new_file(diff_content: str) -> None:
        """Create a new file from a git diff"""
        # Extract the file path
        file_path = diff_content.split('diff --git a/dev/null b/')[1].split('\n')[0].strip()
        full_path = os.path.join(user_codebase_dir, file_path)
        
        # Create directory if it doesn't exist
        os.makedirs(os.path.dirname(full_path), exist_ok=True)
        
        # Extract content after the hunk header
        lines = diff_content.split('\n')
        content_start = next(i for i, line in enumerate(lines) if line.startswith('@@ '))
        content_lines = []
        
        # Process lines after the hunk header
        for line in lines[content_start + 1:]:
            if line.startswith('+'):
                content_lines.append(line[1:])  # Remove the leading +
        
        # Write the file
        with open(full_path, 'w', newline='\n') as f:
            f.write('\n'.join(content_lines))
        
        logger.info(f"Successfully created new file: {file_path}")
 
    # Check if this is a new file creation diff
    if git_diff.startswith('diff --git a/dev/null b/'):
        create_new_file(git_diff)
        return

    # Clean the diff content - stop at first triple backtick
    def clean_diff_content(content: str) -> str:
        # Stop at triple backtick if present
        end_marker = content.find('```')
        if end_marker != -1:
            content = content[:end_marker]
        return content.strip()

    git_diff = clean_diff_content(git_diff)

    # Create timestamp once for both reject and actual diff files
    timestamp = int(time.time() * 1000)  # Get current timestamp in milliseconds

    # Try to apply with --reject first to get more information about failures
    try:
        temp_reject = os.path.join(user_codebase_dir, f'temp_{timestamp}.diff.reject')
        subprocess.run(
            ['git', 'apply', '--reject', '--verbose', temp_reject],
            input=git_diff.encode(),
            cwd=user_codebase_dir,
            capture_output=True,
            check=False
        )

        # If reject file exists, read it for better error reporting
        if os.path.exists(temp_reject):
            with open(temp_reject, 'r') as f:
                reject_content = f.read()
                logger.error(f"Diff application failed. Reject content:\n{reject_content}")
            os.remove(temp_reject)
    except Exception as e:
        logger.debug(f"Reject test failed: {str(e)}")

    temp_file = os.path.join(user_codebase_dir, f'temp_{timestamp}.diff')

    with open(temp_file, 'w', newline='\n') as f:
        f.write(git_diff)
        # need to add a newline at the end of the file of git apply will fail
        f.write("\n")
    logger.info(f"Created temporary diff file: {temp_file}")

    try:
        # Apply the changes using git apply
        cmd = ['git', 'apply', '--verbose', '--ignore-whitespace', '--ignore-space-change', temp_file]
        logger.info(f"Executing command in {user_codebase_dir}: {' '.join(cmd)}")
        
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, cwd=user_codebase_dir, check=True)
            logger.info(f"Git apply stdout: {result.stdout}")
            logger.info(f"Git apply stderr: {result.stderr}")
            logger.info(f"Git apply return code: {result.returncode}")
        except subprocess.CalledProcessError as e:
            logger.error(f"Git apply failed with return code {e.returncode}")
            logger.error(f"Git apply stdout: {e.stdout}")
            logger.error(f"Git apply stderr: {e.stderr}")

            # Extract file path from error output
            file_path = None
            if 'patch failed:' in e.stderr:
                failed_path = e.stderr.split('patch failed: ')[1].split('\n')[0]
                # Remove line number if present
                failed_path = failed_path.split(':')[0]
                file_path = os.path.join(user_codebase_dir, failed_path)

                # Get line number and inspect content
                line_match = re.search(r'patch failed: .*:(\d+)', e.stderr)
                if line_match:
                    line_number = int(line_match.group(1))
                    line_inspection = inspect_line_content(file_path, line_number)
                    logger.error(f"Content around line {line_number}:\n{json.dumps(line_inspection, indent=2)}")

            logger.info(f"Analyzing failure for file: {file_path}")
            analysis = analyze_diff_failure(git_diff, file_path, e.stderr)
            logger.error("Diff application analysis:")
            logger.error(json.dumps(analysis, indent=2))

            # Log the actual file content around the problem area
            if 'actual_location' in analysis and analysis['actual_location']:
                logger.error(f"Content at actual location {analysis['actual_location']}:")
                for i, line in enumerate(analysis['file_excerpt']['actual_location']):
                    logger.error(f"{analysis['actual_location'] + i - 3}: {line}")

            raise RuntimeError(
                f"Failed to apply diff. Analysis: {json.dumps(analysis, indent=2)}"
            )
            
    except Exception as e:
        logger.error(f"Error applying changes: {str(e)}", exc_info=True)
        raise
    finally:
        os.remove(temp_file)

def normalize_new_file_diff(diff: str) -> str:
    """Normalize a git diff for new file creation to ensure consistent format"""
    if diff.startswith('diff --git'):
        # Check if this appears to be a new file creation diff
        lines = [line.rstrip('\n') for line in diff.split('\n')]
        first_line = lines[0]

        # Extract file path from the diff --git line
        match = re.match(r'diff --git (?:a/)?(\S+) (?:b/)?(\S+)', first_line)
        if not match:
            return diff

        file_path = match.group(2)

        # Check if this is a new file creation
        has_new_file_marker = any('new file' in line for line in lines[:3])
        has_only_additions = all(line.startswith('+') or not line.strip()
                               for line in lines[3:]
                               if line.strip() and not line.startswith('diff'))

        if has_new_file_marker or has_only_additions:
            # Find where the actual content starts (after headers)
            content_start = 3
            for i, line in enumerate(lines[3:], start=3):
                if line.startswith('+++') or line.startswith('---'):
                    content_start = i + 1
                    continue
                if line.startswith('@@ '):
                    content_start = i + 1
                    break

            content_lines = lines[content_start:]
            normalized_diff = [
                f'diff --git a/dev/null b/{file_path}',
                'new file mode 100644',
                '--- /dev/null',
                f'+++ b/{file_path}',
                f'@@ -0,0 +1,{len(content_lines)} @@'
            ]
            normalized_diff.extend(content_lines)
            return '\n'.join(normalized_diff)

    return diff

def is_new_file_creation(diff_lines: list) -> bool:
    """
    Determine if a diff represents a new file creation.
    """
    if not diff_lines:
        return False

    # Check for standard new file markers
    has_dev_null = any('diff --git a/dev/null' in line or 
                      'diff --git /dev/null' in line for line in diff_lines[:3])
    has_new_file_mode = any('new file mode' in line for line in diff_lines)
    has_null_marker = '--- /dev/null' in diff_lines

    # Also check for the pattern that indicates a new file
    is_new_file_pattern = (
        diff_lines[0].startswith('diff --git') and
        any('new file mode' in line for line in diff_lines) and
        any(line.startswith('--- /dev/null') for line in diff_lines) and
        any('+++ b/' in line for line in diff_lines)
    )

    return bool(has_dev_null or has_new_file_mode or has_null_marker or is_new_file_pattern)

def correct_git_diff(git_diff: str, original_file_path: str) -> str:
    """
    Corrects the hunk headers in a git diff string by recalculating the line counts and
    adjusting the starting line numbers in the new file, considering the cumulative effect of
    previous hunks.

    The function assumes that the `start_line_old` values in the hunk headers are correct,
    but the line counts and `start_line_new` may be incorrect due to manual edits or errors.

    Parameters:
        git_diff (str): The git diff string to be corrected. It may contain multiple hunks
        original_file_path (str): Path to the original file to calculate correct start_line_old
                        and incorrect hunk headers.

    Returns:
        str: The git diff string with corrected hunk headers.

    The function works by parsing the diff, recalculating the line counts for each hunk,
    adjusting the `start_line_new` values based on the cumulative changes from previous hunks,
    and reconstructing the diff with corrected hunk headers.

    Notes:
        - The diff is expected to be in unified diff format.
        - Lines starting with '+++', '---', or other file headers are not treated as additions or deletions.
        - Context lines are lines that do not start with '+', '-', or '@@'.
        - All lines in the hunk (including empty lines) are considered in the counts.

    """

    # Split into lines first for analysis
    lines = git_diff.split('\n')
    logger.info(f"Starting diff processing. First line: {lines[0] if lines else 'empty diff'}")

    # Check if this is a new file creation BEFORE trying to open the original file
    is_new_file = is_new_file_creation(lines)
    if is_new_file:
        logger.info(f"Detected new file creation for {original_file_path}")
        return git_diff

    # normalize the diff format
    git_diff = normalize_new_file_diff(git_diff)
    logger.info(f"Processing diff for {original_file_path}")

    # If not a new file, proceed with normal diff correction

    try:
        if not os.path.exists(original_file_path):
            if is_new_file:
                logger.info(f"Confirmed new file creation after FileNotFoundError for {original_file_path}")
                return git_diff
            else:
                error_msg = f"File {original_file_path} not found and diff does not indicate new file creation."
                raise FileNotFoundError(error_msg)
            original_content = []
        else:
            with open(original_file_path, 'r') as f:
                original_content = f.read().splitlines()
        corrected_lines = []
        line_index = 0
        # Keep track of the cumulative line number adjustments
        cumulative_line_offset = 0
 
        while line_index < len(lines):
            line = lines[line_index]
            hunk_match = HUNK_HEADER_REGEX.match(line)
 
            if hunk_match:
                # Process the hunk
                corrected_hunk_header, hunk_lines, line_index, line_offset = _process_hunk_with_original_content(
                    lines, line_index, cumulative_line_offset, original_content
                )
 
                # Update cumulative_line_offset
                cumulative_line_offset += line_offset
 
                # Append corrected hunk header and hunk lines
                corrected_lines.append(corrected_hunk_header)
                corrected_lines.extend(hunk_lines)
            else:
                # For non-hunk header lines, just append them
                corrected_lines.append(line)
                line_index += 1
 
        # Join the corrected lines back into a string
        corrected_diff = '\n'.join(corrected_lines)
        return corrected_diff
 
    except Exception as e:
        logger.error(f"Error processing diff: {str(e)}", exc_info=True)
        raise

    # Join the corrected lines back into a string
    corrected_diff = '\n'.join(corrected_lines)
    return corrected_diff

def normalize_line_endings(content: str) -> str:
    """Normalize line endings to \n"""
    # First convert all \r\n to \n
    content = content.replace('\r\n', '\n')
    # Then convert any remaining \r to \n
    return content.replace('\r', '\n')

def clean_whitespace(line: str) -> str:
    """Clean up whitespace while preserving indentation"""
    # Preserve leading whitespace
    leading_space = len(line) - len(line.lstrip())
    # Remove trailing whitespace and normalize internal whitespace
    cleaned = ' '.join(line.rstrip().split())
    # Restore leading whitespace
    return ' ' * leading_space + cleaned

def prepare_content_for_comparison(content: str) -> List[str]:
    """Prepare content for comparison by normalizing line endings and whitespace"""
    # First normalize line endings
    content = content.replace('\r\n', '\n')
    content = content.replace('\r', '\n')

    # Split into lines and clean each line
    lines = content.split('\n')
    cleaned_lines = [clean_whitespace(line) for line in lines]

    # Filter out empty lines that were only whitespace
    return [line for line in cleaned_lines if line or line == '']

def _find_correct_old_start_line(original_content: list, hunk_lines: list) -> int:
    """
    Finds the correct starting line number in the original file by matching context and deleted lines.

    Parameters:
        original_content (list): List of lines from the original file
        hunk_lines (list): List of lines in the current hunk

    Returns:
        int: The correct 1-based line number where the hunk should start in the original file

    The function works by:
    1. Extracting context and deleted lines from the hunk (ignoring added lines)
    2. Creating a pattern from these lines
    3. Finding where this pattern matches in the original file
    4. Converting the matching position to a 1-based line number
    """
    # Extract context and deleted lines from the hunk
    if not original_content:
        logger.debug("No original content provided")
        # Creating a new file or pure addition, should start with @@ -0,0 +1,N @@ or @@ +0,0 +1,N @@
        return 0

    if len(hunk_lines) < 3:
        error_msg = (
            f"Invalid git diff format: Expected at least 2 lines in the hunk, but got {len(hunk_lines)} lines.\n"
            + "Hunk content:\n{}".format('\n'.join(hunk_lines)))
        logger.error(error_msg)
        raise RuntimeError("Invalid git diff format.")

    def normalize_whitespace(text: str) -> str:
        """
        Normalize whitespace while preserving indentation and handling empty lines
        """
        if not text or text.isspace():
            return ""
        indent = len(text) - len(text.lstrip())
        normalized = ' '.join(text.strip().split())
        return ' ' * indent + normalized
 
    def lines_match(line1: str, line2: str) -> bool:
        """Compare two lines ignoring whitespace differences"""
        # Remove all whitespace for core comparison
        if not line1 and not line2:  # Both empty or whitespace-only
            return True
        if not line1 or not line2:  # One empty, one not
            return False

        clean1 = ''.join(line1.split())
        clean2 = ''.join(line2.split())

        return clean1 == clean2

    logger.info(f"Processing hunk with {len(hunk_lines)} lines")

    # Extract target line from hunk header
    hunk_header = next((line for line in hunk_lines if line.startswith('@@ ')), None)
    if hunk_header:
        logger.debug(f"Hunk header: {repr(hunk_header)}")
        match = HUNK_HEADER_REGEX.match(hunk_header)
        if match:
            target_line = int(match.group(1))
            context = original_content[target_line-1:target_line+2]
            logger.debug(f"Target line {target_line}, context: {'; '.join(repr(line)[:60] for line in context)}")

    def extract_context_lines(hunk_lines: list) -> list:
        context_lines = []
        processed = 0
        context_count = 0
        for line in hunk_lines:
            if line.startswith('@@'): continue
            # Lines starting with ' ' are unchanged context lines
            # Lines starting with '-' are deleted lines (part of original context)
            # Lines starting with '+' are new additions (not part of original context)
            if line.startswith(' ') or line.startswith('-'):
                cleaned_line = normalize_whitespace(line[1:])
                if cleaned_line:  
                    context_count += 1
                    context_lines.append(cleaned_line)
        
        logger.debug(f"Found {context_count} context lines, first 3: {'; '.join(repr(line)[:60] for line in context_lines[:3])}")
        return context_lines

    context_and_deleted = [line for line in extract_context_lines(hunk_lines) if line.strip()]
    if not context_and_deleted:
        logger.error(f"No context lines in hunk of {len(hunk_lines)} lines. First 3 lines: {'; '.join(repr(line)[:60] for line in hunk_lines[:3])}")
        raise ValueError(
            "No context lines found in hunk. Each hunk must contain at least one context line.\n"
            f"Hunk content:\n{chr(10).join(hunk_lines)}"
        )

    # Extract target line from hunk header for smarter searching
    target_line = None
    if hunk_header:
        match = HUNK_HEADER_REGEX.match(hunk_header)
        target_line = int(match.group(1)) if match else None

    # Log the first few context lines we're looking for
    logger.info("First few context lines we're looking for:")
    for line in context_and_deleted[:3]:
        logger.info(f"  {repr(line)}")

    # Clean up the original content for comparison
    original_content = [normalize_whitespace(line) for line in original_content]

    logger.info(f"Searching for {len(context_and_deleted)} context lines in {len(original_content)} line file")

    if not context_and_deleted:
        error_msg = (
            "Invalid git diff format: No context or deleted lines found in the hunk.\n"
            "Each hunk must contain at least one context line (starting with space) "
            "or deleted line (starting with '-').\n"
            "Hunk content:\n{}".format('\n'.join(hunk_lines)))
        raise RuntimeError(error_msg)

    # Search for the pattern in the original file
    search_start, search_end = 0, len(original_content)

    pattern_length = len(context_and_deleted)
    for i in range(search_start, search_end - pattern_length + 1):

        def try_match_at_position(pos: int) -> bool:
            try:
                # Get the lines we're comparing
                orig_lines = [
                    original_content[pos + j] if pos + j < len(original_content) else ""
                    for j in range(pattern_length)
                ]
                
                # Log the comparison attempt
                logger.debug(f"Attempting match at position {pos}:")
                for j in range(min(3, pattern_length)):  # Log first 3 lines
                    logger.debug(f"  Original[{pos + j}]: {repr(orig_lines[j])}")
                    logger.debug(f"  Context[{j}]: {repr(context_and_deleted[j])}")
                
                # Try the match
                return all(
                    lines_match(orig, ctx)
                    for orig, ctx in zip(orig_lines, context_and_deleted)
                )
            except Exception as e:
                logger.error(f"Error comparing at position {pos}: {str(e)}")
                return False
 
        # Try exact match first
        matches = try_match_at_position(i)

        if matches:
            logger.info(f"Found exact match at line {i + 1}")
            return i + 1
 
        # If exact match fails, try fuzzy matching
        matches = all(
            _fuzzy_line_match(
                original_content[i + j] if i + j < len(original_content) else "",
                context_and_deleted[j] if j < len(context_and_deleted) else ""
            )
            for j in range(pattern_length)
        )

        # If we found a match and have a target line, prefer matches closer to the target
        if matches and target_line is not None:
            if abs(i + 1 - target_line) <= 10:  # Within 10 lines of target
                return i + 1

        if matches:
            logger.info(f"Found fuzzy match at line {i + 1}")
            return i + 1

    joined_context_and_deleted = '\n'.join(context_and_deleted)
    error_msg = (
        "Failed to locate the hunk position in the original file.\n"
        "This usually happens when the context lines in the diff don't match the original file content.\n"
        f"Context and deleted lines being searched:\n{joined_context_and_deleted}\n"
        "Please ensure the diff is generated against the correct version of the file."
    )
    logger.error(error_msg)
    raise RuntimeError(error_msg)

def _fuzzy_line_match(line1: str, line2: str, threshold: float = 0.8) -> bool:
    """
    Compare two lines with fuzzy matching to handle minor whitespace differences
    """
    if not line1 and not line2:  # Both empty
        return True
    if not line1 or not line2:  # One empty, one not
        return False

    # First try exact match after whitespace normalization
    norm1 = ' '.join(line1.split())
    norm2 = ' '.join(line2.split())
    if norm1 == norm2:
        return True

    # Then try without any whitespace
    clean1 = ''.join(line1.split())
    clean2 = ''.join(line2.split())
    if clean1 == clean2:
        return True

    # Calculate similarity ratio
    from difflib import SequenceMatcher
    # Use normalized strings for better fuzzy matching
    ratio = SequenceMatcher(None, norm1, norm2).ratio()
    return ratio >= threshold

def _process_hunk_with_original_content(lines: list, start_index: int, cumulative_line_offset: int, original_content: list):
    """
    Processes a single hunk starting at start_index in lines, recalculates the line counts,
    and returns the corrected hunk header, hunk lines, and the updated index after the hunk.

    Parameters:
        lines (list): The list of lines from the diff.
        start_index (int): The index in lines where the hunk header is located.
        cumulative_line_offset (int): The cumulative line offset from previous hunks.
        original_content (list): List of lines from the original file.

    Returns:
        tuple:
            - corrected_hunk_header (str): The corrected hunk header.
            - hunk_lines (list): The list of lines in the hunk (excluding the hunk header).
            - end_index (int): The index in lines after the hunk.
            - line_offset (int): The line offset caused by this hunk (to adjust future hunks).

    The function reads the hunk lines, counts the number of lines in the original and new files,
    and adjusts the starting line number in the new file based on cumulative changes.
    """

    line_index = start_index

    # Initialize counts for recalculation
    actual_count_old = 0
    actual_count_new = 0

    # Move to the next line after the hunk header
    line_index += 1

    hunk_lines = []

    # Collect hunk lines until the next hunk header or end of diff
    while line_index < len(lines):
        hunk_line = lines[line_index]
        if HUNK_HEADER_REGEX.match(hunk_line):
            break
        else:
            hunk_lines.append(hunk_line)
            line_index += 1

    # Find the correct start_line_old by matching context and deleted lines
    start_line_old = _find_correct_old_start_line(original_content, hunk_lines)

    # Calculate counts for the hunk lines
    for hunk_line in hunk_lines:
        if hunk_line.startswith('+') and not hunk_line.startswith('+++'):
            actual_count_new += 1
        elif hunk_line.startswith('-') and not hunk_line.startswith('---'):
            actual_count_old += 1
        else:
            # Context line (unchanged line)
            actual_count_old += 1
            actual_count_new += 1

    # Special handling for new file creation
    if start_line_old == 0:
        # For new files:
        # count_old should be 0
        actual_count_old = 0
        corrected_start_line_new = 1
    else:
        # For existing files, adjust start_line_new considering previous line offsets
        corrected_start_line_new = start_line_old + cumulative_line_offset

    # Calculate line offset for subsequent hunks
    line_offset = actual_count_new - actual_count_old

    # Reconstruct the corrected hunk header
    corrected_hunk_header = _format_hunk_header(
        start_line_old, actual_count_old, corrected_start_line_new, actual_count_new
    )


    # Special handling for pure additions (when old count is 0)
    if actual_count_old == 0:
        # Use the same format as the original if it started with +
        original_header = lines[start_index]
        if original_header.strip().startswith('@@ +'):
            corrected_hunk_header = f"@@ +{start_line_old},0 +{corrected_start_line_new},{actual_count_new} @@"

    return corrected_hunk_header, hunk_lines, line_index, line_offset

def _format_hunk_header(start_old: int, count_old: int, start_new: int, count_new: int) -> str:
    """
    Formats the hunk header according to git diff syntax, omitting counts when they are 1.

    Parameters:
        start_old (int): Starting line number in the original file.
        count_old (int): Number of lines in the hunk in the original file.
        start_new (int): Starting line number in the new file.
        count_new (int): Number of lines in the hunk in the new file.

    Returns:
        str: The formatted hunk header.

    The hunk header format is:
        @@ -start_old[,count_old] +start_new[,count_new] @@

    If count_old or count_new is 1, the count is omitted.
    """
    # Omit counts when they are equal to 1
    old_part = f'-{start_old}'
    if count_old != 1:
        old_part += f',{count_old}'
    new_part = f'+{start_new}'
    if count_new != 1:
        new_part += f',{count_new}'
    return f'@@ {old_part} {new_part} @@'
