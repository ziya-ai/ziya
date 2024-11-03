def use_git_to_apply_code_diff(git_diff: str):
def correct_git_diff(git_diff: str, original_file_path: str) -> str:
        original_file_path (str): Path to the original file to calculate correct start_line_old
    is_new_file = False
    # Check if this is a new file creation
    if lines and lines[0].startswith('diff --git a/dev/null'):
        is_new_file = True
        # Check if 'new file mode 100644' is present in the first few lines
        has_file_mode = any('new file mode 100' in line for line in lines[:3])
        if not has_file_mode:
            # Insert the missing line after the first line
            mode_line = 'new file mode 100644'
            lines.insert(1, mode_line)
            logger.info(f"Added missing '{mode_line}' to new file diff")

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
            corrected_hunk_header, hunk_lines, line_index, line_offset = _process_hunk_with_original_content(
                lines, line_index, cumulative_line_offset, original_content
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
        # Creating a new file, should start with @@ -0,0 +1,N @@
        return 0

    if len(hunk_lines) < 3:
        error_msg = (
            f"Invalid git diff format: Expected at least 2 lines in the hunk, but got {len(hunk_lines)} lines.\n"
            + "Hunk content:\n{}".format('\n'.join(hunk_lines)))
        logger.error(error_msg)
        raise RuntimeError("Invalid git diff format.")

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
        original_content (list): List of lines from the original file.
    # Find the correct start_line_old by matching context and deleted lines
    start_line_old = _find_correct_old_start_line(original_content, hunk_lines)

    # Calculate counts for the hunk lines
    # Special handling for new file creation
    if start_line_old == 0:
        # For new files:
        # count_old should be 0
        actual_count_old = 0
        corrected_start_line_new = 1
    else:
        # For existing files, adjust start_line_new considering previous line offsets
        corrected_start_line_new = start_line_old + cumulative_line_offset