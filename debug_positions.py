#!/usr/bin/env python3
"""Debug script to see what positions are being calculated"""

import sys
import os
import tempfile
sys.path.insert(0, '/Users/dcohn/workspace/ziya-release-verify')

from app.utils.diff_utils.application.patch_apply import apply_diff_with_hybrid_difflib_forced_inlined

# Load test files
test_dir = '/Users/dcohn/workspace/ziya-release-verify/tests/diff_test_cases/markdown_renderer_language_cache'

with open(os.path.join(test_dir, 'original.tsx'), 'r') as f:
    original = f.read()

with open(os.path.join(test_dir, 'changes.diff'), 'r') as f:
    diff = f.read()

# Create temp file
with tempfile.NamedTemporaryFile(mode='w', suffix='.tsx', delete=False) as f:
    f.write(original)
    temp_file = f.name

try:
    # Apply the diff
    original_lines = original.splitlines(keepends=True)
    result_lines = apply_diff_with_hybrid_difflib_forced_inlined(
        temp_file,
        diff,
        original_lines,
        skip_hunks=[]
    )
    
    result = ''.join(result_lines)
    
    # Show a snippet of the result around the problem area
    result_lines_no_endings = result.splitlines()
    print(f"Total result lines: {len(result_lines_no_endings)}")
    print()
    print("Lines 55-85 of result:")
    for i in range(54, min(85, len(result_lines_no_endings))):
        print(f"{i+1:3d}: {result_lines_no_endings[i][:80]}")
    
finally:
    os.unlink(temp_file)
