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
from typing import Dict, List, Any, Optional, Tuple

from app.utils.logging_utils import logger
from ..core.exceptions import PatchApplicationError
from ..parsing.diff_parser import parse_unified_diff_exact_plus, extract_target_file_from_diff, split_combined_diff
from ..validation.validators import is_new_file_creation, is_hunk_already_applied, normalize_line_for_comparison
from ..file_ops.file_handlers import create_new_file, cleanup_patch_artifacts
from ..application.patch_apply import apply_diff_with_difflib, apply_diff_with_difflib_hybrid_forced
from ..application.git_diff import parse_patch_output

from .diff_pipeline import DiffPipeline, PipelineStage, HunkStatus, PipelineResult

def apply_diff_pipeline(git_diff: str, file_path: str) -> Dict[str, Any]:
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
        
    Returns:
        A dictionary with the result of the pipeline
    """
    logger.info("Starting diff application pipeline...")
    logger.debug("Original diff content:")
    logger.debug(git_diff)
    
    # Initialize the pipeline
    pipeline = DiffPipeline(file_path, git_diff)
    pipeline.update_stage(PipelineStage.INIT)
    
    # Split combined diffs if present
    logger.debug(f"Original diff first 10 lines:\n{git_diff.splitlines()[:10]}")
    individual_diffs = split_combined_diff(git_diff)
    logger.debug(f"Number of individual diffs: {len(individual_diffs)}")
    if len(individual_diffs) > 0:
        logger.debug(f"First individual diff first 10 lines:\n{individual_diffs[0].splitlines()[:10]}")
    
    if len(individual_diffs) > 1:
        # Find the diff that matches our target file
        matching_diff = next((diff for diff in individual_diffs 
                            if extract_target_file_from_diff(diff) == file_path), None)
        if matching_diff:
            git_diff = matching_diff
            pipeline.current_diff = git_diff
    
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
        logger.error(error)
        pipeline.complete(error=error)
        return pipeline.result.to_dict()
    
    # If force difflib flag is set, skip system patch and git apply
    if os.environ.get('ZIYA_FORCE_DIFFLIB'):
        logger.info("Force difflib mode enabled, bypassing system patch and git apply")
        return run_difflib_stage(pipeline, file_path, git_diff, original_lines)
    
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
        if pipeline.result.changes_written:
            pipeline.complete()
            return pipeline.result.to_dict()
        else:
            pipeline.complete(error="No hunks were applied")
            return pipeline.result.to_dict()
    
    git_apply_result = run_git_apply_stage(pipeline, user_codebase_dir, remaining_diff)
    
    # If all hunks succeeded or were already applied, we're done
    if all(tracker.status in (HunkStatus.SUCCEEDED, HunkStatus.ALREADY_APPLIED) 
           for tracker in pipeline.result.hunks.values()):
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
        if pipeline.result.changes_written:
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
    else:
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
    
    # Verify the final status is consistent with the actual changes
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
        
        # Check if all changes are already applied
        # We need to check both stdout and stderr for errors
        has_malformed_error = "malformed patch" in patch_result.stderr
        has_failure = "failed" in patch_result.stdout.lower() or patch_result.returncode != 0
        
        already_applied = (
            "No file to patch" not in patch_result.stdout and 
            "Reversed (or previously applied)" in patch_result.stdout and
            not has_failure and
            not has_malformed_error
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
        for hunk_id, success in dry_run_status.items():
            if success:
                pipeline.update_hunk_status(
                    hunk_id=hunk_id,
                    stage=PipelineStage.SYSTEM_PATCH,
                    status=HunkStatus.PENDING  # Will be updated after actual patch
                )
            else:
                pipeline.update_hunk_status(
                    hunk_id=hunk_id,
                    stage=PipelineStage.SYSTEM_PATCH,
                    status=HunkStatus.FAILED,
                    error_details={"error": "Failed in dry run"}
                )
        
        # If no hunks succeeded in dry run, skip the actual patch
        if not any(success for success in dry_run_status.values()):
            logger.info("No hunks succeeded in dry run, skipping actual patch")
            return False
        
        # Apply the patch for real
        logger.info(f"Applying {sum(1 for v in dry_run_status.values() if v)}/{len(dry_run_status)} hunks with system patch...")
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
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', suffix='.diff', delete=False) as temp_file:
            temp_file.write(git_diff)
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
            # Mark all pending hunks as succeeded
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
            if not hunk.get('old_block') or not hunk.get('new_lines'):
                logger.warning(f"Malformed hunk detected: missing old_block or new_lines")
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
        hunk_id_mapping = {}

        # Get the original hunk IDs in order
        original_hunk_ids = sorted(pipeline.result.hunks.keys())

        # Create a list of pending hunks (those that still need processing)
        pending_hunks = [hunk_id for hunk_id in original_hunk_ids 
                        if pipeline.result.hunks[hunk_id].status == HunkStatus.PENDING]

        # Log the pending hunks for debugging
        logger.info(f"Pending hunks that need processing: {pending_hunks}")

        # Create mapping from current sequential index to original hunk ID
        # This handles discontiguous mappings by using the actual pending hunks
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
            # Check if this specific hunk is already applied anywhere in the file
            for pos in range(len(original_lines) + 1):  # +1 to allow checking at EOF
                if is_hunk_already_applied(original_lines, hunk, pos, ignore_whitespace=False):
                    found_applied_at_any_pos = True
                    # Use the correct hunk ID from the mapping
                    pipeline_hunk_id = hunk_id_mapping.get(i, original_hunk_id)
                    already_applied_hunks.append(pipeline_hunk_id)
                    
                    # Update the hunk status in the pipeline
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
                # Don't break here - continue checking other hunks
                
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
        
        # Apply the diff with difflib
        try:
            # First try with the hybrid forced mode
            try:
                logger.info("Attempting to apply diff with hybrid forced mode")
                modified_lines = apply_diff_with_difflib_hybrid_forced(file_path, git_diff, original_lines)
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
                        modified_content = apply_diff_with_difflib(file_path, git_diff)
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
