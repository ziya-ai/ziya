"""
Pipeline manager for applying diffs.

This module provides the main entry point for the diff application pipeline,
coordinating the flow through system patch, git apply, difflib, and LLM resolver.
"""

import os
import re
import json
import subprocess
import tempfile
import uuid
from typing import Dict, List, Any, Optional, Tuple

from app.utils.logging_utils import logger
from ..core.exceptions import PatchApplicationError
from ..parsing.diff_parser import parse_unified_diff_exact_plus, extract_target_file_from_diff, split_combined_diff
from ..validation.validators import is_new_file_creation, is_hunk_already_applied, normalize_line_for_comparison
from ..file_ops.file_handlers import create_new_file, cleanup_patch_artifacts
from ..application.patch_apply import apply_diff_with_difflib, apply_diff_with_difflib_hybrid_forced, apply_diff_with_difflib_hybrid_forced_hunks
from ..application.git_diff import parse_patch_output

from .diff_pipeline import DiffPipeline, PipelineStage, HunkStatus, PipelineResult

def apply_diff_pipeline(git_diff: str, file_path: str, request_id: Optional[str] = None) -> Dict[str, Any]:
    """
    Apply a git diff using a structured pipeline approach.
    
    This function manages the flow through the various stages of diff application:
    1. System patch (using the patch command)
    2. Git apply (using git apply)
    3. Difflib (using Python's difflib)
    4. LLM resolver (for complex cases)
    
    Each hunk is tracked through the pipeline, with its status updated at each stage.
    
    Args:
        git_diff: The git diff to apply
        file_path: Path to the target file
        request_id: (Optional) request ID for tracking
        
    Returns:
        A dictionary with the result of the pipeline
    """
    logger.info("Starting diff application pipeline...")
    logger.debug("Original diff content:")
    logger.debug(git_diff)
    
    # Initialize the pipeline
    pipeline = DiffPipeline(file_path, git_diff)

    if not request_id:
        logger.warning("No request ID provided to pipeline, this should not happen")
        request_id = str(uuid.uuid4())

    pipeline.result.request_id = request_id
    logger.info(f"Pipeline initialized with request ID: {request_id}")
    pipeline.update_stage(PipelineStage.INIT)
    
    # Split combined diffs if present
    logger.debug(f"Original diff first 10 lines:\n{git_diff.splitlines()[:10]}")
    individual_diffs = split_combined_diff(git_diff)
    logger.debug(f"Number of individual diffs: {len(individual_diffs)}")
    if len(individual_diffs) > 0:
        logger.debug(f"First individual diff first 10 lines:\n{individual_diffs[0].splitlines()[:10]}")
    
    if len(individual_diffs) > 1:
        # Find the diff that matches our target file
        # Compare using basename to handle full paths vs relative paths
        target_basename = os.path.basename(file_path)
        matching_diff = None
        
        for diff in individual_diffs:
            diff_target = extract_target_file_from_diff(diff)
            if diff_target:
                # Try exact match first
                if diff_target == file_path or diff_target == target_basename:
                    matching_diff = diff
                    break
                # Try basename match
                elif os.path.basename(diff_target) == target_basename:
                    matching_diff = diff
                    break
        
        if matching_diff:
            logger.debug(f"Found matching diff for target file: {file_path}")
            git_diff = matching_diff
            pipeline.current_diff = git_diff
        else:
            logger.warning(f"No matching diff found for target file: {file_path}")
            logger.debug(f"Available diff targets: {[extract_target_file_from_diff(d) for d in individual_diffs]}")
    
    # Get the base directory
    user_codebase_dir = os.environ.get("ZIYA_USER_CODEBASE_DIR")
    if not user_codebase_dir:
        error = "ZIYA_USER_CODEBASE_DIR environment variable is not set"
        logger.error(error)
        pipeline.complete(error=error)
        return pipeline.result.to_dict()
    
    # Extract target file path if not provided
    if not file_path:
        for line in git_diff.splitlines():
            if line.startswith('+++ b/'):
                file_path = os.path.join(user_codebase_dir, line[6:])
                break
            elif line.startswith('diff --git'):
                _, _, path = line.partition(' b/')
                file_path = os.path.join(user_codebase_dir, path)
                pipeline.file_path = file_path
                pipeline.result.file_path = file_path
                break
        
        if not file_path:
            error = "Could not determine target file path"
            logger.error(error)
            pipeline.complete(error=error)
            return pipeline.result.to_dict()
    
    # Handle new file creation
    diff_lines = git_diff.splitlines()
    if is_new_file_creation(diff_lines):
        try:
            create_new_file(git_diff, user_codebase_dir)
            cleanup_patch_artifacts(user_codebase_dir, file_path)
            
            # Create a synthetic hunk for the file creation to track success
            synthetic_hunk = {
                'number': 1,
                'old_start': 0,
                'old_count': 0,
                'new_start': 1,
                'new_count': len([line for line in diff_lines if line.startswith('+') and not line.startswith('+++')]),
                'header': '@@ -0,0 +1,{} @@'.format(len([line for line in diff_lines if line.startswith('+') and not line.startswith('+++')])),
                'old_block': [],
                'new_lines': [line[1:] for line in diff_lines if line.startswith('+') and not line.startswith('+++')]
            }
            
            # Initialize the pipeline with the synthetic hunk
            pipeline.initialize_hunks([synthetic_hunk])
            pipeline.update_hunk_status(1, PipelineStage.SYSTEM_PATCH, HunkStatus.SUCCEEDED)
            
            pipeline.result.changes_written = True
            pipeline.complete()
            return pipeline.result.to_dict()
        except Exception as e:
            error = f"Error creating new file: {str(e)}"
            logger.error(error)
            pipeline.complete(error=error)
            return pipeline.result.to_dict()
    
    # Parse the hunks to track
    try:
        logger.debug(f"Before parsing hunks, git_diff first 10 lines:\n{git_diff.splitlines()[:10]}")
        hunks = list(parse_unified_diff_exact_plus(git_diff, file_path))
        pipeline.initialize_hunks(hunks)
        
        # Check for whitespace-only changes
        from ..application.whitespace_handler import is_whitespace_only_diff
        whitespace_only_hunks = []
        for i, h in enumerate(hunks):
            if is_whitespace_only_diff(h):
                whitespace_only_hunks.append(i+1)  # 1-based indexing for hunk IDs
        
        if whitespace_only_hunks:
            logger.info(f"Detected whitespace-only changes in hunks: {whitespace_only_hunks}")
            # For whitespace-only changes, we'll use a special flag
            os.environ['ZIYA_WHITESPACE_HUNKS'] = ','.join(map(str, whitespace_only_hunks))
    except Exception as e:
        error = f"Error parsing diff: {str(e)}"
        logger.error(error)
        pipeline.complete(error=error)
        return pipeline.result.to_dict()
    
    # Read original content before any modifications
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            original_content = f.read()
            original_lines = original_content.splitlines(True)  # Keep line endings
    except FileNotFoundError:
        original_content = ""
        original_lines = []
    
    # Check if file exists before attempting patch
    if not os.path.exists(file_path) and not is_new_file_creation(diff_lines):
        error = f"Target file does not exist: {file_path}"
        mark_all_hunks_as_failed(pipeline, error, "file_not_found")
        logger.error(error)
        pipeline.complete(error=error)
        return pipeline.result.to_dict()
    
    # If force difflib flag is set, skip system patch and git apply
    if os.environ.get('ZIYA_FORCE_DIFFLIB'):
        logger.info("Force difflib mode enabled, bypassing system patch and git apply")
        pipeline.update_stage(PipelineStage.DIFFLIB)
        difflib_result = run_difflib_stage(pipeline, file_path, git_diff, original_lines)
        
        # Complete the pipeline and return the proper result dictionary
        if difflib_result:
            pipeline.result.changes_written = True
        
        # Set the final status based on hunk results
        if all(tracker.status in (HunkStatus.SUCCEEDED, HunkStatus.ALREADY_APPLIED) 
               for tracker in pipeline.result.hunks.values()):
            pipeline.result.status = "success"
        elif any(tracker.status == HunkStatus.SUCCEEDED 
                for tracker in pipeline.result.hunks.values()):
            pipeline.result.status = "partial"
        else:
            pipeline.result.status = "error"
            
        pipeline.complete()
        return pipeline.result.to_dict()
    
    # Stage 1: System Patch
    pipeline.update_stage(PipelineStage.SYSTEM_PATCH)
    system_patch_result = run_system_patch_stage(pipeline, user_codebase_dir, git_diff)
    
    # Try applying hunks separately that matched but weren't applied due to all-or-nothing behavior
    from ..application.separate_hunk_apply import try_separate_hunks
    
    # Find hunks that could be applied separately
    separate_hunks = [
        hunk_id for hunk_id, tracker in pipeline.result.hunks.items()
        if tracker.hunk_data.get("could_apply_separately", False) and 
        tracker.status == HunkStatus.PENDING
    ]
    
    if separate_hunks:
        logger.info(f"Found {len(separate_hunks)} hunks that could be applied separately")
        # Add all pending hunks to the separate_hunks list to ensure they're all tried
        for hunk_id, tracker in pipeline.result.hunks.items():
            if tracker.status == HunkStatus.PENDING and hunk_id not in separate_hunks:
                separate_hunks.append(hunk_id)
                logger.info(f"Added hunk #{hunk_id} to separate_hunks list to ensure sequential application")
        
        separate_result = try_separate_hunks(pipeline, user_codebase_dir, separate_hunks)
        if separate_result:
            logger.info("Successfully applied some hunks separately")
            system_patch_result = True  # Update the result to indicate some changes were written
    
    # If all hunks succeeded or were already applied, we're done
    if all(tracker.status in (HunkStatus.SUCCEEDED, HunkStatus.ALREADY_APPLIED) 
           for tracker in pipeline.result.hunks.values()):
        pipeline.result.status = "success"
        # CRITICAL FIX: Only mark changes as written if system patch actually succeeded
        # System patch has all-or-nothing behavior - if any hunk fails, no changes are written
        if system_patch_result:
            pipeline.result.changes_written = True
        pipeline.complete()
        return pipeline.result.to_dict()
    
    # Stage 2: Git Apply (for hunks that failed in system patch)
    pipeline.update_stage(PipelineStage.GIT_APPLY)
    remaining_diff = pipeline.extract_remaining_hunks()
    
    if not remaining_diff.strip():
        logger.warning("No valid hunks remaining to process")
        if pipeline.result.changes_written or pipeline.result.succeeded_hunks:
            pipeline.complete()
            return pipeline.result.to_dict()
        else:
            pipeline.complete(error="No hunks were applied")
            return pipeline.result.to_dict()
    
    # Check for problematic patterns that git apply handles poorly
    if "\\ No newline at end of file" in remaining_diff:
        logger.info("Detected 'No newline at end of file' pattern - skipping git apply due to known issues")
        # Mark all pending hunks as failed so they go to difflib
        for hunk_id, tracker in pipeline.result.hunks.items():
            if tracker.status == HunkStatus.PENDING:
                pipeline.update_hunk_status(
                    hunk_id=hunk_id,
                    stage=PipelineStage.GIT_APPLY,
                    status=HunkStatus.FAILED,
                    error_details={"error": "skipped due to newline at EOF issue"}
                )
        git_apply_result = False
    else:
        git_apply_result = run_git_apply_stage(pipeline, user_codebase_dir, remaining_diff)
    
    # If all hunks succeeded or were already applied, we're done
    if all(tracker.status in (HunkStatus.SUCCEEDED, HunkStatus.ALREADY_APPLIED) 
           for tracker in pipeline.result.hunks.values()):
        pipeline.result.status = "success"
        # CRITICAL FIX: Only mark changes as written if git apply actually succeeded
        if git_apply_result:
            pipeline.result.changes_written = True
        pipeline.complete()
        return pipeline.result.to_dict()
    
    # Stage 3: Difflib (for hunks that failed in git apply)
    pipeline.update_stage(PipelineStage.DIFFLIB)
    
    # CRITICAL FIX: Reset all failed hunks to pending so they can be processed by difflib
    # This ensures that hunks that failed in git_apply are still attempted with difflib
    reset_count = pipeline.reset_failed_hunks_to_pending()
    if reset_count > 0:
        logger.info(f"Reset {reset_count} failed hunks to pending for difflib stage")
    
    # Now extract the remaining hunks (which should include the reset failed hunks)
    remaining_diff = pipeline.extract_remaining_hunks()
    
    if not remaining_diff.strip():
        logger.warning("No valid hunks remaining to process after git apply")
        if pipeline.result.changes_written or pipeline.result.succeeded_hunks:
            pipeline.complete()
            return pipeline.result.to_dict()
        else:
            pipeline.complete(error="No hunks were applied")
            return pipeline.result.to_dict()
    
    # Read the current content after previous stages
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            current_content = f.read()
            current_lines = current_content.splitlines(True)  # Keep line endings
    except FileNotFoundError:
        current_content = ""
        current_lines = []
    
    # Check if content has changed from original
    content_changed = current_content != original_content
    if content_changed:
        pipeline.result.changes_written = True
    
    difflib_result = run_difflib_stage(pipeline, file_path, remaining_diff, current_lines)
    
    # Stage 4: LLM Resolver (stub for now)
    # This would be implemented in the future to handle complex cases
    
    # Update all hunks to FAILED if they're still PENDING at the end
    for hunk_id, tracker in pipeline.result.hunks.items():
        if tracker.status == HunkStatus.PENDING:
            pipeline.update_hunk_status(
                hunk_id=hunk_id,
                stage=pipeline.current_stage,
                status=HunkStatus.FAILED,
                error_details={"error": "Failed to apply hunk in all stages"}
            )
    
    # If no hunks succeeded and no changes were written, set the error
    if not pipeline.result.succeeded_hunks and not pipeline.result.already_applied_hunks and not pipeline.result.changes_written:
        pipeline.complete(error="Failed to apply changes: all hunks failed")
        pipeline.result.status = "error"
    else:
        # If we have a mix of succeeded and failed hunks, explicitly set partial status
        if pipeline.result.succeeded_hunks and pipeline.result.failed_hunks:
            logger.info("Mixed results: some hunks succeeded, some failed")
            pipeline.result.status = "partial"
        elif pipeline.result.succeeded_hunks or pipeline.result.already_applied_hunks:
            logger.info("All processed hunks succeeded")
            pipeline.result.status = "success"
        pipeline.complete()
    
    # Add detailed debug logging about hunk statuses
    logger.info("=== PIPELINE COMPLETION SUMMARY ===")
    logger.info(f"File: {file_path}")
    logger.info(f"Total hunks: {len(pipeline.result.hunks)}")
    logger.info(f"Succeeded hunks: {pipeline.result.succeeded_hunks}")
    logger.info(f"Failed hunks: {pipeline.result.failed_hunks}")
    logger.info(f"Already applied hunks: {pipeline.result.already_applied_hunks}")
    logger.info(f"Pending hunks: {pipeline.result.pending_hunks}")
    logger.info(f"Changes written: {pipeline.result.changes_written}")
    
    # Log detailed status for each hunk
    logger.info("=== DETAILED HUNK STATUS ===")
    for hunk_id, tracker in pipeline.result.hunks.items():
        logger.info(f"Hunk #{hunk_id}: Status={tracker.status.value}, Stage={tracker.current_stage.value}")
        if tracker.error_details:
            logger.info(f"  Error details: {tracker.error_details}")
    
    # Get the final result dictionary directly from the PipelineResult object
    # This dictionary now includes the correctly determined status and message
    final_result_dict = pipeline.result.to_dict()

    
    # Log the final result being returned
    logger.info(f"Final result status: {final_result_dict.get('status')}")
    logger.info(f"Final result message: {final_result_dict.get('message')}")
    
    # Add safe JSON serialization
    try:
        details_json = json.dumps(final_result_dict.get('details', {}), indent=2)
        logger.info(f"Final result details: {details_json}")
    except TypeError as e:
        logger.error(f"Error serializing result details: {e}")
        # Create a simplified version that's guaranteed to be serializable
        simplified_details = {
            "succeeded": len(final_result_dict.get('succeeded', [])),
            "failed": len(final_result_dict.get('failed', [])),
            "already_applied": len(final_result_dict.get('already_applied', [])),
            "changes_written": final_result_dict.get('changes_written', False),
            "request_id": final_result_dict.get('request_id', ''),
            "error": str(final_result_dict.get('error', ''))
        }
        logger.info(f"Simplified result details: {json.dumps(simplified_details, indent=2)}")
        
    return final_result_dict

def run_system_patch_stage(pipeline: DiffPipeline, user_codebase_dir: str, git_diff: str) -> bool:
    """
    Run the system patch stage of the pipeline.
    
    Args:
        pipeline: The diff pipeline
        user_codebase_dir: The base directory of the user's codebase
        git_diff: The git diff to apply
        
    Returns:
        True if any changes were written, False otherwise
    """
    logger.info("Starting system patch stage...")

     # Log the exact input being passed to patch
    logger.debug(f"Running patch command (dry-run): {' '.join(['patch', '-p1', '--forward', '--no-backup-if-mismatch', '--reject-file=-', '--batch', '--ignore-whitespace', '--verbose', '--dry-run', '-i', '-'])}")
    logger.debug(f"Patch input string (repr):\n{repr(git_diff)}") # Log with repr to see hidden chars/newlines
    logger.debug("--- End Patch Input ---")
    
    # Do a dry run to see what we're up against
    try:
        patch_command_dry = ['patch', '-p1', '--forward', '--no-backup-if-mismatch', '--reject-file=-', '--batch', '--ignore-whitespace', '--verbose', '--dry-run', '-i', '-']
        logger.debug(f"Running patch command (dry-run): {' '.join(patch_command_dry)}")
        logger.debug(f"Patch input string (repr):\n{repr(git_diff)}") # Log with repr to see hidden chars/newlines
        logger.debug("--- End Patch Input ---")

        command_to_run_dry = patch_command_dry
        patch_result = subprocess.run(
            command_to_run_dry,
            input=git_diff,
            encoding='utf-8',
            cwd=user_codebase_dir,
            capture_output=True,
            text=True,
            timeout=10
        )
        
        logger.debug(f"Patch dry run stdout: {patch_result.stdout}")
        logger.debug(f"Patch dry run stderr: {patch_result.stderr}")
        logger.debug(f"Patch dry run return code: {patch_result.returncode}")
        
        # Parse the dry run output
        dry_run_status = parse_patch_output(patch_result.stdout, patch_result.stderr)
        
        # Check if any hunks need verification due to "Reversed (or previously applied)" message
        needs_verification = False
        for hunk_id, status in dry_run_status.items():
            if status.get("status") == "needs_verification":
                needs_verification = True
                break
                
        # If any hunks need verification, we need to do additional checks
        if needs_verification:
            logger.info("Detected 'Reversed (or previously applied)' message, performing additional verification")
            verify_hunks_with_file_content(pipeline, dry_run_status)
        
        # Check if all changes are already applied
        # We need to check both stdout and stderr for errors
        has_malformed_error = "malformed patch" in patch_result.stderr
        has_failure = "failed" in patch_result.stdout.lower() or patch_result.returncode != 0
        
        already_applied = (
            "No file to patch" not in patch_result.stdout and 
            "Reversed (or previously applied)" in patch_result.stdout and
            not has_failure and
            not has_malformed_error and
            all(status.get("status") == "already_applied" for status in dry_run_status.values())
        )
        
        if already_applied:
            logger.info("All changes are already applied")
            for hunk_id in pipeline.result.hunks:
                pipeline.update_hunk_status(
                    hunk_id=hunk_id,
                    stage=PipelineStage.SYSTEM_PATCH,
                    status=HunkStatus.ALREADY_APPLIED
                )
            return False
        
        # Update hunk statuses based on dry run
        for hunk_id, status_info in dry_run_status.items():
            status_value = status_info.get("status")
            if status_value == "already_applied":
                pipeline.update_hunk_status(
                    hunk_id=hunk_id,
                    stage=PipelineStage.SYSTEM_PATCH,
                    status=HunkStatus.ALREADY_APPLIED,
                    position=status_info.get("position")
                )
            elif status_value == "succeeded":
                pipeline.update_hunk_status(
                    hunk_id=hunk_id,
                    stage=PipelineStage.SYSTEM_PATCH,
                    status=HunkStatus.PENDING  # Will be updated after actual patch
                )
            elif status_value == "needs_verification":
                # After verification, this should have been updated to either succeeded or already_applied
                # If it's still needs_verification, mark it as failed to try other methods
                pipeline.update_hunk_status(
                    hunk_id=hunk_id,
                    stage=PipelineStage.SYSTEM_PATCH,
                    status=HunkStatus.FAILED,
                    error_details={"error": "Verification needed but not completed"}
                )
            else:
                pipeline.update_hunk_status(
                    hunk_id=hunk_id,
                    stage=PipelineStage.SYSTEM_PATCH,
                    status=HunkStatus.FAILED,
                    error_details={"error": "Failed in dry run"}
                )
        
        # If no hunks succeeded in dry run, skip the actual patch
        if not any(status_info.get("status") == "succeeded" for status_info in dry_run_status.values()):
            logger.info("No hunks succeeded in dry run, skipping actual patch")
            return False
        
        # Apply the patch for real
        logger.info(f"Applying {sum(1 for v in dry_run_status.values() if v.get('status') == 'succeeded')}/{len(dry_run_status)} hunks with system patch...")
        patch_command_apply = ['patch', '-p1', '--forward', '--no-backup-if-mismatch', '--reject-file=-', '--batch', '--ignore-whitespace', '--verbose', '-i', '-']
        command_to_run_apply = patch_command_apply
        patch_result = subprocess.run(
            command_to_run_apply,
            input=git_diff,
            encoding='utf-8',
            cwd=user_codebase_dir,
            capture_output=True,
            text=True,
            timeout=10
        )
        
        logger.debug(f"Patch stdout: {patch_result.stdout}")
        logger.debug(f"Patch stderr: {patch_result.stderr}")
        logger.debug(f"Patch return code: {patch_result.returncode}")
        
        # Handle misordered hunks
        if "misordered hunks" in patch_result.stderr:
            logger.warning("Patch reported misordered hunks - marking all hunks as failed")
            for hunk_id in pipeline.result.hunks:
                pipeline.update_hunk_status(
                    hunk_id=hunk_id,
                    stage=PipelineStage.SYSTEM_PATCH,
                    status=HunkStatus.FAILED,
                    error_details={"error": "misordered hunks"}
                )
            return False
        
        # Parse the actual patch output
        patch_status = parse_patch_output(patch_result.stdout, patch_result.stderr)
        
        # Update hunk statuses based on actual patch
        all_hunks_succeeded = True
        any_hunk_succeeded = False
        
        for hunk_id, status_info in patch_status.items():
            status_value = status_info.get("status")
            
            if status_value == "succeeded":
                # CRITICAL FIX: Don't mark hunks as SUCCEEDED if the overall patch failed
                # Instead, mark them as PENDING with a flag so they can be tried in later stages
                if patch_result.returncode != 0:
                    # This hunk matched but wasn't actually applied due to all-or-nothing behavior
                    pipeline.update_hunk_status(
                        hunk_id=hunk_id,
                        stage=PipelineStage.SYSTEM_PATCH,
                        status=HunkStatus.PENDING,  # Keep as PENDING so it's tried in later stages
                        position=status_info.get("position"),
                        confidence=1.0 - (status_info.get("fuzz", 0) * 0.1)
                    )
                    # Add a flag to indicate this hunk could have been applied separately
                    pipeline.result.hunks[hunk_id].hunk_data["could_apply_separately"] = True
                    logger.info(f"Hunk #{hunk_id} matched in system patch but kept as PENDING for later stages due to all-or-nothing behavior")
                else:
                    # Normal case - patch succeeded overall
                    pipeline.update_hunk_status(
                        hunk_id=hunk_id,
                        stage=PipelineStage.SYSTEM_PATCH,
                        status=HunkStatus.SUCCEEDED,
                        position=status_info.get("position"),
                        confidence=1.0 - (status_info.get("fuzz", 0) * 0.1)
                    )
                any_hunk_succeeded = True
            elif status_value == "already_applied":
                pipeline.update_hunk_status(
                    hunk_id=hunk_id,
                    stage=PipelineStage.SYSTEM_PATCH,
                    status=HunkStatus.ALREADY_APPLIED,
                    position=status_info.get("position")
                )
            else:  # failed
                error_details = {
                    "error": status_info.get("error", "application_failed"),
                    "details": status_info.get("details", "Failed to apply hunk")
                }
                pipeline.update_hunk_status(
                    hunk_id=hunk_id,
                    stage=PipelineStage.SYSTEM_PATCH,
                    status=HunkStatus.FAILED,
                    error_details=error_details
                )
                all_hunks_succeeded = False
        
        # CRITICAL FIX: Only mark changes as written if ALL hunks succeeded
        # System patch has all-or-nothing behavior - if any hunk fails, no changes are written
        changes_written = all_hunks_succeeded and any_hunk_succeeded and patch_result.returncode == 0
        
        if any_hunk_succeeded and not all_hunks_succeeded:
            logger.warning("Some hunks succeeded but others failed in system patch. Due to all-or-nothing behavior, NO changes were written.")
            logger.info("These hunks will be tried again in later stages.")
        
        pipeline.result.changes_written = changes_written
        return changes_written
        
    except subprocess.TimeoutExpired:
        logger.error("Patch command timed out")
        for hunk_id in pipeline.result.hunks:
            pipeline.update_hunk_status(
                hunk_id=hunk_id,
                stage=PipelineStage.SYSTEM_PATCH,
                status=HunkStatus.FAILED,
                error_details={"error": "timeout"}
            )
        return False
    except Exception as e:
        logger.error(f"Error in system patch stage: {str(e)}")
        for hunk_id in pipeline.result.hunks:
            pipeline.update_hunk_status(
                hunk_id=hunk_id,
                stage=PipelineStage.SYSTEM_PATCH,
                status=HunkStatus.FAILED,
                error_details={"error": str(e)}
            )
            return False
        return False

def mark_all_hunks_as_failed(pipeline: DiffPipeline, error_message: str, error_type: str = "application_failed") -> None:
    """
    Mark all hunks in the pipeline as failed with the given error message.
    
    Args:
        pipeline: The diff pipeline
        error_message: The error message to set
        error_type: The type of error that occurred
    """
    logger.info(f"Marking all hunks as failed: {error_message} (type: {error_type})")
    for hunk_id in pipeline.result.hunks:
        pipeline.update_hunk_status(
            hunk_id=hunk_id,
            stage=pipeline.current_stage,
            status=HunkStatus.FAILED,
            error_details={"error": error_type, "details": error_message}
        )

def run_git_apply_stage(pipeline: DiffPipeline, user_codebase_dir: str, git_diff: str) -> bool:
    """
    Run the git apply stage of the pipeline.
    
    Args:
        pipeline: The diff pipeline
        user_codebase_dir: The base directory of the user's codebase
        git_diff: The git diff to apply
        
    Returns:
        True if any changes were written, False otherwise
    """
    logger.info("Starting git apply stage...")
    
    if not git_diff.strip():
        logger.warning("Empty diff, skipping git apply stage")
        return False
    
    # Create a temporary file for the diff
    try:
        # Normalize the diff using whatthepatch before writing to file
        from ..application.git_diff import normalize_patch_with_whatthepatch, debug_patch_issues
        logger.info("Normalizing diff with whatthepatch before git apply")
        normalized_diff = normalize_patch_with_whatthepatch(git_diff)
        debug_patch_issues(normalized_diff)
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', suffix='.diff', delete=False) as temp_file:
            temp_file.write(normalized_diff)
            temp_path = temp_file.name
        
        # Do a dry run with git apply --check
        git_check_result = subprocess.run(
            ['git', 'apply', '--verbose', '--ignore-whitespace',
             '--ignore-space-change', '--whitespace=nowarn',
             '--check', temp_path],
            cwd=user_codebase_dir,
            capture_output=True,
            text=True
        )
        
        logger.debug(f"Git apply --check stdout: {git_check_result.stdout}")
        logger.debug(f"Git apply --check stderr: {git_check_result.stderr}")
        logger.debug(f"Git apply --check return code: {git_check_result.returncode}")
        
        # If check fails, mark all hunks as failed
        if git_check_result.returncode != 0:
            logger.warning("Git apply --check failed, marking all pending hunks as failed")
            for hunk_id, tracker in pipeline.result.hunks.items():
                if tracker.status == HunkStatus.PENDING:
                    pipeline.update_hunk_status(
                        hunk_id=hunk_id,
                        stage=PipelineStage.GIT_APPLY,
                        status=HunkStatus.FAILED,
                        error_details={"error": git_check_result.stderr}
                    )
            return False
        
        # Apply the diff with git apply
        git_result = subprocess.run(
            ['git', 'apply', '--verbose', '--ignore-whitespace',
             '--ignore-space-change', '--whitespace=nowarn',
             '--reject', temp_path],
            cwd=user_codebase_dir,
            capture_output=True,
            text=True
        )
        
        logger.debug(f"Git apply stdout: {git_result.stdout}")
        logger.debug(f"Git apply stderr: {git_result.stderr}")
        logger.debug(f"Git apply return code: {git_result.returncode}")
        
        # Parse the git apply output to determine which hunks succeeded
        changes_written = False
        
        if git_result.returncode == 0:
            logger.info("Git apply succeeded")
            # Check if git apply reports clean application
            if "Applied patch" in git_result.stderr and "cleanly" in git_result.stderr:
                logger.info("Git apply reports clean application - marking all hunks as succeeded")
                # Mark all hunks as succeeded when git apply cleanly applies the entire patch
                for hunk_id, tracker in pipeline.result.hunks.items():
                    if tracker.status in (HunkStatus.PENDING, HunkStatus.FAILED):
                        pipeline.update_hunk_status(
                            hunk_id=hunk_id,
                            stage=PipelineStage.GIT_APPLY,
                            status=HunkStatus.SUCCEEDED
                        )
            else:
                # Only mark pending hunks as succeeded for partial success
                for hunk_id, tracker in pipeline.result.hunks.items():
                    if tracker.status == HunkStatus.PENDING:
                        pipeline.update_hunk_status(
                            hunk_id=hunk_id,
                            stage=PipelineStage.GIT_APPLY,
                            status=HunkStatus.SUCCEEDED
                        )
            changes_written = True
        elif "already applied" in git_result.stderr:
            logger.info("Some hunks were already applied")
            # Parse which hunks were already applied
            for hunk_id, tracker in pipeline.result.hunks.items():
                if tracker.status == HunkStatus.PENDING:
                    if str(hunk_id) in git_result.stderr and "already applied" in git_result.stderr:
                        pipeline.update_hunk_status(
                            hunk_id=hunk_id,
                            stage=PipelineStage.GIT_APPLY,
                            status=HunkStatus.ALREADY_APPLIED
                        )
                    else:
                        pipeline.update_hunk_status(
                            hunk_id=hunk_id,
                            stage=PipelineStage.GIT_APPLY,
                            status=HunkStatus.FAILED,
                            error_details={"error": "not applied"}
                        )
        else:
            logger.warning("Git apply failed")
            # Mark all pending hunks as failed
            for hunk_id, tracker in pipeline.result.hunks.items():
                if tracker.status == HunkStatus.PENDING:
                    pipeline.update_hunk_status(
                        hunk_id=hunk_id,
                        stage=PipelineStage.GIT_APPLY,
                        status=HunkStatus.FAILED,
                        error_details={"error": git_result.stderr}
                    )
        
        pipeline.result.changes_written = pipeline.result.changes_written or changes_written
        return changes_written
        
    except Exception as e:
        logger.error(f"Error in git apply stage: {str(e)}")
        for hunk_id, tracker in pipeline.result.hunks.items():
            if tracker.status == HunkStatus.PENDING:
                pipeline.update_hunk_status(
                    hunk_id=hunk_id,
                    stage=PipelineStage.GIT_APPLY,
                    status=HunkStatus.FAILED,
                    error_details={"error": str(e)}
                )
        return False
    finally:
        # Clean up the temporary file
        if 'temp_path' in locals() and os.path.exists(temp_path):
            os.unlink(temp_path)

def update_merged_hunk_status(pipeline, merged_hunk_mapping, new_hunk_index, status, stage, error_details=None):
    """
    Update the status of all original hunks that were merged into a single new hunk.
    
    Args:
        pipeline: The pipeline instance
        merged_hunk_mapping: Dictionary mapping new hunk indices to lists of original hunk indices
        new_hunk_index: Index of the new merged hunk (0-based)
        status: Status to set for all original hunks
        stage: Pipeline stage
        error_details: Optional error details
    """
    if new_hunk_index in merged_hunk_mapping:
        original_indices = merged_hunk_mapping[new_hunk_index]
        logger.info(f"Updating status for merged hunk {new_hunk_index}: original hunks {original_indices} -> {status}")
        
        # Get the list of pending hunks to map indices to actual hunk IDs
        original_hunk_ids = sorted(pipeline.result.hunks.keys())
        pending_hunks = [hunk_id for hunk_id in original_hunk_ids 
                        if pipeline.result.hunks[hunk_id].status == HunkStatus.PENDING]
        
        for orig_idx in original_indices:
            if orig_idx < len(pending_hunks):
                hunk_id = pending_hunks[orig_idx]
                pipeline.update_hunk_status(
                    hunk_id=hunk_id,
                    stage=stage,
                    status=status,
                    error_details=error_details
                )
                logger.info(f"Updated original hunk #{hunk_id} (index {orig_idx}) to {status}")


def run_difflib_stage(pipeline: DiffPipeline, file_path: str, git_diff: str, original_lines: List[str]) -> bool:
    """
    Run the difflib stage of the pipeline.
    
    Args:
        pipeline: The diff pipeline
        file_path: Path to the file to modify
        git_diff: The git diff to apply
        original_lines: The original file content as a list of lines
        
    Returns:
        True if any changes were written, False otherwise
    """
    from ..core.config import get_context_size, get_search_radius, get_confidence_threshold
    
    # Set environment variables for difflib application if needed
    if os.environ.get('ZIYA_DIFF_CONTEXT_SIZE') is None:
        # Use a larger context size for difflib mode to improve matching
        os.environ['ZIYA_DIFF_CONTEXT_SIZE'] = str(get_context_size('large'))
    
    if os.environ.get('ZIYA_DIFF_SEARCH_RADIUS') is None:
        # Use a larger search radius for difflib mode to improve matching
        os.environ['ZIYA_DIFF_SEARCH_RADIUS'] = str(get_search_radius() * 2)
    
    logger.info("Starting difflib stage...")
    
    if not git_diff.strip():
        logger.warning("Empty diff, skipping difflib stage")
        return False
    
    # Store the original content for later comparison
    original_content = ''.join(original_lines)
    
    try:
        # Parse the hunks
        hunks = list(parse_unified_diff_exact_plus(git_diff, file_path))
        logger.info(f"Parsed {len(hunks)} hunks for difflib stage")
        
        # Fix overlapping hunks to prevent data corruption
        from ..application.overlapping_hunks_fix import fix_overlapping_hunks
        original_hunk_count = len(hunks)
        original_hunks = hunks.copy()  # Keep original hunks for reference
        
        # Pass the original file content to ensure correct old_block reconstruction
        original_file_content = ''.join(original_lines)
        hunks = fix_overlapping_hunks(hunks, original_file_content)
        
        # Track which original hunks were merged into which new hunks
        merged_hunk_mapping = {}  # new_hunk_index -> list of original hunk indices
        if len(hunks) != original_hunk_count:
            logger.info(f"Overlapping hunks fix: {original_hunk_count} -> {len(hunks)} hunks")
            
            # Build mapping from merged hunks back to original hunks
            for new_idx, hunk in enumerate(hunks):
                if 'merged_from' in hunk:
                    merged_hunk_mapping[new_idx] = hunk['merged_from']
                    logger.debug(f"Merged hunk {new_idx} contains original hunks: {hunk['merged_from']}")
                else:
                    # Find which original hunk this corresponds to
                    for orig_idx, orig_hunk in enumerate(original_hunks):
                        if (hunk['old_start'] == orig_hunk['old_start'] and 
                            hunk['old_count'] == orig_hunk['old_count']):
                            merged_hunk_mapping[new_idx] = [orig_idx]
                            break
        
        # CRITICAL FIX: Check for malformed hunks first
        malformed_hunks = []
        for i, hunk in enumerate(hunks, 1):
            hunk_id = hunk.get('number', i)
            
            # Check if the hunk is malformed
            if 'header' in hunk and '@@ -' in hunk['header']:
                header_match = re.match(r'^@@ -(\d+),(\d+) \+(\d+),(\d+) @@', hunk['header'])
                if not header_match:
                    logger.warning(f"Malformed hunk header detected: {hunk['header']}")
                    malformed_hunks.append(hunk_id)
                    continue
            
            # Check if essential hunk data is missing
            # Note: old_block can be an empty list for empty files, so check for key existence
            if 'old_block' not in hunk or 'new_lines' not in hunk:
                logger.warning(f"Malformed hunk detected: missing old_block or new_lines keys")
                malformed_hunks.append(hunk_id)
                continue
            
            # Also check that new_lines is not None (empty list is OK for deletions)
            if hunk.get('new_lines') is None:
                logger.warning(f"Malformed hunk detected: new_lines is None")
                malformed_hunks.append(hunk_id)
                continue
        
        # If any hunks are malformed, mark them as failed and return
        if malformed_hunks:
            logger.warning(f"Found {len(malformed_hunks)} malformed hunks, marking as failed")
            for hunk_id in malformed_hunks:
                pipeline.update_hunk_status(
                    hunk_id=hunk_id,
                    stage=PipelineStage.DIFFLIB,
                    status=HunkStatus.FAILED,
                    error_details={"error": "Malformed hunk detected"}
                )
            return False
        
        # Check if all hunks are already applied        
        all_hunks_found_applied = True # Assume true initially
        already_applied_hunks = []
        
        # Map hunk numbers to their IDs in the pipeline
        # After overlapping hunks fix, we need to account for merged hunks
        hunk_id_mapping = {}

        # Get the original hunk IDs in order
        original_hunk_ids = sorted(pipeline.result.hunks.keys())

        # Create a list of pending hunks (those that still need processing)
        pending_hunks = [hunk_id for hunk_id in original_hunk_ids 
                        if pipeline.result.hunks[hunk_id].status == HunkStatus.PENDING]

        # Log the pending hunks for debugging
        logger.info(f"Pending hunks that need processing: {pending_hunks}")

        # Create mapping from current sequential index to original hunk ID
        # This handles both normal hunks and merged hunks
        if merged_hunk_mapping:
            # We have merged hunks, so we need to create a more complex mapping
            logger.info(f"Creating hunk ID mapping with merged hunks: {merged_hunk_mapping}")
            
            for new_idx, original_indices in merged_hunk_mapping.items():
                # For merged hunks, we'll use the first original hunk's ID as the primary ID
                # but we need to mark all the original hunks as handled
                primary_original_idx = original_indices[0]
                if primary_original_idx < len(pending_hunks):
                    primary_hunk_id = pending_hunks[primary_original_idx]
                    hunk_id_mapping[new_idx + 1] = primary_hunk_id  # +1 because enumerate starts at 1
                    
                    # Mark all the merged original hunks as handled by this new hunk
                    for orig_idx in original_indices:
                        if orig_idx < len(pending_hunks):
                            orig_hunk_id = pending_hunks[orig_idx]
                            # We'll handle the status updates for merged hunks later
                            logger.debug(f"Original hunk {orig_hunk_id} (index {orig_idx}) merged into new hunk {new_idx + 1}")
                else:
                    # Fallback if we can't find the original hunk
                    hunk_id_mapping[new_idx + 1] = pending_hunks[0] if pending_hunks else new_idx + 1
        else:
            # No merged hunks, use the original logic
            if len(pending_hunks) == len(hunks):
                # If the number of pending hunks matches the number of hunks in the diff,
                # we can map them directly
                for i, hunk_id in enumerate(pending_hunks, 1):
                    hunk_id_mapping[i] = hunk_id
            else:
                # If there's a mismatch in the number of hunks, log a warning
                # and fall back to a best-effort mapping
                logger.warning(f"Mismatch between pending hunks ({len(pending_hunks)}) and hunks in diff ({len(hunks)})")
                
                # Try to match hunks by content if possible
                for i, hunk in enumerate(hunks, 1):
                    if i <= len(pending_hunks):
                        hunk_id_mapping[i] = pending_hunks[i-1]
                    else:
                        # If we run out of pending hunks, use a fallback
                        # Find the next available hunk ID
                        next_id = max(original_hunk_ids) + 1 if original_hunk_ids else i
                        hunk_id_mapping[i] = next_id
                        logger.warning(f"No pending hunk available for diff hunk #{i}, using ID {next_id}")

        # Log the mapping for debugging
        logger.info(f"Hunk ID mapping for discontiguous hunks: {hunk_id_mapping}")
        
        # Check each hunk
        hunk_id = None
        for i, hunk in enumerate(hunks, 1):
            all_hunks_found_applied = False  # Reset for each hunk to avoid false positives
            
            # FIXED: Use the correct hunk ID from the mapping
            original_hunk_id = hunk_id_mapping.get(i)
            if original_hunk_id is None:
                logger.warning(f"No mapping found for hunk #{i}, using hunk number as fallback")
                original_hunk_id = hunk.get('number', i)
                
            # Log which hunk we're processing
            logger.info(f"Processing hunk #{i} (mapped to original hunk #{original_hunk_id})")
            
            # Skip hunks that are already marked as applied in the pipeline
            if original_hunk_id in pipeline.result.hunks and pipeline.result.hunks[original_hunk_id].status in (HunkStatus.SUCCEEDED, HunkStatus.ALREADY_APPLIED):
                logger.info(f"Skipping hunk #{original_hunk_id} as it's already been successfully applied")
                continue
                
            found_applied_at_any_pos = False
            
            # Skip "already applied" detection for merged hunks since they contain
            # combined content from multiple original hunks that won't match the current file state
            is_merged_hunk = merged_hunk_mapping and (i-1) in merged_hunk_mapping
            if is_merged_hunk:
                logger.info(f"Skipping 'already applied' detection for merged hunk #{i} - will apply directly")
                logger.info(f"Hunk #{i} (original ID #{original_hunk_id}) is not already applied")
                all_hunks_found_applied = False
                continue
            
            # Check if this specific hunk is already applied anywhere in the file
            # OPTIMIZATION: Use smarter search instead of checking every position
            search_positions = []
            
            # Strategy 1: Check around the expected position first (most likely)
            expected_line = hunk.get('old_start_line', 1) - 1  # Convert to 0-based
            search_radius = 50  # Check within 50 lines of expected position
            start_search = max(0, expected_line - search_radius)
            end_search = min(len(original_lines), expected_line + search_radius)
            search_positions.extend(range(start_search, end_search + 1))
            
            # Strategy 2: If hunk has distinctive content, search for that first
            if 'new_lines' in hunk and hunk.get('new_lines'):
                # Look for the first distinctive line to narrow down search
                distinctive_line = None
                for line in hunk['new_lines']:
                    stripped = line.strip()
                    if len(stripped) > 10 and not stripped.startswith(('if ', 'for ', 'while ', 'def ', 'class ')):
                        distinctive_line = stripped
                        break
                
                if distinctive_line:
                    # Find positions where this distinctive line appears
                    for i, file_line in enumerate(original_lines):
                        if file_line.strip() == distinctive_line:
                            # Add positions around this match
                            for offset in range(-5, 6):  # Check 5 lines before/after
                                pos = i + offset
                                if 0 <= pos <= len(original_lines) and pos not in search_positions:
                                    search_positions.append(pos)
            
            # Strategy 3: Only do full file search as last resort and with sampling
            if not search_positions:
                # Sample every 10th position instead of checking every position
                sample_step = max(1, len(original_lines) // 100)
                search_positions = list(range(0, len(original_lines) + 1, sample_step))
            
            # Remove duplicates and sort
            search_positions = sorted(set(search_positions))
            logger.debug(f"Optimized already-applied check: searching {len(search_positions)} positions instead of {len(original_lines) + 1}")
            
            for pos in search_positions:
                # CRITICAL FIX: First check if the target state (new_lines) is already present in the file
                # This is the most important check - if the target state is already there, we can mark it as already applied
                if 'new_lines' in hunk and pos + len(hunk.get('new_lines', [])) <= len(original_lines):
                    # Extract the expected content after applying the hunk
                    new_lines = hunk.get('new_lines', [])
                    file_slice = original_lines[pos:pos+len(new_lines)]
                    
                    # Normalize both for comparison
                    normalized_file_slice = [normalize_line_for_comparison(line) for line in file_slice]
                    normalized_new_lines = [normalize_line_for_comparison(line) for line in new_lines]
                    
                    # If the file already contains the target state, mark it as already applied
                    if normalized_file_slice == normalized_new_lines:
                        # CRITICAL FIX: For deletion hunks, we need to check if the content to be deleted
                        # still exists in the file. If it does, the hunk is NOT already applied.
                        if 'removed_lines' in hunk:
                            removed_lines = hunk.get('removed_lines', [])
                            
                            # If this is a deletion hunk (has lines to remove)
                            if removed_lines:
                                # Check if the content to be deleted still exists anywhere in the file
                                removed_content = "\n".join([normalize_line_for_comparison(line) for line in removed_lines])
                                file_content = "\n".join([normalize_line_for_comparison(line) for line in original_lines])
                                
                                # If the content to be deleted still exists in the file, 
                                # then the hunk is NOT already applied
                                if removed_content in file_content:
                                    logger.debug(f"Deletion hunk not applied - content to be deleted still exists in file at pos {pos}")
                                    continue
                        
                        # CRITICAL FIX: Also check if the old_block matches what's in the file
                        # This prevents marking a hunk as "already applied" when the file has content
                        # that doesn't match what we're trying to remove
                        if 'old_block' in hunk:
                            # Extract the lines we're trying to remove
                            removed_lines = []
                            for line in hunk.get('old_block', []):
                                if line.startswith('-'):
                                    removed_lines.append(line[1:])
                            
                            # If there are lines to remove, check if they match the file content
                            if removed_lines:
                                # Check if we have enough lines in the file to compare
                                if pos + len(removed_lines) <= len(original_lines):
                                    # Compare the file content with the lines we're trying to remove
                                    file_slice_for_removed = original_lines[pos:pos+len(removed_lines)]
                                    
                                    # Normalize both for comparison
                                    normalized_file_slice_for_removed = [normalize_line_for_comparison(line) for line in file_slice_for_removed]
                                    normalized_removed_lines = [normalize_line_for_comparison(line) for line in removed_lines]
                                    
                                    # If the file content doesn't match what we're trying to remove,
                                    # then this hunk can't be already applied here
                                    if normalized_file_slice_for_removed != normalized_removed_lines:
                                        # Skip this position - the content doesn't match what we're trying to remove
                                        logger.debug(f"Skipping position {pos} - file content doesn't match what we're trying to remove")
                                        continue
                        
                        # CRITICAL FIX: For pure additions (like import statements), check if the exact content exists
                        # in the file before marking as already applied
                        if 'old_block' in hunk:
                            # Count the number of removed lines
                            removed_line_count = sum(1 for line in hunk.get('old_block', []) if line.startswith('-'))
                            
                            # If this is a pure addition (no lines removed)
                            if removed_line_count == 0:
                                # Get the added content
                                added_lines = []
                                for line in hunk.get('new_block', []):
                                    if line.startswith('+'):
                                        added_lines.append(line[1:])
                                
                                # Check if the exact added content exists anywhere in the file
                                added_content = "\n".join([normalize_line_for_comparison(line) for line in added_lines])
                                file_content = "\n".join([normalize_line_for_comparison(line) for line in original_lines])
                                
                                # If the exact added content doesn't exist in the file, it's not already applied
                                if added_content not in file_content:
                                    logger.debug(f"Pure addition not found in file content")
                                    continue
                        
                        # CRITICAL: Check for malformed state before marking as already applied
                        from ..validation.validators import detect_malformed_state, extract_diff_changes, _validate_removal_content
                        if detect_malformed_state(original_lines, hunk):
                            logger.info(f"Hunk #{i} (original ID #{original_hunk_id}) detected in malformed state - not marking as already applied")
                            all_hunks_found_applied = False
                            continue
                        
                        # ADDITIONAL CHECK: For hunks with removals, ensure removal content actually exists
                        removed_lines, _ = extract_diff_changes(hunk)
                        if removed_lines and not _validate_removal_content(original_lines, removed_lines, pos):
                            logger.info(f"Hunk #{i} (original ID #{original_hunk_id}) removal validation failed - not marking as already applied")
                            all_hunks_found_applied = False
                            continue
                        
                        found_applied_at_any_pos = True
                        # Use the correct hunk ID from the mapping
                        pipeline_hunk_id = hunk_id_mapping.get(i, original_hunk_id)
                        already_applied_hunks.append(pipeline_hunk_id)
                        
                        # Update the hunk status in the pipeline
                        # Check if this is a merged hunk and update all original hunks
                        if merged_hunk_mapping and (i-1) in merged_hunk_mapping:
                            update_merged_hunk_status(pipeline, merged_hunk_mapping, i-1, HunkStatus.ALREADY_APPLIED, PipelineStage.DIFFLIB)
                        else:
                            pipeline.update_hunk_status(
                                hunk_id=pipeline_hunk_id,
                                stage=PipelineStage.DIFFLIB,
                                status=HunkStatus.ALREADY_APPLIED
                            )
                        logger.info(f"Hunk #{i} (original ID #{pipeline_hunk_id}) is already applied at position {pos}")
                        break
                
                # If we haven't found it already applied, use the standard check
                if not found_applied_at_any_pos and is_hunk_already_applied(original_lines, hunk, pos, ignore_whitespace=False):
                    # CRITICAL: Double-check for malformed state before marking as already applied
                    from ..validation.validators import detect_malformed_state, extract_diff_changes, _validate_removal_content
                    if detect_malformed_state(original_lines, hunk):
                        logger.info(f"Hunk #{i} (original ID #{original_hunk_id}) detected in malformed state - not marking as already applied")
                        all_hunks_found_applied = False
                        continue
                    
                    # ADDITIONAL CHECK: For hunks with removals, ensure removal content actually exists
                    removed_lines, _ = extract_diff_changes(hunk)
                    if removed_lines and not _validate_removal_content(original_lines, removed_lines, pos):
                        logger.info(f"Hunk #{i} (original ID #{original_hunk_id}) removal validation failed - not marking as already applied")
                        all_hunks_found_applied = False
                        continue
                    
                    found_applied_at_any_pos = True
                    # Use the correct hunk ID from the mapping
                    pipeline_hunk_id = hunk_id_mapping.get(i, original_hunk_id)
                    already_applied_hunks.append(pipeline_hunk_id)
                    
                    # Update the hunk status in the pipeline
                    # Check if this is a merged hunk and update all original hunks
                    if merged_hunk_mapping and (i-1) in merged_hunk_mapping:
                        update_merged_hunk_status(pipeline, merged_hunk_mapping, i-1, HunkStatus.ALREADY_APPLIED, PipelineStage.DIFFLIB)
                    else:
                        pipeline.update_hunk_status(
                            hunk_id=pipeline_hunk_id,
                            stage=PipelineStage.DIFFLIB,
                            status=HunkStatus.ALREADY_APPLIED
                        )
                    logger.info(f"Hunk #{i} (original ID #{pipeline_hunk_id}) is already applied at position {pos}")
                    break
            
            if not found_applied_at_any_pos:
                logger.info(f"Hunk #{i} (original ID #{original_hunk_id}) is not already applied")
                all_hunks_found_applied = False
                
        # Special handling for misordered hunks and multi-hunk same function cases
        from ..application.hunk_ordering import is_misordered_hunks_case, is_multi_hunk_same_function_case
        if is_misordered_hunks_case(hunks):
            logger.info("Detected misordered hunks case, will use special handling")
        elif is_multi_hunk_same_function_case(hunks):
            logger.info("Detected multi-hunk same function case, will use special handling")
        
        if all_hunks_found_applied:
            logger.info("All hunks already applied, returning original content")
            # Mark all pending hunks as already applied
            for hunk_id, tracker in pipeline.result.hunks.items():
                if tracker.status == HunkStatus.PENDING:
                    # Clear any previous error details since the hunk is actually already applied
                    pipeline.update_hunk_status(
                        hunk_id=hunk_id,
                        stage=PipelineStage.DIFFLIB,
                        status=HunkStatus.ALREADY_APPLIED,
                        error_details=None  # Clear previous errors
                    )
                    logger.info(f"Marked hunk #{hunk_id} as ALREADY_APPLIED (ignoring previous errors)")
            
            # Explicitly add all hunks to already_applied_hunks
            for i, hunk in enumerate(hunks, 1):
                # FIXED: Use the correct hunk ID from the mapping
                pipeline_hunk_id = hunk_id_mapping.get(i, hunk.get('number', i))
                if pipeline_hunk_id not in pipeline.result.already_applied_hunks:
                    pipeline.result.already_applied_hunks.append(pipeline_hunk_id)
            
            # Set the status to success for already applied hunks
            pipeline.result.status = "success"
            pipeline.result.changes_written = False
            
            # Complete the pipeline and return success
            pipeline.complete()
            
            # No need to modify the result_dict - the to_dict method will properly
            # include the already_applied_hunks from the pipeline result
            
            return False
        
        # Filter out hunks that are already marked as applied
        hunks_to_apply = []
        hunks_to_skip = []
        
        for i, hunk in enumerate(hunks, 1):
            hunk_id = hunk_id_mapping.get(i, hunk.get('number', i))
            if hunk_id in pipeline.result.hunks and pipeline.result.hunks[hunk_id].status == HunkStatus.ALREADY_APPLIED:
                logger.info(f"Skipping hunk #{i} (ID #{hunk_id}) as it's already marked as applied")
                hunks_to_skip.append(hunk_id)
            else:
                hunks_to_apply.append(hunk)
        
        if not hunks_to_apply:
            logger.info("All hunks are already marked as applied, no need to apply diff")
            return False
        
        # Apply the diff with difflib
        try:
            # CRITICAL: Check for malformed hunks before application
            from ..validation.validators import detect_malformed_state
            malformed_hunks = []
            for i, hunk in enumerate(hunks, 1):
                if detect_malformed_state(original_lines, hunk):
                    hunk_id = hunk.get('number', i)
                    malformed_hunks.append(hunk_id)
                    logger.info(f"Hunk #{hunk_id} detected as malformed - marking as failed")
                    pipeline.update_hunk_status(
                        hunk_id=hunk_id,
                        stage=PipelineStage.DIFFLIB,
                        status=HunkStatus.FAILED,
                        error_details={"error": "Malformed state detected - both old and new content exist"}
                    )
            
            # Add malformed hunks to the skip list
            if malformed_hunks:
                logger.info(f"Adding malformed hunks to skip list: {malformed_hunks}")
                hunks_to_skip.extend(malformed_hunks)
            
            # If all hunks are malformed, return failure
            if len(malformed_hunks) == len(hunks):
                logger.warning("All hunks are malformed, cannot apply diff")
                return False
            
            # First try with the hybrid forced mode
            try:
                logger.info("Attempting to apply diff with hybrid forced mode")
                
                # CRITICAL FIX: Use the merged hunks directly instead of re-parsing the original diff
                if merged_hunk_mapping:
                    logger.info("Using merged hunks for difflib application")
                    modified_lines = apply_diff_with_difflib_hybrid_forced_hunks(
                        file_path, 
                        hunks,  # Use the merged hunks directly
                        original_lines,
                        skip_hunks=hunks_to_skip
                    )
                else:
                    logger.info("Using original diff content for difflib application")
                    # Pass the list of hunks to skip to the hybrid forced mode
                    modified_lines = apply_diff_with_difflib_hybrid_forced(
                        file_path, 
                        git_diff, 
                        original_lines,
                        skip_hunks=hunks_to_skip
                    )
                modified_content = ''.join(modified_lines)
                
                # CRITICAL VERIFICATION: Check if the content actually changed
                if modified_content == original_content:
                    logger.warning("Hybrid forced mode claimed success but no changes were made to the content")
                    # This could be a case where the changes were already applied
                    # Double-check if hunks are already applied
                    all_already_applied = True
                    for i, hunk in enumerate(hunks, 1):
                        hunk_applied = False
                        hunk_id = hunk.get('number', i)
                        # Check if this hunk is already applied anywhere in the file
                        for pos in range(len(original_lines) + 1):
                            # CRITICAL: Check for malformed state before marking as already applied
                            from ..validation.validators import detect_malformed_state, extract_diff_changes, _validate_removal_content
                            if detect_malformed_state(original_lines, hunk):
                                logger.info(f"Hunk #{hunk_id} detected in malformed state - not marking as already applied")
                                hunk_applied = False
                                break
                            
                            # ADDITIONAL CHECK: For hunks with removals, ensure removal content actually exists
                            removed_lines, _ = extract_diff_changes(hunk)
                            if removed_lines and not _validate_removal_content(original_lines, removed_lines, pos):
                                logger.info(f"Hunk #{hunk_id} removal validation failed - not marking as already applied")
                                hunk_applied = False
                                break
                            
                            if is_hunk_already_applied(original_lines, hunk, pos):
                                hunk_applied = True
                                logger.info(f"Verified hunk #{hunk_id} is already applied at position {pos}")
                                break
                        if not hunk_applied:
                            all_already_applied = False
                            break
                    
                    if all_already_applied:
                        # All hunks were actually already applied
                        logger.info("Verified all hunks were already applied")
                        for hunk_id, tracker in pipeline.result.hunks.items():
                            if tracker.status == HunkStatus.PENDING:
                                pipeline.update_hunk_status(
                                    hunk_id=hunk_id,
                                    stage=PipelineStage.DIFFLIB,
                                    status=HunkStatus.ALREADY_APPLIED
                                )
                                logger.info(f"Marked hunk #{hunk_id} as ALREADY_APPLIED")
                        # Set changes_written to False since no actual changes were made
                        pipeline.result.changes_written = False
                        return False
                    else:
                        # Hunks were not applied and no changes were made
                        logger.warning("Hunks were not already applied but no changes were made")
                        for hunk_id, tracker in pipeline.result.hunks.items():
                            if tracker.status == HunkStatus.PENDING:
                                pipeline.update_hunk_status(
                                    hunk_id=hunk_id,
                                    stage=PipelineStage.DIFFLIB,
                                    status=HunkStatus.FAILED,
                                    error_details={"error": "No changes were applied despite success claim"}
                                )
                                logger.info(f"Marked hunk #{hunk_id} as FAILED due to no actual changes")
                        # Set changes_written to False since no actual changes were made
                        pipeline.result.changes_written = False
                        return False
                
                logger.info("Successfully applied diff with hybrid forced mode - verified content changes")

                # Write the modified content back to the file
                with open(file_path, 'w', encoding='utf-8') as f:
                    f.write(modified_content)
                    logger.info(f"Successfully wrote changes to {file_path}")

                pipeline.result.changes_written = True
                
                # Update all hunks to SUCCEEDED regardless of previous status
                # This is necessary because the difflib stage can succeed even if git_apply failed
                for hunk_id in pipeline.result.hunks:
                    pipeline.update_hunk_status(
                        hunk_id=hunk_id,
                        stage=PipelineStage.DIFFLIB,
                        status=HunkStatus.SUCCEEDED
                    )
                    logger.info(f"Marked hunk #{hunk_id} as SUCCEEDED in difflib stage")
                
                return True
                
            except Exception as e:
                if hasattr(e, 'details') and getattr(e, 'details', {}).get("type") == "already_applied":
                    # If hybrid mode says already applied, trust it
                    logger.info("Hybrid mode detected hunks already applied")
                    for hunk_id, tracker in pipeline.result.hunks.items():
                        if tracker.status == HunkStatus.PENDING:
                            pipeline.update_hunk_status(
                                hunk_id=hunk_id,
                                stage=PipelineStage.DIFFLIB,
                                status=HunkStatus.ALREADY_APPLIED
                            )
                    return False
                else:
                    # Fall back to regular difflib mode
                    logger.info(f"Falling back to regular difflib mode due to: {str(e)}")
                    try:
                        # Use the correct function signature for apply_diff_with_difflib
                        modified_content = apply_diff_with_difflib(file_path, git_diff, hunks_to_skip)
                        if modified_content is None:
                            logger.error("apply_diff_with_difflib returned None")
                            # Mark all pending hunks as failed
                            for hunk_id, tracker in pipeline.result.hunks.items():
                                if tracker.status == HunkStatus.PENDING:
                                    pipeline.update_hunk_status(
                                        hunk_id=hunk_id,
                                        stage=PipelineStage.DIFFLIB,
                                        status=HunkStatus.FAILED,
                                        error_details={"error": "apply_diff_with_difflib returned None"}
                                    )
                            return False
                        
                        # CRITICAL VERIFICATION: Check if the content actually changed
                        if modified_content == original_content:
                            logger.warning("Regular difflib mode claimed success but no changes were made to the content")
                            # This could be a case where the changes were already applied
                            # Double-check if hunks are already applied
                            all_already_applied = True
                            for i, hunk in enumerate(hunks, 1):
                                hunk_applied = False
                                hunk_id = hunk.get('number', i)
                                # Check if this hunk is already applied anywhere in the file
                                for pos in range(len(original_lines) + 1):
                                    # CRITICAL: Check for malformed state before marking as already applied
                                    from ..validation.validators import detect_malformed_state, extract_diff_changes, _validate_removal_content
                                    if detect_malformed_state(original_lines, hunk):
                                        logger.info(f"Hunk #{hunk_id} detected in malformed state - not marking as already applied")
                                        hunk_applied = False
                                        break
                                    
                                    # ADDITIONAL CHECK: For hunks with removals, ensure removal content actually exists
                                    removed_lines, _ = extract_diff_changes(hunk)
                                    if removed_lines and not _validate_removal_content(original_lines, removed_lines, pos):
                                        logger.info(f"Hunk #{hunk_id} removal validation failed - not marking as already applied")
                                        hunk_applied = False
                                        break
                                    
                                    if is_hunk_already_applied(original_lines, hunk, pos):
                                        hunk_applied = True
                                        logger.info(f"Verified hunk #{hunk_id} is already applied at position {pos}")
                                        break
                                if not hunk_applied:
                                    all_already_applied = False
                                    break
                            
                            if all_already_applied:
                                # All hunks were actually already applied
                                logger.info("Verified all hunks were already applied")
                                for hunk_id, tracker in pipeline.result.hunks.items():
                                    if tracker.status == HunkStatus.PENDING:
                                        pipeline.update_hunk_status(
                                            hunk_id=hunk_id,
                                            stage=PipelineStage.DIFFLIB,
                                            status=HunkStatus.ALREADY_APPLIED
                                        )
                                        logger.info(f"Marked hunk #{hunk_id} as ALREADY_APPLIED")
                                
                                # Explicitly add all hunks to already_applied_hunks
                                for i, hunk in enumerate(hunks, 1):
                                    hunk_id = hunk.get('number', i)
                                    if hunk_id not in pipeline.result.already_applied_hunks:
                                        pipeline.result.already_applied_hunks.append(hunk_id)
                                
                                pipeline.result.status = "success"
                                # Set changes_written to False since no actual changes were made
                                pipeline.result.changes_written = False
                                return False
                            else:
                                # Hunks were not applied and no changes were made
                                logger.warning("Hunks were not already applied but no changes were made")
                                for hunk_id, tracker in pipeline.result.hunks.items():
                                    if tracker.status == HunkStatus.PENDING:
                                        pipeline.update_hunk_status(
                                            hunk_id=hunk_id,
                                            stage=PipelineStage.DIFFLIB,
                                            status=HunkStatus.FAILED,
                                            error_details={"error": "No changes were applied despite success claim"}
                                        )
                                        logger.info(f"Marked hunk #{hunk_id} as FAILED due to no actual changes")
                                # Set changes_written to False since no actual changes were made
                                pipeline.result.changes_written = False
                                return False
                        
                        modified_lines = modified_content.splitlines(True)

                        # Write changes only if we got modified content
                        with open(file_path, 'w', encoding='utf-8') as f:
                            f.write(modified_content)
                            logger.info(f"Successfully wrote changes to {file_path}")
                            logger.info("Successfully applied diff with regular difflib mode - verified content changes")
                        
                        # Update all hunks to SUCCEEDED regardless of previous status
                        for hunk_id in pipeline.result.hunks:
                            pipeline.update_hunk_status(
                                hunk_id=hunk_id,
                                stage=PipelineStage.DIFFLIB,
                                status=HunkStatus.SUCCEEDED
                            )
                            logger.info(f"Marked hunk #{hunk_id} as SUCCEEDED in regular difflib mode")
                        
                        pipeline.result.changes_written = True
                        return True
                    except Exception as inner_e:
                        logger.error(f"Error in regular difflib mode: {str(inner_e)}")
                        return False
            
        except PatchApplicationError as e:
            if e.details.get("type") == "already_applied":
                logger.info("Difflib detected hunks already applied")
                # Mark all pending hunks as already applied
                for hunk_id, tracker in pipeline.result.hunks.items():
                    if tracker.status == HunkStatus.PENDING:
                        pipeline.update_hunk_status(
                            hunk_id=hunk_id,
                            stage=PipelineStage.DIFFLIB,
                            status=HunkStatus.ALREADY_APPLIED
                        )
                        logger.info(f"Marked hunk #{hunk_id} as ALREADY_APPLIED")
                return False
            else:
                # Handle failures from difflib
                logger.error(f"Difflib application failed: {str(e)}")
                failures = e.details.get("failures", [])
                
                # Map failures to hunks
                for failure in failures:
                    hunk_idx = failure.get("details", {}).get("hunk")
                    if hunk_idx:
                        pipeline.update_hunk_status(
                            hunk_id=hunk_idx,
                            stage=PipelineStage.DIFFLIB,
                            status=HunkStatus.FAILED,
                            error_details=failure.get("details"),
                            confidence=failure.get("details", {}).get("confidence", 0.0)
                        )
                        logger.info(f"Marked hunk #{hunk_idx} as FAILED with confidence {failure.get('details', {}).get('confidence', 0.0)}")
                
                # Mark any remaining pending hunks as failed
                for hunk_id, tracker in pipeline.result.hunks.items():
                    if tracker.status == HunkStatus.PENDING:
                        pipeline.update_hunk_status(
                            hunk_id=hunk_id,
                            stage=PipelineStage.DIFFLIB,
                            status=HunkStatus.FAILED,
                            error_details={"error": str(e)}
                        )
                        logger.info(f"Marked remaining hunk #{hunk_id} as FAILED")
                
                return False
    except Exception as e:
        logger.error(f"Error in difflib stage: {str(e)}")
        hunks_marked_failed = []
        hunk_ids_to_process = list(pipeline.result.hunks.keys())
        for current_hunk_id in hunk_ids_to_process:
            tracker = pipeline.result.hunks[current_hunk_id]
            if tracker.status == HunkStatus.PENDING:
                pipeline.update_hunk_status(
                    hunk_id=current_hunk_id,
                    stage=PipelineStage.DIFFLIB,
                    status=HunkStatus.FAILED,
                    error_details={"error": str(e)}
                )
                hunks_marked_failed.append(current_hunk_id)
        logger.info(f"Marked hunks {hunks_marked_failed} as FAILED due to exception: {str(e)}")
        return False

def run_llm_resolver_stage(pipeline: DiffPipeline, file_path: str, git_diff: str, original_lines: List[str]) -> bool:
    """
    Run the LLM resolver stage of the pipeline.
    This is a stub for now and will be implemented in the future.
    
    Args:
        pipeline: The diff pipeline
        file_path: Path to the file to modify
        git_diff: The git diff to apply
        original_lines: The original file content as a list of lines
        
    Returns:
        True if any changes were written, False otherwise
    """
    logger.info("Starting LLM resolver stage...")
    
    # This is a stub for now
    # In the future, this would use an LLM to resolve complex cases
    
    # Mark all pending hunks as failed for now
    for hunk_id, tracker in pipeline.result.hunks.items():
        if tracker.status == HunkStatus.PENDING:
            pipeline.update_hunk_status(
                hunk_id=hunk_id,
                stage=PipelineStage.LLM_RESOLVER,
                status=HunkStatus.FAILED,
                error_details={"error": "LLM resolver not implemented yet"}
            )
    
    return False
def verify_hunks_with_file_content(pipeline: DiffPipeline, hunk_status: Dict[int, Dict[str, Any]]) -> None:
    """
    Verify hunks that were marked as "needs_verification" by checking the actual file content.
    
    Args:
        pipeline: The pipeline instance
        hunk_status: Dictionary mapping hunk IDs to status information
    """
    from ..validation.validators import is_hunk_already_applied
    
    # Read the file content
    try:
        with open(pipeline.file_path, 'r', encoding='utf-8') as f:
            file_content = f.read()
            file_lines = file_content.splitlines()
    except FileNotFoundError:
        logger.warning(f"File not found: {pipeline.file_path}")
        # Mark all needs_verification hunks as failed
        for hunk_id, status in hunk_status.items():
            if status.get("status") == "needs_verification":
                hunk_status[hunk_id]["status"] = "failed"
                hunk_status[hunk_id]["error"] = "File not found"
        return
        
    # Parse the hunks from the diff
    from ..parsing.diff_parser import parse_unified_diff_exact_plus
    try:
        hunks = list(parse_unified_diff_exact_plus(pipeline.current_diff, pipeline.file_path))
    except Exception as e:
        logger.error(f"Error parsing diff: {str(e)}")
        # Mark all needs_verification hunks as failed
        for hunk_id, status in hunk_status.items():
            if status.get("status") == "needs_verification":
                hunk_status[hunk_id]["status"] = "failed"
                hunk_status[hunk_id]["error"] = f"Error parsing diff: {str(e)}"
        return
        
    # Verify each hunk that needs verification
    for hunk_id, status in hunk_status.items():
        if status.get("status") == "needs_verification":
            # Find the corresponding hunk in the parsed hunks
            hunk = None
            for h in hunks:
                if h.get('number') == hunk_id:
                    hunk = h
                    break
                    
            if not hunk:
                logger.warning(f"Could not find hunk {hunk_id} in parsed hunks")
                hunk_status[hunk_id]["status"] = "failed"
                hunk_status[hunk_id]["error"] = "Could not find hunk in parsed diff"
                continue
                
            # Get the position where the hunk was supposedly applied
            pos = status.get("position", 0)
            if pos <= 0:
                logger.warning(f"Invalid position {pos} for hunk {hunk_id}")
                hunk_status[hunk_id]["status"] = "failed"
                hunk_status[hunk_id]["error"] = "Invalid position"
                continue
                
            # Adjust position to 0-based index
            pos = pos - 1
                
            # Check if the hunk is actually already applied at this position
            is_applied = is_hunk_already_applied(file_lines, hunk, pos)
            
            if is_applied:
                logger.info(f"Verified hunk {hunk_id} is already applied at position {pos}")
                hunk_status[hunk_id]["status"] = "already_applied"
            else:
                logger.info(f"Verified hunk {hunk_id} is NOT already applied at position {pos}")
                # Mark as failed so it will be tried with other methods
                hunk_status[hunk_id]["status"] = "failed"
                hunk_status[hunk_id]["error"] = "Not already applied despite patch command indication"
