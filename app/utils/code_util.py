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
        
    Returns:
        A dictionary with the result of the operation
    """
    # For all cases, use the pipeline implementation
    return apply_diff_pipeline(git_diff, file_path)
