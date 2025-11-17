"""
Utilities for file operations related to diffs and patches.
"""

import os
import re
import glob
from typing import List

from app.utils.logging_utils import logger

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
            
        # Extract the file path from the diff --git line
        full_path = os.path.join(base_dir, file_path)
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
        with open(full_path, 'w', encoding='utf-8') as f:
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

        # Find and remove .rej and .orig files
        for pattern in ['*.rej', '*.orig']:
            for artifact in glob.glob(os.path.join(file_dir, pattern)):
                logger.info(f"Removing patch artifact: {artifact}")
                os.remove(artifact)
    except Exception as e:
        logger.warning(f"Error cleaning up patch artifacts: {str(e)}")

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
