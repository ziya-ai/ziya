"""
Pipeline manager for applying diffs.

This module provides the main entry point for the diff application pipeline,
coordinating the flow through system patch, git apply, difflib, and LLM resolver.
"""

import os
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
    individual_diffs = split_combined_diff(git_diff)
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
            if line.startswith('diff --git'):
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
    
    # If all hunks succeeded or were already applied, we're done
    if all(tracker.status in (HunkStatus.SUCCEEDED, HunkStatus.ALREADY_APPLIED) 
           for tracker in pipeline.result.hunks.values()):
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
        pipeline.result.changes_written = True
        pipeline.complete()
        return pipeline.result.to_dict()
    
    # Stage 3: Difflib (for hunks that failed in git apply)
    pipeline.update_stage(PipelineStage.DIFFLIB)
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
    
    difflib_result = run_difflib_stage(pipeline, file_path, remaining_diff, current_lines)
    
    # Stage 4: LLM Resolver (stub for now)
    # This would be implemented in the future to handle complex cases
    
    # Complete the pipeline
    pipeline.complete()
    
    # Add detailed debug logging about hunk statuses
    logger.info("=== PIPELINE COMPLETION SUMMARY ===")
    logger.info(f"File: {file_path}")
    logger.info(f"Total hunks: {len(pipeline.result.hunks)}")
    logger.info(f"Succeeded hunks: {pipeline.result.succeeded_hunks}")
    logger.info(f"Failed hunks: {pipeline.result.failed_hunks}")
    logger.info(f"Already applied hunks: {pipeline.result.already_applied_hunks}")
    logger.info(f"Pending hunks: {pipeline.result.pending_hunks}")
    
    # Log detailed status for each hunk
    logger.info("=== DETAILED HUNK STATUS ===")
    for hunk_id, tracker in pipeline.result.hunks.items():
        logger.info(f"Hunk #{hunk_id}: Status={tracker.status.value}, Stage={tracker.current_stage.value}")
        if tracker.error_details:
            logger.info(f"  Error details: {tracker.error_details}")
    
    return pipeline.result.to_dict()

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
        patch_command_dry = ['patch', '-p1', '--forward', '--no-backup-if-mismatch', '--reject-file=-', '--batch', '--ignore-whitespace', '--verbose', '--dry-run', '-i', '-'],
        logger.debug(f"Running patch command (dry-run): {' '.join(patch_command_dry)}")
        logger.debug(f"Patch input string (repr):\n{repr(git_diff)}") # Log with repr to see hidden chars/newlines
        logger.debug("--- End Patch Input ---")

        command_to_run_dry = patch_command_dry[0] if isinstance(patch_command_dry[0], list) else patch_command_dry
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
        dry_run_status = parse_patch_output(patch_result.stdout)
        
        # Check if all changes are already applied
        already_applied = (
            "No file to patch" not in patch_result.stdout and 
            "Reversed (or previously applied)" in patch_result.stdout and
            "failed" not in patch_result.stdout.lower()
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
                    status=HunkStatus.FAILED
                )
        
        # If no hunks succeeded in dry run, skip the actual patch
        if not any(success for success in dry_run_status.values()):
            logger.info("No hunks succeeded in dry run, skipping actual patch")
            return False
        
        # Apply the patch for real
        logger.info(f"Applying {sum(1 for v in dry_run_status.values() if v)}/{len(dry_run_status)} hunks with system patch...")
        patch_command_apply = ['patch', '-p1', '--forward', '--no-backup-if-mismatch', '--reject-file=-', '--batch', '--ignore-whitespace', '--verbose', '-i', '-']
        command_to_run_apply = patch_command_apply[0] if isinstance(patch_command_apply[0], list) else patch_command_apply
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
        patch_status = parse_patch_output(patch_result.stdout)
        
        # Update hunk statuses based on actual patch
        changes_written = False
        for hunk_id, success in patch_status.items():
            if success:
                if "Reversed (or previously applied)" in patch_result.stdout and f"Hunk #{hunk_id}" in patch_result.stdout:
                    pipeline.update_hunk_status(
                        hunk_id=hunk_id,
                        stage=PipelineStage.SYSTEM_PATCH,
                        status=HunkStatus.ALREADY_APPLIED
                    )
                else:
                    pipeline.update_hunk_status(
                        hunk_id=hunk_id,
                        stage=PipelineStage.SYSTEM_PATCH,
                        status=HunkStatus.SUCCEEDED
                    )
                    changes_written = True
            else:
                pipeline.update_hunk_status(
                    hunk_id=hunk_id,
                    stage=PipelineStage.SYSTEM_PATCH,
                    status=HunkStatus.FAILED
                )
        
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
        
        # Check if all hunks are already applied        
        all_hunks_found_applied = True # Assume true initially
        hunk_id = None
        for i, hunk in enumerate(hunks, 1):
            hunk_id = hunk.get('number', i)
            found_applied_at_any_pos = False
            # Check if this hunk is already applied anywhere in the file
            for pos in range(len(original_lines) + 1):  # +1 to allow checking at EOF
                if is_hunk_already_applied(original_lines, hunk, pos):
                    found_applied_at_any_pos = True
                    logger.info(f"Hunk #{hunk_id} is already applied at position {pos}")
                    break
            if not found_applied_at_any_pos:
                logger.info(f"Hunk #{hunk_id} is not already applied")
                all_hunks_found_applied = False
                break
                
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
                    pipeline.update_hunk_status(
                        hunk_id=hunk_id,
                        stage=PipelineStage.DIFFLIB,
                        status=HunkStatus.ALREADY_APPLIED
                    )
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
