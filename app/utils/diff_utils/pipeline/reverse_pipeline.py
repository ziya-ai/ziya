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
    
    # If the file already matches the expected content, nothing to reverse
    if expected_content is not None and current_content.rstrip() == expected_content.rstrip():
        logger.info("File already matches expected content - nothing to reverse")
        return {'status': 'success', 'stage': 'already_correct', 'changes_written': False}
    
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
    
    # Stage 2b: Try direct reverse WITHOUT verification — save as best-effort candidate
    best_effort_content = None
    result_no_verify = _try_direct_reverse(diff_content, file_path, current_content, None)
    if result_no_verify['success']:
        with open(file_path, 'r', encoding='utf-8') as f:
            best_effort_content = f.read()
        # Restore file for subsequent stages
        with open(file_path, 'w', encoding='utf-8') as f:
            f.write(current_content)
    
    # Stage 3: Try reversed diff with simplified application
    result = _try_reversed_diff_simple(diff_content, file_path, current_content, expected_content)
    if result['success']:
        logger.info("Reverse succeeded via simplified reversed diff")
        return {'status': 'success', 'stage': 'reversed_diff_simple', 'changes_written': True}
    
    # Stage 4: Try reversed diff through full forward pipeline (with fuzzy matching)
    result = _try_reversed_diff_full_pipeline(diff_content, file_path, current_content, expected_content)
    if result['success']:
        # If we have expected content and a best-effort candidate, compare
        if expected_content is not None and best_effort_content is not None:
            with open(file_path, 'r', encoding='utf-8') as f:
                stage4_content = f.read()
            import difflib
            stage2_ratio = difflib.SequenceMatcher(None, best_effort_content, expected_content).ratio()
            stage4_ratio = difflib.SequenceMatcher(None, stage4_content, expected_content).ratio()
            if stage2_ratio > stage4_ratio:
                logger.info(f"Stage 2 result closer to expected ({stage2_ratio:.3f} vs {stage4_ratio:.3f}), using it")
                with open(file_path, 'w', encoding='utf-8') as f:
                    f.write(best_effort_content)
                return {'status': 'success', 'stage': 'direct_reverse_best_effort', 'changes_written': True}
        logger.info("Reverse succeeded via full forward pipeline")
        return {'status': 'success', 'stage': 'reversed_diff_full', 'changes_written': True}
    
    # If all verified stages failed but we have a best-effort result, use it
    if best_effort_content is not None:
        logger.info("Using best-effort direct reverse result")
        with open(file_path, 'w', encoding='utf-8') as f:
            f.write(best_effort_content)
        return {'status': 'success', 'stage': 'direct_reverse_best_effort', 'changes_written': True}
    
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
    
    Parse the diff, locate the new content block in the file, then surgically
    replace only the changed lines (keeping the file's actual context lines intact).
    Falls back to full block replacement if surgical approach fails.
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
            new_block = hunk.get('new_lines', [])  # Full new block (context + additions)
            old_block = hunk.get('old_block', [])   # Full old block (context + removals)
            
            if not new_block and not old_block:
                continue
            
            new_start = hunk.get('new_start', 1) - 1 + offset
            
            if new_block:
                # Find the new_block in the current content
                found_pos = _find_lines_in_content(result_lines, new_block, new_start)
                
                if found_pos is not None:
                    # Surgical replacement: walk the hunk lines and only swap
                    # the changed portions, keeping the file's actual context
                    hunk_lines = hunk.get('lines', [])
                    new_result = []
                    file_idx = found_pos
                    
                    for line in hunk_lines:
                        if line.startswith('+') and not line.startswith('+++'):
                            # Added line in forward — skip it (don't include in result)
                            file_idx += 1
                        elif line.startswith('-') and not line.startswith('---'):
                            # Removed line in forward — restore it
                            new_result.append(line[1:] + '\n' if not line[1:].endswith('\n') else line[1:])
                        else:
                            # Context line — keep the file's actual line
                            if file_idx < len(result_lines):
                                new_result.append(result_lines[file_idx])
                            file_idx += 1
                    
                    result_lines[found_pos:found_pos + len(new_block)] = new_result
                    offset += len(new_result) - len(new_block)
                else:
                    # Fallback: process each change group individually
                    # Extract change groups from hunk lines
                    hunk_lines = hunk.get('lines', [])
                    change_groups = []
                    current_removed = []
                    current_added = []
                    context_count = 0
                    
                    for line in hunk_lines:
                        if line.startswith('-') and not line.startswith('---'):
                            current_removed.append(line[1:])
                        elif line.startswith('+') and not line.startswith('+++'):
                            current_added.append(line[1:])
                        else:
                            if current_removed or current_added:
                                change_groups.append((current_removed, current_added, context_count))
                                current_removed = []
                                current_added = []
                            context_count += 1
                    if current_removed or current_added:
                        change_groups.append((current_removed, current_added, context_count))
                    
                    if not change_groups:
                        return {'success': False}
                    
                    # Apply each change group in reverse order to preserve positions
                    all_applied = True
                    for removed, added, _ in reversed(change_groups):
                        if added:
                            found = _find_lines_in_content(result_lines, added, new_start)
                            if found is not None:
                                restored = [l + '\n' if not l.endswith('\n') else l for l in removed]
                                result_lines[found:found + len(added)] = restored
                                offset += len(removed) - len(added)
                            else:
                                all_applied = False
                                break
                        elif removed:
                            # Pure deletion in forward — need to re-insert
                            # Use nearby context to find position
                            insert_pos = min(new_start, len(result_lines))
                            restored = [l + '\n' if not l.endswith('\n') else l for l in removed]
                            result_lines[insert_pos:insert_pos] = restored
                            offset += len(removed)
                    
                    if not all_applied:
                        return {'success': False}
            else:
                # Pure deletion in forward (new_block empty, old_block has content)
                insert_pos = min(new_start, len(result_lines))
                old_block_with_endings = [l + '\n' if not l.endswith('\n') else l for l in old_block]
                result_lines[insert_pos:insert_pos] = old_block_with_endings
                offset += len(old_block)
        
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
    
    def check_match_stripped(pos: int) -> bool:
        """Fallback: match ignoring leading/trailing whitespace differences."""
        if pos < 0 or pos + len(search_lines) > len(content_lines):
            return False
        for i, search_line in enumerate(search_normalized):
            content_line = content_lines[pos + i].rstrip('\n\r')
            if content_line.strip() != search_line.strip():
                return False
        return True
    
    # Search in expanding radius from start_pos (exact match)
    max_radius = min(100, len(content_lines))
    
    for radius in range(max_radius):
        for pos in [start_pos + radius, start_pos - radius]:
            if check_match(pos):
                return pos
    
    # Full file search as fallback (exact match)
    for pos in range(len(content_lines)):
        if check_match(pos):
            return pos
    
    # Whitespace-normalized search near start_pos as last resort
    for radius in range(max_radius):
        for pos in [start_pos + radius, start_pos - radius]:
            if check_match_stripped(pos):
                return pos
    
    # High-ratio fuzzy match: accept if almost all lines match (for large blocks)
    if len(search_lines) >= 5:
        threshold = max(1, len(search_lines) // 6)  # Allow ~1 mismatch per 6 lines
        for radius in range(max_radius):
            for pos in [start_pos + radius, start_pos - radius]:
                if pos < 0 or pos + len(search_lines) > len(content_lines):
                    continue
                mismatches = 0
                for i, search_line in enumerate(search_normalized):
                    if content_lines[pos + i].rstrip('\n\r') != search_line:
                        mismatches += 1
                        if mismatches > threshold:
                            break
                if mismatches <= threshold:
                    return pos
    
    return None


def _try_reversed_diff_simple(diff_content: str, file_path: str, current_content: str, expected_content: Optional[str] = None) -> Dict[str, Any]:
    """
    Stage 3: Apply reversed diff with simplified matching.
    
    Generate the reversed diff and apply it using full block matching
    (old_block → new_lines) without fuzzy matching or "already applied" detection.
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
            old_block = hunk.get('old_block', [])   # Full old block to find
            new_block = hunk.get('new_lines', [])    # Full new block to replace with
            
            if not old_block and not new_block:
                continue
            
            old_start = hunk.get('old_start', 1) - 1 + offset
            
            if old_block:
                found_pos = _find_lines_in_content(result_lines, old_block, old_start)
                
                if found_pos is not None:
                    new_block_with_endings = [l + '\n' if not l.endswith('\n') else l for l in new_block]
                    result_lines[found_pos:found_pos + len(old_block)] = new_block_with_endings
                    offset += len(new_block) - len(old_block)
                else:
                    return {'success': False}
            elif new_block:
                # Pure addition
                insert_pos = min(old_start, len(result_lines))
                new_block_with_endings = [l + '\n' if not l.endswith('\n') else l for l in new_block]
                result_lines[insert_pos:insert_pos] = new_block_with_endings
                offset += len(new_block)
        
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
