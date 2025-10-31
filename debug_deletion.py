#!/usr/bin/env python3
"""Debug script to trace deletion bug"""

import sys
sys.path.insert(0, '/Users/dcohn/workspace/ziya-release-verify')

from app.utils.diff_utils.application.patch_apply import parse_unified_diff_exact_plus

# Load the diff
with open('/Users/dcohn/workspace/ziya-release-verify/tests/diff_test_cases/markdown_renderer_language_cache/changes.diff', 'r') as f:
    diff_content = f.read()

# Parse hunks
hunks = list(parse_unified_diff_exact_plus(diff_content, "test.tsx"))

print(f"Number of hunks: {len(hunks)}")
print()

for i, h in enumerate(hunks, 1):
    print(f"=== HUNK #{i} ===")
    print(f"old_start: {h['old_start']}")
    print(f"old_count: {h['old_count']}")  # From header
    print(f"new_start: {h['new_start']}")
    print(f"new_count: {h['new_count']}")
    print(f"len(old_block): {len(h.get('old_block', []))}")  # Actual lines in diff
    print(f"len(new_lines): {len(h.get('new_lines', []))}")
    print(f"len(removed_lines): {len(h.get('removed_lines', []))}")
    print(f"len(added_lines): {len(h.get('added_lines', []))}")
    print()
    print("First 5 old_block lines:")
    for line in h.get('old_block', [])[:5]:
        print(f"  {repr(line[:60])}")
    print()
    print("First 5 new_lines:")
    for line in h.get('new_lines', [])[:5]:
        print(f"  {repr(line[:60])}")
    print()
