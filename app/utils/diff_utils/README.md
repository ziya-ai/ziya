# diff_utils Package

This package provides utilities for parsing, validating, and applying diffs and patches. It is designed to be modular and maintainable, with clear separation of concerns.

## Package Structure

The package is organized into the following modules:

- **core**: Core utilities and exceptions used throughout the package
- **parsing**: Utilities for parsing diffs and extracting information
- **validation**: Utilities for validating diffs and checking if they can be applied
- **application**: Utilities for applying diffs and patches to files
- **file_ops**: Utilities for file operations related to diffs and patches
- **pipeline**: Pipeline for managing the flow of diff application

## Main Components

### Core

- `PatchApplicationError`: Custom exception for patch application failures
- `clamp`: Utility to ensure a value stays within a range
- `normalize_escapes`: Normalize escape sequences in text for better matching
- `calculate_block_similarity`: Calculate similarity between two blocks of text

### Parsing

- `parse_unified_diff`: Parse a unified diff format and extract hunks
- `parse_unified_diff_exact_plus`: Parse a unified diff with robust handling of embedded markers
- `extract_target_file_from_diff`: Extract the target file path from a git diff
- `split_combined_diff`: Split a combined diff into individual file diffs

### Validation

- `is_new_file_creation`: Determine if a diff represents a new file creation
- `is_hunk_already_applied`: Check if a hunk is already applied at a given position

### Application

- `apply_diff_with_difflib`: Apply a diff to a file using difflib
- `apply_diff_with_difflib_hybrid_forced`: Apply a diff with special case handling
- `use_git_to_apply_code_diff`: Main entry point for applying a git diff

### File Operations

- `create_new_file`: Create a new file from a git diff
- `cleanup_patch_artifacts`: Clean up artifacts left by patch application

### Pipeline

- `apply_diff_pipeline`: Apply a diff using a structured pipeline approach
- `DiffPipeline`: Class for managing the diff application pipeline
- `PipelineStage`: Enum representing the stages of the pipeline
- `HunkStatus`: Enum representing the status of a hunk in the pipeline
- `PipelineResult`: Class representing the result of the pipeline

## Usage

### Using the Pipeline

The recommended way to apply a diff is to use the pipeline:

```python
from app.utils.diff_utils import apply_diff_pipeline

# Apply a git diff to a file
result = apply_diff_pipeline(git_diff, file_path)
print(f"Status: {result['status']}")
print(f"Succeeded hunks: {result['details']['succeeded']}")
print(f"Failed hunks: {result['details']['failed']}")
```

### Using Individual Components

For more fine-grained control, you can use the individual components:

```python
from app.utils.diff_utils import parse_unified_diff_exact_plus, apply_diff_with_difflib

# Parse a diff
hunks = parse_unified_diff_exact_plus(diff_content, file_path)

# Apply a diff
apply_diff_with_difflib(file_path, diff_content)
```
