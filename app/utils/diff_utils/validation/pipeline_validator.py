"""
Pipeline-based diff validation using the EXACT same pipeline as actual application.

This ensures validation results are 100% accurate to what would happen during
actual application, with per-hunk detailed feedback.
"""

import logging
import os
import tempfile
import shutil
from typing import Dict, Any, List, Optional
from app.utils.logging_utils import logger
from ..parsing.diff_parser import extract_target_file_from_diff
from ..pipeline.pipeline_manager import apply_diff_pipeline


def validate_diff_with_full_pipeline(
    diff_content: str,
    file_path: Optional[str] = None
) -> Dict[str, Any]:
    """
    Validate a diff using the EXACT same pipeline as actual application.
    
    This creates a temporary copy of the file and runs the complete pipeline
    to get accurate per-hunk validation results.
    
    Args:
        diff_content: The git diff to validate
        file_path: Optional file path (will be extracted from diff if not provided)
        
    Returns:
        Dictionary with detailed validation results:
        {
            "can_apply": bool,                    # True if all/some hunks succeeded
            "status": str,                        # "success", "partial", or "error"
            "file_path": str,                     # Target file path
            "total_hunks": int,                   # Total number of hunks
            "succeeded_hunks": list[int],         # Hunk IDs that succeeded
            "failed_hunks": list[int],            # Hunk IDs that failed
            "already_applied_hunks": list[int],   # Hunk IDs already applied
            "hunk_details": dict,                 # Per-hunk status details
            "recommendation": str,                # What to fix
            "model_feedback": str                 # Formatted feedback for LLM
        }
    """
    logger.info("Validating diff with full pipeline...")
    
    result = {
        "can_apply": False,
        "status": "error",
        "file_path": "",
        "total_hunks": 0,
        "succeeded_hunks": [],
        "failed_hunks": [],
        "already_applied_hunks": [],
        "hunk_details": {},
        "recommendation": "",
        "model_feedback": ""
    }
    
    # Extract file path if not provided
    if not file_path:
        file_path = extract_target_file_from_diff(diff_content)
        if not file_path:
            result["recommendation"] = "Add proper diff headers (diff --git, ---, +++)"
            result["model_feedback"] = f"❌ Could not extract file path from diff. Regenerate with complete headers:\ndiff --git a/file.ext b/file.ext\n--- a/file.ext\n+++ b/file.ext"
            return result
    
    result["file_path"] = file_path
    
    # Get the user codebase directory
    try:
        from app.context import get_project_root_or_none
        codebase_dir = get_project_root_or_none() or os.environ.get("ZIYA_USER_CODEBASE_DIR")
    except ImportError:
        codebase_dir = os.environ.get("ZIYA_USER_CODEBASE_DIR")
    if not codebase_dir:
        result["model_feedback"] = "❌ System error: codebase directory not configured"
        return result
    
    full_path = os.path.join(codebase_dir, file_path)
    
    # If file not found at primary codebase_dir, check the current project path
    # This handles the case where ZIYA_USER_CODEBASE_DIR is the server launch dir
    # but the diff targets files in a different project
    if not os.path.exists(full_path):
        try:
            from ...storage.projects import ProjectStorage
            from ...utils.paths import get_ziya_home
            # Try to find file by checking all known project paths
        except ImportError:
            pass

    # Check if this is a new file creation
    is_new_file = "new file mode" in diff_content or "--- /dev/null" in diff_content
    
    # If not a new file, check that target exists
    if not is_new_file and not os.path.exists(full_path):
        result["model_feedback"] = f"❌ Target file does not exist: {file_path}\nVerify the file path or mark as new file creation with:\nnew file mode 100644\n--- /dev/null"
        return result
    
    # Create a temporary directory for validation
    with tempfile.TemporaryDirectory() as temp_dir:
        temp_file_path = os.path.join(temp_dir, os.path.basename(file_path))
        
        # Copy original file to temp location (if it exists)
        if os.path.exists(full_path):
            shutil.copy2(full_path, temp_file_path)
        
        # Redirect the pipeline to the temp directory using the request-scoped
        # ContextVar instead of mutating the process-global env var.
        from app.context import set_project_root, get_project_root
        original_context_root = get_project_root()
        set_project_root(temp_dir)
        
        # Suppress noisy warnings during validation (dry-run mode)
        diff_logger = logging.getLogger('app.utils.diff_utils')
        atomic_logger = logging.getLogger('app.utils.diff_utils.application.atomic_applier')
        original_diff_level = diff_logger.level
        original_atomic_level = atomic_logger.level
        
        # Set to ERROR to suppress WARNING during dry-run
        diff_logger.setLevel(logging.ERROR)
        atomic_logger.setLevel(logging.ERROR)
        
        try:
            # Run the EXACT same pipeline that would be used for real application
            logger.info(f"🔍 VALIDATION: Running pipeline on temp file: {temp_file_path}")
            logger.info(f"🔍 VALIDATION: Diff content preview: {diff_content[:200]}")
            
            pipeline_result = apply_diff_pipeline(
                git_diff=diff_content,
                file_path=temp_file_path,
                request_id=f"validation-{os.getpid()}"
            )
            
            logger.info(f"🔍 VALIDATION: Pipeline result: {pipeline_result}")
            
            # Extract results
            result["status"] = pipeline_result.get("status", "error")
            result["succeeded_hunks"] = pipeline_result.get("succeeded", [])
            result["failed_hunks"] = pipeline_result.get("failed", [])
            result["already_applied_hunks"] = pipeline_result.get("already_applied", [])
            result["hunk_details"] = pipeline_result.get("hunk_statuses", {})
            result["total_hunks"] = len(result["succeeded_hunks"]) + len(result["failed_hunks"]) + len(result["already_applied_hunks"])
            
            # ANY failure means regeneration required
            has_any_failure = len(result["failed_hunks"]) > 0
            result["can_apply"] = not has_any_failure
            
            # Generate model feedback ONLY on failure
            if has_any_failure:
                result["model_feedback"] = format_model_feedback(
                    file_path=file_path,
                    status=result["status"],
                    succeeded=result["succeeded_hunks"],
                    failed=result["failed_hunks"],
                    already_applied=result["already_applied_hunks"],
                    hunk_details=result["hunk_details"]
                )
                
                # Generate recommendation
                if result["status"] == "partial":
                    result["recommendation"] = f"Regenerate hunks {result['failed_hunks']} with accurate context"
                else:
                    result["recommendation"] = "Regenerate entire diff with complete context from current file state"
            
            logger.info(f"Validation complete: {result['status']} - {len(result['succeeded_hunks'])}/{result['total_hunks']} hunks OK")
            
        finally:
            # Restore original log levels
            diff_logger.setLevel(original_diff_level)
            atomic_logger.setLevel(original_atomic_level)
            # Restore request-scoped project root
            set_project_root(original_context_root)
    
    return result


def format_model_feedback(
    file_path: str,
    status: str,
    succeeded: List[int],
    failed: List[int],
    already_applied: List[int],
    hunk_details: Dict[str, Any]
) -> str:
    """
    Format detailed feedback for the model about validation failures.
    Only called when there are failures.
    """
    feedback_parts = [
        f"The diff for {file_path} cannot be applied. Please provide a corrected diff."
    ]
    
    # Only report failed hunks
    feedback_parts.append(f"\nFailed hunks:")
    for hunk_id in failed:
        hunk_status = hunk_details.get(str(hunk_id), {})
        stage = hunk_status.get("stage", "unknown")
        error_details = hunk_status.get("error_details", {})
        
        if isinstance(error_details, dict):
            error_type = error_details.get("error", "unknown error")
            error_msg = error_details.get("details", "")
        else:
            error_type = str(error_details)
            error_msg = ""
        
        feedback_parts.append(f"- Hunk #{hunk_id}: {error_type}")
        if error_msg:
            feedback_parts.append(f"  ({error_msg})")
    
    feedback_parts.append(
        f"\nRegenerate hunks {', '.join(map(str, failed))} with accurate line numbers "
        f"and at least 5 context lines. Use the current file content above."
    )
    
    return "\n".join(feedback_parts)
