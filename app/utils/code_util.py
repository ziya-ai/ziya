"""
Legacy module for backward compatibility.
This module re-exports all functionality from the diff_utils package.
"""

# Re-export everything from diff_utils
from app.utils.diff_utils import *

# For backward compatibility
from app.utils.diff_utils.core.exceptions import PatchApplicationError
from app.utils.diff_utils.core.utils import clamp, normalize_escapes, calculate_block_similarity
from app.utils.diff_utils.parsing.diff_parser import parse_unified_diff, parse_unified_diff_exact_plus
from app.utils.diff_utils.parsing.diff_parser import extract_target_file_from_diff, split_combined_diff
from app.utils.diff_utils.validation.validators import is_new_file_creation, is_hunk_already_applied
from app.utils.diff_utils.application.patch_apply import apply_diff_with_difflib, apply_diff_with_difflib_hybrid_forced
from app.utils.diff_utils.application.git_diff import use_git_to_apply_code_diff, correct_git_diff
from app.utils.diff_utils.file_ops.file_handlers import create_new_file, cleanup_patch_artifacts
from app.utils.diff_utils.pipeline import apply_diff_pipeline, DiffPipeline, PipelineStage, HunkStatus, PipelineResult

# Define HunkData class for backward compatibility

def clean_backtick_sequences(text):
    """
    Clean backtick sequences from text.
    This is used by the parse_output function to clean code blocks.
    """
    if not text:
        return ""
    
    # If the text starts with ```diff, it's a diff block
    if "```diff" in text:
        return text
    
    # If the text contains backtick code blocks, extract the content
    if "```" in text:
        # Simple extraction of code blocks
        lines = text.split("\n")
        in_code_block = False
        cleaned_lines = []
        
        for line in lines:
            if line.startswith("```") and not in_code_block:
                in_code_block = True
                # Skip the opening backticks line
                continue
            elif line.startswith("```") and in_code_block:
                in_code_block = False
                # Skip the closing backticks line
                continue
            else:
                cleaned_lines.append(line)
        
        return "\n".join(cleaned_lines)
    
    return text
class HunkData:
    """
    Stores data for a single hunk in the unified diff: header, start_line, old_lines, new_lines, etc.
    Also includes optional context fields if needed (context_before, context_after).
    """
    def __init__(self, header='', start_line=1, old_lines=None, new_lines=None,
                 context_before=None, context_after=None):
        self.header = header
        self.start_line = start_line
        self.old_lines = old_lines or []
        self.new_lines = new_lines or []
        self.context_before = context_before or []
        self.context_after = context_after or []

    def __repr__(self):
        return (f"<HunkData start_line={self.start_line} "
                f"old={len(self.old_lines)} new={len(self.new_lines)}>")

# Constants for backward compatibility
MIN_CONFIDENCE = 0.72  # what confidence level we cut off forced diff apply after fuzzy match
MAX_OFFSET = 5        # max allowed line offset before considering a hunk apply failed

# For backward compatibility, provide the original function as the main entry point
def use_git_to_apply_code_diff_legacy(git_diff: str, file_path: str) -> None:
    """
    Legacy function for backward compatibility.
    Use apply_diff_pipeline instead for new code.
    """
    from app.utils.diff_utils.application.git_diff import use_git_to_apply_code_diff as original_func
    return original_func(git_diff, file_path)

# Replace the original function with the pipeline-based version
def use_git_to_apply_code_diff(git_diff: str, file_path: str):
    """
    Apply a git diff to a file using the refactored diff_utils package.
    
    Args:
        git_diff: The git diff to apply
        file_path: Path to the file to modify
        
<<<<<<< HEAD
    Returns:
        A dictionary with the result of the operation
    """
    # For all cases, use the pipeline implementation
    return apply_diff_pipeline(git_diff, file_path)
=======
    # If force difflib flag is set, skip system patch entirely
    if os.environ.get('ZIYA_FORCE_DIFFLIB'):
        logger.info("Force difflib mode enabled, bypassing system patch")
        try:
            apply_diff_with_difflib(file_path, git_diff)
            return
        except Exception as e:
            raise PatchApplicationError(str(e), {"status": "error", "type": "difflib_error"})

    results = {"succeeded": [], "already_applied": [], "failed": []}

    # Read original content before any modifications
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            original_content = f.read()
    except FileNotFoundError:
        original_content = ""

    try:
        # Check if file exists before attempting patch
        if not os.path.exists(file_path) and not is_new_file_creation(diff_lines):
            raise PatchApplicationError(f"Target file does not exist: {file_path}", {
                "status": "error",
                "type": "missing_file",
                "file": file_path
            })
        logger.info("Starting patch application pipeline...")
        logger.debug("About to run patch command with:")
        logger.debug(f"CWD: {user_codebase_dir}")
        logger.debug(f"Input length: {len(git_diff)} bytes")
        changes_written = False
        # Do a dry run to see what we're up against on first pass
        patch_result = subprocess.run(
            ['patch', '-p1', '--forward', '--no-backup-if-mismatch', '--reject-file=-', '--batch', '--ignore-whitespace', '--verbose', '--dry-run', '-i', '-'],
            input=git_diff,
            encoding='utf-8',
            cwd=user_codebase_dir,
            capture_output=True,
            text=True,
            timeout=10
        )
        logger.debug(f"stdout: {patch_result.stdout}")
        logger.debug(f"stderr: {patch_result.stderr}")
        logger.debug(f"Return code: {patch_result.returncode}")

        hunk_status = {}
        patch_output = ""
        file_was_modified = False
        has_line_mismatch = False
        has_large_offset = False
        has_fuzz = False
        patch_reports_success = False

        # Parse the dry run output
        dry_run_status = parse_patch_output(patch_result.stdout)
        hunk_status = dry_run_status
        already_applied = (not "No file to patch" in patch_result.stdout and "Reversed (or previously applied)" in patch_result.stdout and
                         "failed" not in patch_result.stdout.lower())
        logger.debug("Returned from dry run, processing results...")
        logger.debug(f"Dry run status: {dry_run_status}")

        # If patch indicates changes are already applied, return success
        if already_applied:
            logger.info("All changes are already applied")
            return {"status": "success", "details": {
                "succeeded": [],
                "failed": [],
                "failed": [],
                "already_applied": list(dry_run_status.keys())
            }}

        # Apply successful hunks with system patch if any
        # fixme: we should probably be iterating success only, but this will also hit already applied cases
        if any(success for success in dry_run_status.values()):
            logger.info(f"Applying successful hunks ({sum(1 for v in dry_run_status.values() if v)}/{len(dry_run_status)}) with system patch...")
            patch_result = subprocess.run(
                ['patch', '-p1', '--forward', '--no-backup-if-mismatch', '--reject-file=-', '--batch', '--ignore-whitespace', '--verbose', '-i', '-'],
                input=git_diff,
                encoding='utf-8',
                cwd=user_codebase_dir,
                capture_output=True,
                text=True,
                timeout=10
            )

            # Actually write the successful changes
            if "misordered hunks" in patch_result.stderr:
                logger.warning("Patch reported misordered hunks - falling back to difflib")
                # Skip to difflib application
                apply_diff_with_difflib(file_path, git_diff)
                return
            elif patch_result.returncode == 0:
                logger.info("Successfully applied some hunks with patch, writing changes")
                # Verify changes were actually written
                changes_written = True

            else:
                logger.warning("Patch application had mixed results")

            patch_output = patch_result.stdout
            logger.debug(f"Raw (system) patch stdout:\n{patch_output}")
            logger.debug(f"Raw (system) patch stdout:\n{patch_result.stderr}")
            hunk_status = parse_patch_output(patch_output)

        # Record results from patch stage
        for hunk_num, success in dry_run_status.items():
            if success:
                if "Reversed (or previously applied)" in patch_output and f"Hunk #{hunk_num}" in patch_output:
                    logger.info(f"Hunk #{hunk_num} was already applied")
                    results["already_applied"].append(hunk_num)
                else:
                    logger.info(f"Hunk #{hunk_num} applied successfully")
                    results["succeeded"].append(hunk_num)
                    changes_written = True
            else:
                logger.info(f"Hunk #{hunk_num} failed to apply")
                results["failed"].append(hunk_num)

        if results["succeeded"] or results["already_applied"]:
            logger.info(f"Successfully applied {len(results['succeeded'])} hunks, "
                      f"{len(results['already_applied'])} were already applied")
            changes_written = True

        # If any hunks failed, extract them to pass onto next pipeline stage
        if results["failed"]:
            logger.info(f"Extracting {len(results['failed'])} failed hunks for next stage")
            git_diff = extract_remaining_hunks(git_diff, {h: False for h in results["failed"]})
        else:
            logger.info("Exiting pipeline die to full success condition.")
            return {"status": "success", "details": results}

        # Proceed with git apply if we have any failed hunks
        if results["failed"]:
            logger.debug("Some failed hunks reported, processing..")
            if not git_diff.strip():
                logger.warning("No valid hunks remaining to process")
                return {"status": "partial", "details": results}
            temp_path = None
            logger.info("Proceeding with git apply for remaining hunks")
            try:
                with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', suffix='.diff', delete=False) as temp_file:
                    temp_file.write(git_diff)
                    temp_path = temp_file.name

                git_result = subprocess.run(
                    ['git', 'apply', '--verbose', '--ignore-whitespace',
                     '--ignore-space-change', '--whitespace=nowarn',
                     '--check', temp_path],
                    cwd=user_codebase_dir,
                    capture_output=True,
                    text=True
                )

                if "patch does not apply" not in git_result.stderr:
                    logger.info("Changes already applied according to git apply --check")
                    return {"status": "success", "details": {
                        "succeeded": [],
                        "failed": [],
                        "already_applied": results["failed"]
                    }}

                git_result = subprocess.run(
                    ['git', 'apply', '--verbose', '--ignore-whitespace',
                     '--ignore-space-change', '--whitespace=nowarn',
                     '--reject', temp_path],
                    cwd=user_codebase_dir,
                    capture_output=True,
                    text=True
                )

                logger.debug(f"Git apply stdout:\n{git_result.stdout}")
                logger.debug(f"Git apply stderr:\n{git_result.stderr}")

                if git_result.returncode == 0:
                    logger.info("Git apply succeeded")
                    # Move hunks from failed to succeeded
                    for hunk_num in results["failed"][:]:
                        results["failed"].remove(hunk_num)
                        results["succeeded"].append(hunk_num)
                    changes_written = True
                    return {"status": "success", "details": results}
                elif "already applied" in git_result.stderr:
                    # Move hunks from failed to already_applied
                    for hunk_num in results["failed"][:]:
                        results["failed"].remove(hunk_num)
                        results["already_applied"].append(hunk_num)
                        logger.info(f"Marking hunk {hunk_num} as already applied and continuing")
                else:
                    logger.info("Git apply failed, moving to difflib stage...")
                    # Continue to difflib
            finally:
                if os.path.exists(temp_path):
                    os.unlink(temp_path)

            # If git apply failed, try difflib with the same hunks we just tried
            logger.info("Attempting to apply changes with difflib")
            try:
                logger.info("Starting difflib application...")
                # Parse the remaining hunks for difflib
                if git_diff:
                    logger.debug(f"Passing to difflib:\n{git_diff}")
                    try:
                        apply_diff_with_difflib(file_path, git_diff)
                        # If difflib succeeds, move remaining failed hunks to succeeded
                        for hunk_num in results["failed"][:]:
                            results["failed"].remove(hunk_num)
                            results["succeeded"].append(hunk_num)
                        changes_written = True
                        return {"status": "success", "details": results}
                    except Exception as e:
                        if isinstance(e, PatchApplicationError) and e.details.get("type") == "already_applied":
                            # Move failed hunks to already_applied
                            for hunk_num in results["failed"][:]:
                                results["failed"].remove(hunk_num)
                                results["already_applied"].append(hunk_num)
                            return {"status": "success", "details": results}
                        logger.error(f"Difflib application failed: {str(e)}")
                        raise
            except PatchApplicationError as e:
                logger.error(f"Difflib application failed: {str(e)}")
                if e.details.get("type") == "already_applied":
                    return {"status": "success", "details": results}
                if changes_written:
                    return {"status": "partial", "details": results}
                raise
        else:
            logger.debug("Unreachable? No hunks reported failure, exiting pipeline after system patch stage.")

    except Exception as e:
        logger.error(f"Error applying patch: {str(e)}")
        raise
    finally:
        cleanup_patch_artifacts(user_codebase_dir, file_path)

    # Return final status
    if len(results["failed"]) == 0:
        return {"status": "success", "details": results}
    elif changes_written:
        return {"status": "partial", "details": results}
    return {"status": "error", "details": results}

def parse_patch_output(patch_output: str) -> Dict[int, bool]:
    """Parse patch command output to determine which hunks succeeded/failed.
    Returns a dict mapping hunk number to success status."""
    hunk_status = {}
    logger.debug(f"Parsing patch output:\n{patch_output}")

    in_patch_output = False
    current_hunk = None
    for line in patch_output.splitlines():
        if "Patching file" in line:
            in_patch_output = True
            continue
        if not in_patch_output:
            continue

        # Track the current hunk number
        hunk_match = re.search(r'Hunk #(\d+)', line)
        if hunk_match:
            current_hunk = int(hunk_match.group(1))

        # Check for significant adjustments that should invalidate "success"
        if current_hunk is not None:
            if "succeeded at" in line:
                hunk_status[current_hunk] = True
                logger.debug(f"Hunk {current_hunk} succeeded")
            elif "failed" in line:
                logger.debug(f"Hunk {current_hunk} failed")

        # Match lines like "Hunk #1 succeeded at 6."
        match = re.search(r'Hunk #(\d+) (succeeded at \d+(?:\s+with fuzz \d+)?|failed)', line)
        if match:
            hunk_num = int(match.group(1))
            # Consider both clean success and fuzzy matches as successful
            success = 'succeeded' in match.group(2)
            hunk_status[hunk_num] = success
            logger.debug(f"Found hunk {hunk_num}: {'succeeded' if success else 'failed'}")

    logger.debug(f"Final hunk status: {hunk_status}")
    return hunk_status

def extract_remaining_hunks(git_diff: str, hunk_status: Dict[int,bool]) -> str:
    """Extract hunks that weren't successfully applied."""
    logger.debug("Extracting remaining hunks from diff")

    logger.debug(f"Hunk status before extraction: {json.dumps(hunk_status, indent=2)}")

    # Parse the original diff into hunks
    lines = git_diff.splitlines()
    hunks = []
    current_hunk = []
    headers = []
    hunk_count = 0
    in_hunk = False

    for line in lines:
        if line.startswith(('diff --git', '--- ', '+++ ')):
            headers.append(line)
        elif line.startswith('@@'):
            hunk_count += 1
            if current_hunk:
                if current_hunk:
                    hunks.append((hunk_count - 1, current_hunk))

            # Only start collecting if this hunk failed
            if hunk_count in hunk_status and not hunk_status[hunk_count]:
                logger.debug(f"Including failed hunk #{hunk_count}")
                current_hunk = [f"{line} Hunk #{hunk_count}"]
                in_hunk = True
            else:
                logger.debug(f"Skipping successful hunk #{hunk_count}")
                current_hunk = []
                in_hunk = False
        elif in_hunk:
            current_hunk.append(line)
            if not line.startswith((' ', '+', '-', '\\')):
                # End of hunk reached
                if current_hunk:
                    hunks.append(current_hunk)
                current_hunk = []
                in_hunk = False

    if current_hunk:
        hunks.append((hunk_count, current_hunk))

    # Build final result with proper spacing
    result = []
    result.extend(headers)
    for _, hunk_lines in hunks:
        result.extend(hunk_lines)

    if not result:
        logger.warning("No hunks to extract")
        return ''

    final_diff = '\n'.join(result) + '\n'
    logger.debug(f"Extracted diff for remaining hunks:\n{final_diff}")
    return final_diff
>>>>>>> 839af8b (Backend minor fixes (#26))
