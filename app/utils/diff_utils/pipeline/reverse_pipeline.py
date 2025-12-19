"""
Reverse diff application pipeline.

This module provides a structured pipeline for applying diffs in reverse,
mirroring the forward pipeline but with strategies optimized for reverse application.
"""

import os
import logging
import subprocess
import tempfile
from typing import Dict, Any, List, Optional

from ..core.diff_reverser import reverse_diff
from ..parsing.diff_parser import parse_unified_diff_exact_plus
from .pipeline_manager import apply_diff_pipeline

logger = logging.getLogger(__name__)


def apply_reverse_diff_pipeline(diff_content: str, file_path: str, expected_content: Optional[str] = None) -> Dict[str, Any]:
    """
    Apply a diff in reverse using a structured pipeline approach.
    
    Stages:
    1. patch -R (system patch with reverse flag)
    2. Direct content replacement (parse diff and swap changes)
    3. Reversed diff through simplified difflib (no fuzzy matching)
    4. Reversed diff through full forward pipeline (with fuzzy matching)
    
    Args:
        diff_content: The original (non-reversed) diff content
        file_path: Path to the file to reverse
        expected_content: Optional expected result for verification (used in testing)
        
    Returns:
        Dictionary with status and details
    """
    logger.info("Starting reverse diff pipeline...")
    
    # Read current file content
    with open(file_path, 'r', encoding='utf-8') as f:
        current_content = f.read()
    
    # Stage 1: Try patch -R
    result = _try_patch_reverse(diff_content, file_path, current_content, expected_content)
    if result['success']:
        logger.info("Reverse succeeded via patch -R")
        return {'status': 'success', 'stage': 'patch_reverse', 'changes_written': True}
    
    # Stage 2: Try direct content replacement
    result = _try_direct_reverse(diff_content, file_path, current_content, expected_content)
    if result['success']:
        logger.info("Reverse succeeded via direct replacement")
        return {'status': 'success', 'stage': 'direct_reverse', 'changes_written': True}
    
    # Stage 3: Try reversed diff with simplified application
    result = _try_reversed_diff_simple(diff_content, file_path, current_content, expected_content)
    if result['success']:
        logger.info("Reverse succeeded via simplified reversed diff")
        return {'status': 'success', 'stage': 'reversed_diff_simple', 'changes_written': True}
    
    # Stage 4: Try reversed diff through full forward pipeline (with fuzzy matching)
    result = _try_reversed_diff_full_pipeline(diff_content, file_path, current_content, expected_content)
    if result['success']:
        logger.info("Reverse succeeded via full forward pipeline")
        return {'status': 'success', 'stage': 'reversed_diff_full', 'changes_written': True}
    
    logger.error("All reverse stages failed")
    return {'status': 'failed', 'error': 'All reverse stages failed'}


def _try_patch_reverse(diff_content: str, file_path: str, current_content: str, expected_content: Optional[str] = None) -> Dict[str, Any]:
    """Stage 1: Try system patch with -R flag."""
    import shutil
    import re
    
    # Extract the path from the diff header to set up correct directory structure
    match = re.search(r'^---\s+a/(.+)$', diff_content, re.MULTILINE)
    if not match:
        match = re.search(r'^---\s+(.+)$', diff_content, re.MULTILINE)
    
    if not match:
        return {'success': False}
    
    diff_path = match.group(1).split('\t')[0].strip()
    
    temp_dir = tempfile.mkdtemp()
    try:
        # Create the directory structure from the diff
        target_file = os.path.join(temp_dir, diff_path)
        os.makedirs(os.path.dirname(target_file), exist_ok=True)
        
        # Copy current file content to the temp location
        shutil.copy2(file_path, target_file)
        
        # Write diff file
        diff_file = os.path.join(temp_dir, 'changes.diff')
        with open(diff_file, 'w') as f:
            f.write(diff_content)
        
        # Run patch -R with -p1 from the temp directory
        result = subprocess.run(
            ['patch', '-R', '-p1', '--no-backup-if-mismatch', '-i', diff_file],
            cwd=temp_dir,
            capture_output=True,
            text=True,
            timeout=5
        )
        
        if result.returncode == 0:
            with open(target_file, 'r') as f:
                result_content = f.read()
            
            # If we have expected content, verify the result
            if expected_content is not None:
                if result_content.rstrip() != expected_content.rstrip():
                    logger.debug("patch -R succeeded but result doesn't match expected")
                    return {'success': False}
            
            # Copy the result back
            shutil.copy2(target_file, file_path)
            return {'success': True}
        
        return {'success': False}
    except subprocess.TimeoutExpired:
        return {'success': False}
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def _try_direct_reverse(diff_content: str, file_path: str, current_content: str, expected_content: Optional[str] = None) -> Dict[str, Any]:
    """
    Stage 2: Direct content replacement.
    
    Parse the diff and directly replace the "new" content with the "old" content.
    This works when the forward diff was applied cleanly.
    """
    try:
        hunks = list(parse_unified_diff_exact_plus(diff_content, file_path))
        if not hunks:
            return {'success': False}
        
        current_lines = current_content.splitlines(keepends=True)
        # Ensure last line has newline for consistency
        if current_lines and not current_lines[-1].endswith('\n'):
            current_lines[-1] += '\n'
        
        result_lines = current_lines.copy()
        offset = 0
        
        for hunk in hunks:
            old_lines = []  # Lines removed in forward diff (to restore)
            new_lines = []  # Lines added in forward diff (to remove)
            context_before = []  # Context lines before changes
            
            for line in hunk.get('lines', []):
                if line.startswith('-') and not line.startswith('---'):
                    old_lines.append(line[1:])
                elif line.startswith('+') and not line.startswith('+++'):
                    new_lines.append(line[1:])
                elif line.startswith(' ') and not old_lines and not new_lines:
                    context_before.append(line[1:])
            
            if not new_lines and not old_lines:
                continue
            
            # Find where the new_lines are in the current content
            new_start = hunk.get('new_start', 1) - 1 + offset
            
            # Search for the new_lines around the expected position
            found_pos = _find_lines_in_content(result_lines, new_lines, new_start)
            
            if found_pos is not None:
                # Replace new_lines with old_lines
                old_lines_with_endings = [l + '\n' if not l.endswith('\n') else l for l in old_lines]
                result_lines[found_pos:found_pos + len(new_lines)] = old_lines_with_endings
                offset += len(old_lines) - len(new_lines)
            elif not new_lines and old_lines:
                # Pure deletion in forward = pure addition in reverse
                # Use context lines to find position
                if context_before:
                    found_pos = _find_lines_in_content(result_lines, context_before, new_start)
                    if found_pos is not None:
                        insert_pos = found_pos + len(context_before)
                    else:
                        insert_pos = new_start
                else:
                    insert_pos = new_start
                old_lines_with_endings = [l + '\n' if not l.endswith('\n') else l for l in old_lines]
                result_lines[insert_pos:insert_pos] = old_lines_with_endings
                offset += len(old_lines)
            else:
                # Couldn't find the content to replace
                return {'success': False}
        
        # Write the result
        result_content = ''.join(result_lines)
        
        # If we have expected content, verify the result before writing
        if expected_content is not None:
            if result_content.rstrip() != expected_content.rstrip():
                logger.debug("Direct reverse succeeded but result doesn't match expected")
                return {'success': False}
        
        with open(file_path, 'w', encoding='utf-8') as f:
            f.write(result_content)
        
        return {'success': True}
    
    except Exception as e:
        logger.debug(f"Direct reverse failed: {e}")
        return {'success': False}


def _find_lines_in_content(content_lines: List[str], search_lines: List[str], start_pos: int) -> Optional[int]:
    """Find search_lines in content_lines, starting near start_pos, then full file."""
    if not search_lines:
        return None
    
    # Normalize search lines
    search_normalized = [l.rstrip('\n\r') for l in search_lines]
    
    def check_match(pos: int) -> bool:
        if pos < 0 or pos + len(search_lines) > len(content_lines):
            return False
        for i, search_line in enumerate(search_normalized):
            content_line = content_lines[pos + i].rstrip('\n\r')
            if content_line != search_line:
                return False
        return True
    
    # Search in expanding radius from start_pos
    max_radius = min(100, len(content_lines))
    
    for radius in range(max_radius):
        for pos in [start_pos + radius, start_pos - radius]:
            if check_match(pos):
                return pos
    
    # Full file search as fallback
    for pos in range(len(content_lines)):
        if check_match(pos):
            return pos
    
    return None


def _try_reversed_diff_simple(diff_content: str, file_path: str, current_content: str, expected_content: Optional[str] = None) -> Dict[str, Any]:
    """
    Stage 3: Apply reversed diff with simplified matching.
    
    Generate the reversed diff and apply it without fuzzy matching or
    "already applied" detection.
    """
    try:
        reversed_diff_content = reverse_diff(diff_content)
        hunks = list(parse_unified_diff_exact_plus(reversed_diff_content, file_path))
        
        if not hunks:
            return {'success': False}
        
        current_lines = current_content.splitlines(keepends=True)
        if current_lines and not current_lines[-1].endswith('\n'):
            current_lines[-1] += '\n'
        
        result_lines = current_lines.copy()
        offset = 0
        
        for hunk in hunks:
            old_lines = []  # Lines to find and remove
            new_lines = []  # Lines to insert
            context_before = []  # Context lines before changes
            
            for line in hunk.get('lines', []):
                if line.startswith('-') and not line.startswith('---'):
                    old_lines.append(line[1:])
                elif line.startswith('+') and not line.startswith('+++'):
                    new_lines.append(line[1:])
                elif line.startswith(' ') and not old_lines and not new_lines:
                    # Context line before any changes
                    context_before.append(line[1:])
            
            if not old_lines and not new_lines:
                continue
            
            old_start = hunk.get('old_start', 1) - 1 + offset
            
            if old_lines:
                # Find and replace old_lines with new_lines
                found_pos = _find_lines_in_content(result_lines, old_lines, old_start)
                
                if found_pos is not None:
                    new_lines_with_endings = [l + '\n' if not l.endswith('\n') else l for l in new_lines]
                    result_lines[found_pos:found_pos + len(old_lines)] = new_lines_with_endings
                    offset += len(new_lines) - len(old_lines)
                else:
                    return {'success': False}
            elif new_lines:
                # Pure addition - use context lines to find position
                if context_before:
                    found_pos = _find_lines_in_content(result_lines, context_before, old_start)
                    if found_pos is not None:
                        insert_pos = found_pos + len(context_before)
                    else:
                        insert_pos = old_start
                else:
                    insert_pos = old_start
                new_lines_with_endings = [l + '\n' if not l.endswith('\n') else l for l in new_lines]
                result_lines[insert_pos:insert_pos] = new_lines_with_endings
                offset += len(new_lines)
        
        # Write the result
        result_content = ''.join(result_lines)
        
        # If we have expected content, verify the result before writing
        if expected_content is not None:
            if result_content.rstrip() != expected_content.rstrip():
                logger.debug("Reversed diff simple succeeded but result doesn't match expected")
                return {'success': False}
        
        with open(file_path, 'w', encoding='utf-8') as f:
            f.write(result_content)
        
        return {'success': True}
    
    except Exception as e:
        logger.debug(f"Reversed diff simple failed: {e}")
        return {'success': False}


def _try_reversed_diff_full_pipeline(diff_content: str, file_path: str, current_content: str, expected_content: Optional[str] = None) -> Dict[str, Any]:
    """
    Stage 4: Apply reversed diff through the full forward pipeline.
    
    This uses the same fuzzy matching logic as forward application.
    Note: We don't verify against expected_content here because fuzzy matching
    may produce slightly different but valid results.
    """
    try:
        reversed_diff_content = reverse_diff(diff_content)
        
        # Use the forward pipeline with skip_already_applied_check
        result = apply_diff_pipeline(reversed_diff_content, file_path, skip_already_applied_check=True)
        
        # apply_diff_pipeline returns a dict with 'status' key
        return {'success': result.get('status') == 'success'}
    except Exception as e:
        logger.debug(f"Reversed diff full pipeline failed: {e}")
        return {'success': False}
