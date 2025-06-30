"""
Module for applying hunks separately that were marked as could_apply_separately.
This allows partial success with system patch by reapplying successful hunks individually.
"""

import os
import subprocess
import tempfile
from typing import Dict, List, Any, Optional, Tuple

from app.utils.logging_utils import logger
from ..application.git_diff import parse_patch_output
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
    
    logger.info(f"Initial separate_hunks list: {separate_hunks}")
    # Parse the diff to get all hunks for line number adjustment
    hunks = list(parse_unified_diff_exact_plus(pipeline.original_diff, pipeline.file_path))
    
    # Map hunk IDs to their indices in the hunks list
    hunk_index_map = {}
    for i, hunk in enumerate(hunks):
        hunk_id = hunk.get('number', i+1)
        hunk_index_map[hunk_id] = i
    
    # Track line number adjustments as we apply hunks
    line_adjustment = 0
    
    # Track which hunks have actually been applied successfully
    successfully_applied_hunks = set()
    
    # CRITICAL FIX: Calculate line adjustment based on the order of hunk processing
    # For each hunk we're about to process, check if earlier hunks in the diff failed
    
    # CRITICAL FIX: Be more selective about which hunks to try separately
    # If we have specific hunks marked as could_apply_separately, prioritize those
    # Otherwise, only add sequential pending hunks that don't require fuzz/offset
    if not separate_hunks:
        # Find hunks that are marked as could_apply_separately
        separate_hunks = [
            hunk_id for hunk_id, tracker in pipeline.result.hunks.items()
            if tracker.hunk_data.get("could_apply_separately", False) and 
            tracker.status == HunkStatus.PENDING
        ]
        
        # If no hunks are explicitly marked, add sequential pending hunks
        if not separate_hunks:
            for hunk_id, tracker in sorted(pipeline.result.hunks.items()):
                if tracker.status == HunkStatus.PENDING:
                    # Only add hunks that don't have large fuzz or offset requirements
                    hunk_index = hunk_index_map.get(hunk_id)
                    if hunk_index is not None and not hunks[hunk_index].get('requires_fuzz', False):
                        separate_hunks.append(hunk_id)
    
    if not separate_hunks:
        logger.info("No suitable hunks to apply separately")
        return False
        
    # Sort hunks by their position in the file to ensure proper sequential application
    separate_hunks.sort(key=lambda hunk_id: hunks[hunk_index_map.get(hunk_id, 0)].get('old_start', 0))
    
    # Process each hunk separately in order
    for hunk_id in separate_hunks:
        hunk_index = hunk_index_map.get(hunk_id)
        if hunk_index is None:
            logger.warning(f"Hunk #{hunk_id} not found in hunks list")
            continue
            
        hunk = hunks[hunk_index]
        
        # CRITICAL FIX: Calculate line adjustment for this specific hunk
        # based on which earlier hunks have failed to apply
        current_line_adjustment = 0
        for i in range(hunk_index):
            earlier_hunk = hunks[i]
            earlier_hunk_id = earlier_hunk.get('number', i+1)
            
            # Check if this earlier hunk failed to apply
            if earlier_hunk_id not in successfully_applied_hunks:
                # This hunk failed, so we need to adjust line numbers
                old_count = earlier_hunk.get('old_count', 0)
                new_count = earlier_hunk.get('new_count', 0)
                net_change = new_count - old_count
                current_line_adjustment -= net_change  # Subtract because it didn't apply
                logger.info(f"Adjusting hunk #{hunk_id} by {-net_change} lines due to failed hunk #{earlier_hunk_id}")
        
        # Extract this hunk into its own diff with line number adjustment
        try:
            # Skip hunks that are already marked as succeeded or already applied
            if pipeline.result.hunks[hunk_id].status in (HunkStatus.SUCCEEDED, HunkStatus.ALREADY_APPLIED):
                logger.info(f"Skipping hunk #{hunk_id} as it's already {pipeline.result.hunks[hunk_id].status.value}")
                continue
                
            # Extract the hunk with proper line adjustment
            hunk_diff = extract_hunk_from_diff(pipeline.original_diff, hunk_id, current_line_adjustment)
            if not hunk_diff:
                logger.warning(f"Failed to extract hunk #{hunk_id} from diff")
                continue

            logger.info(f"Extracted hunk #{hunk_id} for separate application with line adjustment {current_line_adjustment}")
            logger.debug(f"Hunk diff content:\n{hunk_diff}")
            
            # Apply this hunk with system patch
            success, already_applied = apply_single_hunk(hunk_diff, user_codebase_dir, pipeline.file_path, hunk)
            
            if success:
                status = HunkStatus.ALREADY_APPLIED if already_applied else HunkStatus.SUCCEEDED
                logger.info(f"{'Hunk was already applied' if already_applied else 'Successfully applied hunk'} #{hunk_id} separately")
                
                # Mark this hunk as successfully applied
                successfully_applied_hunks.add(hunk_id)
                
                pipeline.update_hunk_status(
                    hunk_id=hunk_id,
                    stage=PipelineStage.SYSTEM_PATCH,
                    status=status,
                    position=pipeline.result.hunks[hunk_id].position,
                    confidence=pipeline.result.hunks[hunk_id].confidence
                )
                any_changes_written = any_changes_written or not already_applied
                
                logger.info(f"Successfully applied hunk #{hunk_id} separately")
            else:
                logger.warning(f"Failed to apply hunk #{hunk_id} separately")
                # Keep the hunk as is - it will be tried in later stages
        
        except Exception as e:
            logger.error(f"Error applying hunk #{hunk_id} separately: {str(e)}")
            # Keep the hunk as is - it will be tried in later stages

    if any_changes_written:
        pipeline.result.changes_written = True
        
    return any_changes_written

def apply_single_hunk(hunk_diff: str, user_codebase_dir: str, file_path: str, hunk: Dict[str, Any]) -> Tuple[bool, bool]:
    """
    Apply a single hunk with system patch.
    
    Args:
        hunk_diff: The diff content for a single hunk
        user_codebase_dir: The base directory of the user's codebase
        file_path: Path to the file to modify
        
    Returns:
        Tuple of (success, already_applied) where:
        - success: True if the hunk was applied successfully or was already applied
        - already_applied: True if the hunk was already applied
    """
    # Create a temporary file for the diff
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', suffix='.diff', delete=False) as temp_file:
            temp_file.write(hunk_diff)
            temp_path = temp_file.name
        
        logger.debug(f"Created temporary diff file at {temp_path}")
        
        # 1. Run Dry Run
        patch_command_dry = ['patch', '-p1', '--forward', '--no-backup-if-mismatch', 
                             '--reject-file=-', '--batch', '--fuzz=0',  # CRITICAL FIX: Disable fuzzy matching
                             '--verbose', '--dry-run', '-i', temp_path]
        
        logger.debug(f"Running patch command (dry-run): {' '.join(patch_command_dry)}")
        
        dry_run_result = subprocess.run(
            patch_command_dry,
            cwd=user_codebase_dir,
            capture_output=True,
            text=True,
            timeout=10
        )
        
        logger.debug(f"Patch dry run stdout: {dry_run_result.stdout}")
        logger.debug(f"Patch dry run stderr: {dry_run_result.stderr}")
        logger.debug(f"Patch dry run return code: {dry_run_result.returncode}")
        
        # Parse dry run output
        dry_run_status_map = parse_patch_output(dry_run_result.stdout, dry_run_result.stderr)
        dry_run_hunk_info = dry_run_status_map.get(1, {})  # Default to empty dict if not found
        
        # Check if parsing failed or returned empty
        if not dry_run_hunk_info and dry_run_result.returncode != 0 and not "already applied" in dry_run_result.stdout:
            logger.warning(f"Could not parse dry run status for hunk in {file_path}")
            return False, False  # Treat parsing failure as patch failure
        
        dry_run_status = dry_run_hunk_info.get("status")
        dry_run_fuzz = dry_run_hunk_info.get("fuzz", 0)
        dry_run_offset = dry_run_hunk_info.get("offset", 0)  # Check for offset too
        
        # Check if the patch was already applied
        # 2. Handle Dry Run Results
        if dry_run_status == "failed":
            logger.warning(f"Dry run failed for hunk in {file_path}. Status: {dry_run_status}")
            return False, False  # Failed
        elif dry_run_status == "already_applied":
            # CRITICAL FIX: Check if this is a false "already applied" message
            # If the dry run says "already applied" but the return code is 0,
            # it's likely a clean application that patch is misinterpreting
            if dry_run_result.returncode == 0:
                # Look for specific indicators of true "already applied" status
                truly_already_applied = (
                    "Skipping patch." in dry_run_result.stdout or
                    "Reversed (or previously applied) patch detected!  Skipping patch." in dry_run_result.stdout
                )
                logger.info(f"Dry run indicates {'truly already applied' if truly_already_applied else 'successful application'} for {file_path}")
                return True, truly_already_applied  # Success, Already Applied only if truly already applied
            else:
                return False, False  # Failed with "already applied" but non-zero return code
        elif dry_run_status == "succeeded" and (dry_run_fuzz > 0 or dry_run_offset != 0):
            logger.warning(f"Dry run succeeded with fuzz ({dry_run_fuzz}) or offset ({dry_run_offset}) for hunk in {file_path}. Deferring to difflib.")
            return False, False  # Treat as failure for this stage
        elif dry_run_status == "succeeded":
            # Clean success in dry run, proceed to actual patch
            logger.info(f"Dry run succeeded cleanly for hunk in {file_path}. Attempting actual patch.")
            pass  # Proceed to actual patch command
        else:
            # Unknown status from dry run or parsing failed but exit code was 0
            logger.warning(f"Unknown or ambiguous dry run status for hunk in {file_path}. Status: {dry_run_status}. Treating as failure.")
            return False, False  # Failed
        
        # 3. Run Actual Patch (only if dry run was clean success)
        patch_command_apply = ['patch', '-p1', '--forward', '--no-backup-if-mismatch',
                               '--reject-file=-', '--batch', '--fuzz=0',
                               '--verbose', '-i', temp_path]  # No --noreverse
        
        logger.debug(f"Running patch command (actual): {' '.join(patch_command_apply)}")
        
        patch_result = subprocess.run(
            patch_command_apply,
            cwd=user_codebase_dir,
            capture_output=True,
            text=True,
            timeout=10
        )
        
        logger.debug(f"Actual patch stdout: {patch_result.stdout}")
        logger.debug(f"Actual patch stderr: {patch_result.stderr}")
        logger.debug(f"Actual patch return code: {patch_result.returncode}")
        
        # Check if the patch was already applied
        # 2. Handle Dry Run Results
        if dry_run_status == "failed":
            logger.warning(f"Dry run failed for hunk in {file_path}. Status: {dry_run_status}")
            return False, False  # Failed
        elif dry_run_status == "already_applied":
            # CRITICAL FIX: Check if this is a false "already applied" message
            # If the dry run says "already applied" but the return code is 0,
            # it's likely a clean application that patch is misinterpreting
            if dry_run_result.returncode == 0:
                # Look for specific indicators of true "already applied" status
                truly_already_applied = (
                    "Skipping patch." in dry_run_result.stdout or
                    "Reversed (or previously applied) patch detected!  Skipping patch." in dry_run_result.stdout
                )
                logger.info(f"Dry run indicates {'truly already applied' if truly_already_applied else 'successful application'} for {file_path}")
                return True, truly_already_applied  # Success, Already Applied only if truly already applied
            else:
                return False, False  # Failed with "already applied" but non-zero return code
        elif dry_run_status == "succeeded" and (dry_run_fuzz > 0 or dry_run_offset != 0):
            logger.warning(f"Dry run succeeded with fuzz ({dry_run_fuzz}) or offset ({dry_run_offset}) for hunk in {file_path}. Deferring to difflib.")
            return False, False  # Treat as failure for this stage
        elif dry_run_status == "succeeded":
            # Clean success in dry run, proceed to actual patch
            logger.info(f"Dry run succeeded cleanly for hunk in {file_path}. Attempting actual patch.")
            pass  # Proceed to actual patch command
        else:
            # Unknown status from dry run or parsing failed but exit code was 0
            logger.warning(f"Unknown or ambiguous dry run status for hunk in {file_path}. Status: {dry_run_status}. Treating as failure.")
            return False, False  # Failed
        
        # 3. Run Actual Patch (only if dry run was clean success)
        patch_command_apply = ['patch', '-p1', '--forward', '--no-backup-if-mismatch',
                               '--reject-file=-', '--batch', '--fuzz=0',
                               '--verbose', '-i', temp_path]  # No --noreverse
        
        logger.debug(f"Running patch command (actual): {' '.join(patch_command_apply)}")
        
        patch_result = subprocess.run(
            patch_command_apply,
            cwd=user_codebase_dir,
            capture_output=True,
            text=True,
            timeout=10
        )
        
        logger.debug(f"Actual patch stdout: {patch_result.stdout}")
        logger.debug(f"Actual patch stderr: {patch_result.stderr}")
        logger.debug(f"Actual patch return code: {patch_result.returncode}")
        
        # Check if the patch was already applied
        # 2. Handle Dry Run Results
        if dry_run_status == "failed":
            logger.warning(f"Dry run failed for hunk in {file_path}. Status: {dry_run_status}")
            return False, False  # Failed
        elif dry_run_status == "already_applied":
            # CRITICAL FIX: Check if this is a false "already applied" message
            # If the dry run says "already applied" but the return code is 0,
            # it's likely a clean application that patch is misinterpreting
            if dry_run_result.returncode == 0:
                # Look for specific indicators of true "already applied" status
                truly_already_applied = (
                    "Skipping patch." in dry_run_result.stdout or
                    "Reversed (or previously applied) patch detected!  Skipping patch." in dry_run_result.stdout
                )
                logger.info(f"Dry run indicates {'truly already applied' if truly_already_applied else 'successful application'} for {file_path}")
                return True, truly_already_applied  # Success, Already Applied only if truly already applied
            else:
                return False, False  # Failed with "already applied" but non-zero return code
        elif dry_run_status == "succeeded" and (dry_run_fuzz > 0 or dry_run_offset != 0):
            logger.warning(f"Dry run succeeded with fuzz ({dry_run_fuzz}) or offset ({dry_run_offset}) for hunk in {file_path}. Deferring to difflib.")
            return False, False  # Treat as failure for this stage
        elif dry_run_status == "succeeded":
            # Clean success in dry run, proceed to actual patch
            logger.info(f"Dry run succeeded cleanly for hunk in {file_path}. Attempting actual patch.")
            pass  # Proceed to actual patch command
        else:
            # Unknown status from dry run or parsing failed but exit code was 0
            logger.warning(f"Unknown or ambiguous dry run status for hunk in {file_path}. Status: {dry_run_status}. Treating as failure.")
            return False, False  # Failed
        
        # 3. Run Actual Patch (only if dry run was clean success)
        patch_command_apply = ['patch', '-p1', '--forward', '--no-backup-if-mismatch',
                               '--reject-file=-', '--batch', '--fuzz=0',
                               '--verbose', '-i', temp_path]  # No --noreverse
        
        logger.debug(f"Running patch command (actual): {' '.join(patch_command_apply)}")
        
        patch_result = subprocess.run(
            patch_command_apply,
            cwd=user_codebase_dir,
            capture_output=True,
            text=True,
            timeout=10
        )
        
        logger.debug(f"Actual patch stdout: {patch_result.stdout}")
        logger.debug(f"Actual patch stderr: {patch_result.stderr}")
        logger.debug(f"Actual patch return code: {patch_result.returncode}")
        
        # Check if the patch was applied successfully
        if patch_result.returncode == 0:
            logger.info(f"Actual patch succeeded for hunk in {file_path}")
            
            # CRITICAL DEBUG: Check what's actually in the file after patch
            try:
                full_file_path = os.path.join(user_codebase_dir, file_path)
                if os.path.exists(full_file_path):
                    with open(full_file_path, 'r', encoding='utf-8') as f:
                        file_content = f.read()
                    
                    # Check if this patch unexpectedly applied other changes
                    if 'QuestionProvider' in file_content:
                        import_count = file_content.count('import {QuestionProvider}')
                        jsx_count = file_content.count('<QuestionProvider>')
                        logger.error(f"🚨 PATCH SIDE EFFECT DETECTED!")
                        logger.error(f"File after patch contains QuestionProvider:")
                        logger.error(f"  Import statements: {import_count}")
                        logger.error(f"  JSX elements: {jsx_count}")
                        logger.error(f"  This suggests the patch applied more than intended!")
                        
                        # Show first 20 lines to see what was applied
                        lines = file_content.splitlines()
                        logger.error(f"First 20 lines of file after patch:")
                        for i, line in enumerate(lines[:20]):
                            logger.error(f"  {i+1:2d}: {line}")
            except Exception as e:
                logger.error(f"Error checking file content after patch: {e}")
            
            return True, False  # Success, Not Already Applied
        else:
            logger.warning(f"Actual patch failed for hunk in {file_path} (exit code {patch_result.returncode}) after clean dry run.")
            return False, False
            
    except Exception as e:
        logger.error(f"Error applying single hunk: {str(e)}")
        return False, False
    finally:
        # Clean up the temporary file
        if 'temp_path' in locals() and os.path.exists(temp_path):
            os.unlink(temp_path)
            logger.debug(f"Removed temporary diff file {temp_path}")

def extract_hunk_from_diff(diff_content: str, hunk_id: int, line_adjustment: int = 0) -> Optional[str]:
    """
    Extract a single hunk from a diff based on its ID.
    
    Args:
        diff_content: The full diff content
        hunk_id: The ID of the hunk to extract
        line_adjustment: Optional adjustment to line numbers
        
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
                target_hunk = hunk.copy()  # Create a copy to avoid modifying the original
                break
        
        if not target_hunk:
            logger.warning(f"Hunk #{hunk_id} not found in diff")
            return None
        
        # Apply line number adjustment if provided
        if line_adjustment != 0:
            logger.info(f"Adjusting line numbers for hunk #{hunk_id} by {line_adjustment} lines")
            target_hunk['old_start'] += line_adjustment
            target_hunk['new_start'] += line_adjustment
            
            # CRITICAL FIX: Also update the header to reflect the adjustment
            old_count = target_hunk.get('old_count', len(target_hunk.get('old_block', [])))
            new_count = len(target_hunk.get('new_lines', []))
            adjusted_header = f"@@ -{target_hunk['old_start']},{old_count} +{target_hunk['new_start']},{new_count} @@"
            logger.info(f"Updated hunk header to: {adjusted_header}")
            
            # Update the header in the hunk data
            target_hunk['header'] = adjusted_header
        
        # Construct a new diff with just this hunk
        # Use the updated header if it was adjusted
        final_header = target_hunk.get('header', f"@@ -{target_hunk['old_start']},{len(target_hunk['old_block'])} +{target_hunk['new_start']},{len(target_hunk['new_lines'])} @@")
        logger.debug(f"Using final header: {final_header}")
        
        header_lines = [
            f"--- {source_path}",
            f"+++ {target_file}",
            final_header
        ]
        
        # Construct the content lines
        content_lines = []
        
        # Use the 'lines' field if available, which preserves the original diff structure
        if 'lines' in target_hunk and target_hunk['lines']:
            content_lines = target_hunk['lines']
        else:
            # Fallback: reconstruct from old_block and new_lines
            # This is a more complex reconstruction that tries to preserve order
            logger.warning(f"Hunk #{hunk_id} missing 'lines' field, attempting reconstruction")
            
            old_block = target_hunk.get('old_block', [])
            new_lines = target_hunk.get('new_lines', [])
            removed_lines = target_hunk.get('removed_lines', [])
            added_lines = target_hunk.get('added_lines', [])
            
            # Create a simple reconstruction by interleaving removed and added lines
            # This is not perfect but better than the previous approach
            old_idx = 0
            new_idx = 0
            
            # First add all removed lines
            for line in removed_lines:
                content_lines.append(f"-{line}")
            
            # Then add all added lines
            for line in added_lines:
                content_lines.append(f"+{line}")
            
            # Add context lines (lines that appear in both old and new)
            context_lines = []
            for line in old_block:
                if line in new_lines and line not in removed_lines:
                    context_lines.append(f" {line}")
            
            # Insert context lines at the beginning if they exist
            if context_lines:
                content_lines = context_lines + content_lines
        
        return '\n'.join(header_lines + content_lines)
        
    except Exception as e:
        logger.error(f"Error extracting hunk #{hunk_id} from diff: {str(e)}")
        return None
