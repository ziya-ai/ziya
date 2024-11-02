import os
import subprocess
from app.utils.logging_utils import logger
import time
import re

HUNK_HEADER_REGEX = re.compile(r'^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@')

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

    # Create a temporary file with the diff content and a timestamp
    timestamp = int(time.time() * 1000)  # Get current timestamp in milliseconds
    temp_file = os.path.join(user_codebase_dir, f'temp_{timestamp}.diff')

    with open(temp_file, 'w', newline='\n') as f:
        f.write(git_diff)
        # need to add a newline at the end of the file of git apply will fail
        f.write("\n")
    logger.info(f"Created temporary diff file: {temp_file}")

    try:
        # Apply the changes using git apply
        cmd = ['git', 'apply', '--verbose', '--ignore-whitespace', '--ignore-space-change', temp_file]
        logger.info(f"Executing command: {' '.join(cmd)}")
        result = subprocess.run(cmd, capture_output=True, text=True, cwd=user_codebase_dir, check=True)

        logger.info(f"Git apply stdout: {result.stdout}")
        logger.error(f"Git apply stderr: {result.stderr}")
        logger.info(f"Git apply return code: {result.returncode}")

    except Exception as e:
        logger.error(f"Error applying changes: {str(e)}", exc_info=True)
        raise
    finally:
        os.remove(temp_file)

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
    # Split the diff into lines
    lines = git_diff.split('\n')

    # Check if this is a new file creation by looking for "new file mode" in the diff
    is_new_file = any('new file mode 100644' in line for line in lines[:5])
    original_content = []

    if not is_new_file:
        try:
            with open(original_file_path, 'r') as f:
                original_content = f.read().splitlines()
        except FileNotFoundError:
            error_msg = (
                f"File {original_file_path} not found and diff does not indicate new file creation. "
            )
            raise FileNotFoundError(error_msg)

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

def _find_correct_start_line(original_content: list, hunk_lines: list) -> int:
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
        # Creating a new file.
        return 1

    if len(hunk_lines) < 3:
        error_msg = (
            f"Invalid git diff format: Expected at least 2 lines in the hunk, but got {len(hunk_lines)} lines.\n"
            "Hunk content:\n{}".format('\n'.join(hunk_lines)))
        logger.error(error_msg)
        raise RuntimeError("git diff file is not valid.")

    context_and_deleted = []
    for line in hunk_lines:
        if line.startswith(' ') or line.startswith('-'):
            # Remove the prefix character
            context_and_deleted.append(line[1:])

    if not context_and_deleted:
        error_msg = (
            "Invalid git diff format: No context or deleted lines found in the hunk.\n"
            "Each hunk must contain at least one context line (starting with space) "
            "or deleted line (starting with '-').\n"
            "Hunk content:\n{}".format('\n'.join(hunk_lines)))
        raise RuntimeError(error_msg)

    # Search for the pattern in the original file
    pattern_length = len(context_and_deleted)
    for i in range(len(original_content) - pattern_length + 1):
        matches = True
        for j in range(pattern_length):
            if j >= len(context_and_deleted):
                break
            if i + j >= len(original_content) or original_content[i + j] != context_and_deleted[j]:
                matches = False
                break
        if matches:
            # Found the correct position git diff start with 1.
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
    start_line_old = _find_correct_start_line(original_content, hunk_lines)

    # Now process hunk_lines to calculate counts
    for hunk_line in hunk_lines:
        if hunk_line.startswith('+') and not hunk_line.startswith('+++'):
            actual_count_new += 1
        elif hunk_line.startswith('-') and not hunk_line.startswith('---'):
            actual_count_old += 1
        else:
            # Context line (unchanged line)
            actual_count_old += 1
            actual_count_new += 1

    # Adjust start_line_new considering previous line offsets
    corrected_start_line_new = start_line_old + cumulative_line_offset

    # Calculate line offset for subsequent hunks
    line_offset = actual_count_new - actual_count_old

    # Reconstruct the corrected hunk header
    corrected_hunk_header = _format_hunk_header(
        start_line_old, actual_count_old, corrected_start_line_new, actual_count_new
    )

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



if __name__ == '__main__':
    # TODO: Create unit test and move these code to unit test
    diff = """\
diff --git a/file.txt b/file.txt
index e69de29..4b825dc 100644
--- a/file.txt
+++ b/file.txt
@@ -1,5 +1,5 @@
 Line one
+Line two
 Line three
 Line four
 Line five
@@ -10,2 +10,2 @@
 Line ten
-Line eleven
+Line eleven modified"""
    print(correct_git_diff(diff,""))
