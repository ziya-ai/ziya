"""
Patch script to apply the improved difflib functions to code_util.py.
This script will:
1. Read the original code_util.py
2. Replace the target functions with improved versions
3. Write the updated code back to code_util.py
"""

import os
import re
import sys

# Add project root to Python path
project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, project_root)

# Import the improved functions
from app.utils.code_util_integration import (
    normalize_escapes,
    calculate_block_similarity,
    is_hunk_already_applied,
    find_best_chunk_position,
    apply_diff_with_difflib_hybrid_forced
)

# Path to the original code_util.py
code_util_path = os.path.join(project_root, 'app', 'utils', 'code_util.py')

# Read the original file
with open(code_util_path, 'r') as f:
    original_code = f.read()

# Define the functions to replace
functions_to_replace = {
    'calculate_block_similarity': calculate_block_similarity,
    'is_hunk_already_applied': is_hunk_already_applied,
    'find_best_chunk_position': find_best_chunk_position,
    'apply_diff_with_difflib_hybrid_forced': apply_diff_with_difflib_hybrid_forced
}

# Add the normalize_escapes function
normalize_escapes_code = """
def normalize_escapes(text: str) -> str:
    \"\"\"
    Normalize escape sequences in text to improve matching.
    This helps with comparing strings that have different escape sequence representations.
    \"\"\"
    # Replace common escape sequences with placeholders
    replacements = {
        '\\\\n': '_NL_',
        '\\\\r': '_CR_',
        '\\\\t': '_TAB_',
        '\\\\"': '_QUOTE_',
        "\\\\'": '_SQUOTE_',
        '\\\\\\\\': '_BSLASH_'
    }
    
    result = text
    for esc, placeholder in replacements.items():
        result = result.replace(esc, placeholder)
    
    return result
"""

# Function to extract a function's source code
def get_function_source(func):
    import inspect
    return inspect.getsource(func)

# Replace each function in the original code
modified_code = original_code

# First add the normalize_escapes function after the imports
import_section_end = re.search(r'(^class PatchApplicationError|^def)', modified_code, re.MULTILINE).start()
modified_code = modified_code[:import_section_end] + normalize_escapes_code + modified_code[import_section_end:]

# Then replace each target function
for func_name, func in functions_to_replace.items():
    # Get the source code of the improved function
    new_func_source = get_function_source(func)
    
    # Find the original function in the code
    pattern = rf'def {func_name}\([^)]*\).*?(?=\n\w+\s*\w+|$)'
    match = re.search(pattern, modified_code, re.DOTALL)
    
    if match:
        # Replace the function
        modified_code = modified_code[:match.start()] + new_func_source + modified_code[match.end():]
    else:
        print(f"Warning: Could not find function {func_name} in the original code")

# Write the modified code back to the file
with open(code_util_path, 'w') as f:
    f.write(modified_code)

print(f"Successfully patched {code_util_path} with improved difflib functions")
