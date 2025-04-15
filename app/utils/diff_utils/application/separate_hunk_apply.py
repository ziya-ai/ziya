"""
Module for applying hunks separately that were marked as could_apply_separately.
This allows partial success with system patch by reapplying successful hunks individually.
"""

import os
import subprocess
import tempfile
from typing import Dict, List, Any, Optional, Tuple

from app.utils.logging_utils import logger
from ..core.exceptions import PatchApplicationError
from ..parsing.diff_parser import parse_unified_diff_exact_plus

def try_separate_hunks(pipeline, user_codebase_dir: str, separate_hunks: List[int]) -> bool:
    """
    Try to apply hunks separately that were marked as could_apply_separately.
    
    Args:
        pipeline: The diff pipeline
        user_codebase_dir: The base directory of the user's codebase
        separate_hunks: List of hunk IDs to try applying separately
        
    Returns:
        True if any changes were written, False otherwise
    """
    from ..pipeline.diff_pipeline import HunkStatus, PipelineStage
    
    if not separate_hunks:
        logger.info("No hunks to apply separately")
        return False
        
    logger.info(f"Attempting to apply {len(separate_hunks)} hunks separately")
    
    # Track if any changes were written
    any_changes_written = False
    
    # Process each hunk separately
    for hunk_id in separate_hunks:
        hunk_data = pipeline.result.hunks[hunk_id].hunk_data
        
        # Extract this hunk into its own diff
        try:
            hunk_diff = extract_hunk_from_diff(pipeline.original_diff, hunk_id)
            if not hunk_diff:
                logger.warning(f"Failed to extract hunk #{hunk_id} from diff")
                continue
                
            logger.info(f"Extracted hunk #{hunk_id} for separate application")
            logger.debug(f"Hunk diff content:\n{hunk_diff}")
            
            # Apply this hunk with system patch
            success = apply_single_hunk(hunk_diff, user_codebase_dir, pipeline.file_path)
            
            if success:
                logger.info(f"Successfully applied hunk #{hunk_id} separately")
                pipeline.update_hunk_status(
                    hunk_id=hunk_id,
                    stage=PipelineStage.SYSTEM_PATCH,
                    status=HunkStatus.SUCCEEDED,
                    position=pipeline.result.hunks[hunk_id].position,
                    confidence=pipeline.result.hunks[hunk_id].confidence
                )
                any_changes_written = True
            else:
                logger.warning(f"Failed to apply hunk #{hunk_id} separately")
                # Keep the hunk as is - it will be tried in later stages
        
        except Exception as e:
            logger.error(f"Error applying hunk #{hunk_id} separately: {str(e)}")
            # Keep the hunk as is - it will be tried in later stages
    
    if any_changes_written:
        pipeline.result.changes_written = True
        
    return any_changes_written

def apply_single_hunk(hunk_diff: str, user_codebase_dir: str, file_path: str) -> bool:
    """
    Apply a single hunk with system patch.
    
    Args:
        hunk_diff: The diff content for a single hunk
        user_codebase_dir: The base directory of the user's codebase
        file_path: Path to the file to modify
        
    Returns:
        True if the hunk was applied successfully, False otherwise
    """
    # Create a temporary file for the diff
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', suffix='.diff', delete=False) as temp_file:
            temp_file.write(hunk_diff)
            temp_path = temp_file.name
        
        logger.debug(f"Created temporary diff file at {temp_path}")
        
        # Apply the patch
        patch_command = ['patch', '-p1', '--forward', '--no-backup-if-mismatch', 
                         '--reject-file=-', '--batch', '--ignore-whitespace', 
                         '--verbose', '-i', temp_path]
        
        logger.debug(f"Running patch command: {' '.join(patch_command)}")
        
        patch_result = subprocess.run(
            patch_command,
            cwd=user_codebase_dir,
            capture_output=True,
            text=True,
            timeout=10
        )
        
        logger.debug(f"Patch stdout: {patch_result.stdout}")
        logger.debug(f"Patch stderr: {patch_result.stderr}")
        logger.debug(f"Patch return code: {patch_result.returncode}")
        
        # Check if the patch was applied successfully
        if patch_result.returncode == 0:
            logger.info(f"Successfully applied hunk to {file_path}")
            return True
        else:
            logger.warning(f"Failed to apply hunk to {file_path}")
            return False
            
    except Exception as e:
        logger.error(f"Error applying single hunk: {str(e)}")
        return False
    finally:
        # Clean up the temporary file
        if 'temp_path' in locals() and os.path.exists(temp_path):
            os.unlink(temp_path)
            logger.debug(f"Removed temporary diff file {temp_path}")

def extract_hunk_from_diff(diff_content: str, hunk_id: int) -> Optional[str]:
    """
    Extract a single hunk from a diff based on its ID.
    
    Args:
        diff_content: The full diff content
        hunk_id: The ID of the hunk to extract
        
    Returns:
        A diff containing only the specified hunk, or None if the hunk couldn't be found
    """
    try:
        # Extract the target file path from the diff
        target_file = None
        source_path = None
        
        for line in diff_content.splitlines():
            if line.startswith('+++ '):
                target_file = line[4:].strip()
                if target_file.startswith('b/'):
                    target_file = target_file[2:]
            elif line.startswith('--- '):
                source_path = line[4:].strip()
                
        if not target_file:
            logger.warning("Could not extract target file path from diff")
            return None
            
        if not source_path:
            logger.warning("Could not extract source path from diff")
            return None
            
        # Parse the diff to get all hunks
        hunks = list(parse_unified_diff_exact_plus(diff_content, target_file))
        
        # Find the hunk with the matching ID
        target_hunk = None
        for i, hunk in enumerate(hunks):
            # Use the index+1 as the hunk ID if the hunk doesn't have a number
            hunk_num = hunk.get('number', i+1)
            if hunk_num == hunk_id:
                target_hunk = hunk
                break
        
        if not target_hunk:
            logger.warning(f"Hunk #{hunk_id} not found in diff")
            return None
        
        # Construct a new diff with just this hunk
        # Recreate the hunk header from the hunk data
        hunk_header = f"@@ -{target_hunk['old_start']},{target_hunk['old_count']} +{target_hunk['new_start']},{target_hunk['new_count']} @@"
        
        header_lines = [
            f"--- {source_path}",
            f"+++ {target_file}",
            hunk_header
        ]
        
        # Construct the content lines
        content_lines = []
        
        # Use the 'lines' field if available, otherwise reconstruct from old_block and new_lines
        if 'lines' in target_hunk and target_hunk['lines']:
            content_lines = target_hunk['lines']
        else:
            # Reconstruct the content lines
            old_block = target_hunk['old_block']
            new_lines = target_hunk['new_lines']
            
            # Find lines that are in both old_block and new_lines (context lines)
            context_lines = set(old_block) & set(new_lines)
            
            # Add removed lines (in old_block but not in context_lines)
            for line in old_block:
                if line not in context_lines:
                    content_lines.append(f"-{line}")
            
            # Add context lines and added lines
            for line in new_lines:
                if line in context_lines:
                    content_lines.append(f" {line}")
                else:
                    content_lines.append(f"+{line}")
        
        return '\n'.join(header_lines + content_lines)
        
    except Exception as e:
        logger.error(f"Error extracting hunk #{hunk_id} from diff: {str(e)}")
        return None
