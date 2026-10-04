"""
Utilities for file operations related to diffs and patches.
"""

import os
import re
import glob
from contextlib import contextmanager
from typing import List

from app.utils.logging_utils import logger


def detect_line_ending(file_path: str) -> str:
    """Return a file's dominant line ending, defaulting to LF."""
    try:
        with open(file_path, 'rb') as f:
            data = f.read()
    except OSError:
        return '\n'
    return '\r\n' if data.count(b'\r\n') * 2 > data.count(b'\n') else '\n'


def write_preserving_line_endings(file_path: str, content: str) -> None:
    """
    Write text to an existing file using that file's dominant line ending.

    The pipeline reads files with universal newlines, so content arrives
    LF-only. A plain text-mode write emits os.linesep, which rewrote every line
    of LF files on Windows (and of CRLF files on POSIX).
    """
    newline = detect_line_ending(file_path)
    with open(file_path, 'w', encoding='utf-8', newline=newline) as f:
        f.write(content.replace('\r\n', '\n'))


@contextmanager
def preserved_line_endings(file_path: str):
    """
    Keep a file's dominant line ending when an external tool modifies it.

    With core.autocrlf=true (the Git for Windows default), git apply writes the
    lines it adds to an LF file as CRLF, leaving mixed line endings.
    """
    try:
        with open(file_path, 'rb') as f:
            before = f.read()
    except OSError:
        yield
        return
    crlf = before.count(b'\r\n') * 2 > before.count(b'\n')
    yield
    try:
        with open(file_path, 'rb') as f:
            after = f.read()
    except OSError:
        return
    if after == before:
        return
    normalized = after.replace(b'\r\n', b'\n')
    if crlf:
        normalized = normalized.replace(b'\n', b'\r\n')
    if normalized != after:
        with open(file_path, 'wb') as f:
            f.write(normalized)


def delete_file(git_diff: str, base_dir: str) -> str:
    """
    Delete a file indicated by a deletion diff.

    Returns the relative path of the deleted file.

    Args:
        git_diff: The git diff content (must be a deletion diff)
        base_dir: The base directory of the project

    Raises:
        FileNotFoundError: If the file does not exist
        ValueError: If the file path cannot be determined
    """
    from ..parsing.diff_parser import extract_target_file_from_diff

    rel_path = extract_target_file_from_diff(git_diff)
    if not rel_path:
        raise ValueError("Could not determine file path from deletion diff")

    full_path = os.path.join(base_dir, rel_path)
    if not os.path.isabs(full_path):
        full_path = os.path.abspath(full_path)

    if not os.path.exists(full_path):
        raise FileNotFoundError(f"File does not exist: {full_path}")

    logger.info(f"Deleting file: {full_path}")
    os.remove(full_path)
    logger.info(f"Successfully deleted file: {rel_path}")

    return rel_path


def create_new_file(git_diff: str, base_dir: str) -> None:
    """
    Create a new file from a git diff.
    
    Args:
        git_diff: The git diff content
        base_dir: The base directory where the file should be created
    """
    logger.info(f"Processing new file diff with length: {len(git_diff)} bytes")

    logger.debug("Full diff content:")
    logger.debug(git_diff)

    try:
        # Parse the diff content
        diff_lines = git_diff.splitlines()

        # Find the file path line
        file_path = None
        for line in diff_lines:
            if line.startswith('diff --git'):
                # Handle both "a/path b/path" and "path path" formats
                if ' b/' in line:
                    file_path = line.split(' b/')[-1]
                else:
                    # Extract second path from "diff --git path1 path2"
                    parts = line.split()
                    if len(parts) >= 4:
                        file_path = parts[-1]
                break
            elif line.startswith('+++ b/'):
                file_path = line[6:]  # Remove the '+++ b/' prefix
                break
            elif line.startswith('+++ ') and not line.startswith('+++ /dev/null'):
                file_path = line[4:].strip()
                break
                
        # Make sure we found a file path
        if file_path is None:
            raise ValueError("Could not extract target file path from diff")

        # Honor an author-specified absolute target: git strips the leading
        # slash into the b/ prefix, so restore it before the join below.
        # os.path.join(base_dir, "/abs/path") returns "/abs/path" unchanged,
        # so a restored absolute path is created where it was asked for
        # instead of being nested under base_dir (the bug that silently wrote
        # <project>/Users/.../file.py). A relative path is unaffected.
        from ..parsing.diff_parser import restore_leading_slash
        file_path = restore_leading_slash(file_path)

        # Extract the file path from the diff --git line
        full_path = os.path.join(base_dir, file_path)
        # Containment check (CWE-22/94): file_path came verbatim from a diff
        # header the caller does not otherwise validate at this depth — a
        # "+++ b/../../../etc/cron.d/x" header reaches this, the true write
        # sink, from three different upstream extraction sites. Kept as an
        # inline check (no import from app.routes/app.services) to preserve
        # this module's layering.
        _resolved_base = os.path.abspath(base_dir)
        _resolved_full = os.path.abspath(full_path)
        if not (_resolved_full == _resolved_base
                or _resolved_full.startswith(_resolved_base + os.sep)):
            raise ValueError(f"Path traversal detected: diff target '{file_path}' escapes base directory")
        logger.debug(f"Creating file at path: {file_path}")

        # Create directory if it doesn't exist
        os.makedirs(os.path.dirname(full_path), exist_ok=True)

        # Extract the content (everything after the @@ line)
        content_lines = []

        # Parse hunk header to get expected line count
        hunk_header_pattern = re.compile(r'^@@ -\d+(?:,\d+)? \+\d+,(\d+) @@')
        expected_lines = 0
        for line in diff_lines:
            match = hunk_header_pattern.match(line)
            if match:
                expected_lines = int(match.group(1))
                logger.debug(f"Found hunk header, expecting {expected_lines} lines of content")
                continue
            # Skip header lines
            if line.startswith(('diff --git', 'new file mode', '--- ', '+++ ')):
                logger.info(f"Skipping header line: {line}")
                continue
                
            # Process content lines
            if line.startswith('+'):
                logger.info(f"Adding content line: {line}")
                content_lines.append(line[1:])
            else:
                logger.info(f"Skipping non-plus line: {line}")
        # Write the content
        logger.debug(f"Extracted {len(content_lines)} content lines")
        logger.debug(f"Expected {expected_lines} lines")
        logger.debug("First 10 content lines:")
        logger.debug('\n'.join(content_lines[:10]))
        content = '\n'.join(content_lines)
        # LF on every platform, rather than os.linesep
        with open(full_path, 'w', encoding='utf-8', newline='\n') as f:
            f.write(content)
            if not content.endswith('\n'):
                f.write('\n')

        # Verify we got all expected lines
        if len(content_lines) != expected_lines:
            logger.warning(f"Line count mismatch: got {len(content_lines)}, "
                         f"expected {expected_lines}")

        logger.info(f"Successfully created new file: {file_path}")
    except Exception as e:
        logger.error(f"Error creating new file: {str(e)}, diff content: {git_diff[:200]}")
        raise

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

        # Find and remove .rej and .orig files in target directory
        for pattern in ['*.rej', '*.orig']:
            for artifact in glob.glob(os.path.join(file_dir, pattern)):
                logger.info(f"Removing patch artifact: {artifact}")
                os.remove(artifact)
        
        # Also clean up .rej files from current working directory
        # (patch command may create them there if run from wrong directory)
        from app.context import get_project_root
        cwd = get_project_root()
        for pattern in ['*.rej', 'Oops.rej']:
            for artifact in glob.glob(os.path.join(cwd, pattern)):
                logger.info(f"Removing patch artifact from cwd: {artifact}")
                os.remove(artifact)
                
    except Exception as e:
        logger.warning(f"Error cleaning up patch artifacts: {str(e)}")

def cleanup_workspace_artifacts() -> None:
    """
    Clean up .rej files from current working directory that may be left by patch commands.
    """
    try:
        from app.context import get_project_root
        cwd = get_project_root()
        for pattern in ['*.rej', 'Oops.rej']:
            for artifact in glob.glob(os.path.join(cwd, pattern)):
                logger.info(f"Removing workspace patch artifact: {artifact}")
                os.remove(artifact)
    except Exception as e:
        logger.warning(f"Error cleaning up workspace artifacts: {str(e)}")

def remove_reject_file_if_exists(file_path: str):
    """
    Remove .rej file if it exists, to clean up after partial patch attempts.
    
    Args:
        file_path: Path to the file that was patched
    """
    rej_file = file_path + '.rej'
    if os.path.exists(rej_file):
        try:
            os.remove(rej_file)
            logger.info(f"Removed reject file: {rej_file}")
        except OSError as e:
            logger.warning(f"Could not remove reject file {rej_file}: {e}")
