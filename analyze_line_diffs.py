import sys
import os
import tempfile
from pathlib import Path

sys.path.insert(0, '.')
os.environ['ZIYA_USER_CODEBASE_DIR'] = tempfile.gettempdir()

import logging
logging.getLogger().setLevel(logging.CRITICAL)

from app.utils.code_util import use_git_to_apply_code_diff

# Tests with line count differences from earlier analysis
tests = [
    ('MRE_comment_only_changes', 87, 89, +2),
    ('MRE_context_empty_line', 8, 15, +7),
    ('MRE_duplicate_state_declaration', 163, 159, -4),
    ('MRE_incorrect_hunk_offsets', 84, 88, +4),
    ('included_inline_unicode', 910, 911, +1),
    ('json_escape_sequence', 15, 13, -2),
    ('long_multipart_emptylines', 82, 80, -2),
]

for test_name, exp_lines, act_lines, diff in tests:
    test_dir = Path(f'tests/diff_test_cases/{test_name}')
    
    # Find files
    original_file = None
    for ext in ['.py', '.ts', '.tsx', '.js']:
        orig = test_dir / f'original{ext}'
        if orig.exists():
            original_file = orig
            expected_file = test_dir / f'expected{ext}'
            break
    
    if not original_file:
        continue
    
    print(f"\n{'='*80}")
    print(f"TEST: {test_name}")
    print(f"Expected: {exp_lines} lines, Actual: {act_lines} lines, Diff: {diff:+d}")
    print('='*80)
    
    # Read diff to see hunk headers
    diff_content = (test_dir / 'changes.diff').read_text()
    
    # Extract hunk headers
    import re
    hunks = re.findall(r'^@@ -(\d+),?\d* \+(\d+),?\d* @@.*$', diff_content, re.MULTILINE)
    
    print(f"Hunks: {len(hunks)}")
    for i, (old_start, new_start) in enumerate(hunks, 1):
        print(f"  Hunk {i}: @@ -{old_start} +{new_start} @@")
    
    # Check original file line count
    original = original_file.read_text()
    orig_lines = len(original.splitlines())
    print(f"Original file: {orig_lines} lines")
    
    # Check if hunk line numbers are way off
    if hunks:
        max_hunk_line = max(int(old_start) for old_start, _ in hunks)
        if max_hunk_line > orig_lines:
            print(f"⚠️  Hunk references line {max_hunk_line} but file only has {orig_lines} lines!")
            print(f"   Offset: {max_hunk_line - orig_lines} lines too high")
