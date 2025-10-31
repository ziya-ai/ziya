#!/usr/bin/env python3
import sys
sys.path.insert(0, '/Users/dcohn/workspace/ziya-release-verify')

from app.utils.diff_utils.application.patch_apply import parse_unified_diff_exact_plus, normalize_line_for_comparison

# Load files
with open('tests/diff_test_cases/markdown_renderer_language_cache/original.tsx') as f:
    original_lines = f.readlines()

with open('tests/diff_test_cases/markdown_renderer_language_cache/changes.diff') as f:
    diff = f.read()

# Parse hunk
hunks = list(parse_unified_diff_exact_plus(diff, "test.tsx"))
h = hunks[0]

old_block = h['old_block']
print(f"old_block has {len(old_block)} lines")
print(f"original file has {len(original_lines)} lines")
print()

# Check position 2 (where it matched)
pos = 2
file_slice = original_lines[pos:pos + len(old_block)]
print(f"Checking position {pos}:")
print(f"  file_slice length: {len(file_slice)}")
print()

# Normalize and compare
normalized_file = [normalize_line_for_comparison(line) for line in file_slice]
normalized_old = [normalize_line_for_comparison(line) for line in old_block]

print("First 5 normalized file lines:")
for line in normalized_file[:5]:
    print(f"  {repr(line[:60])}")
print()

print("First 5 normalized old_block lines:")
for line in normalized_old[:5]:
    print(f"  {repr(line[:60])}")
print()

if normalized_file == normalized_old:
    print("✓ EXACT MATCH at position 2")
else:
    print("✗ NO MATCH at position 2")
    # Find first mismatch
    for i, (f, o) in enumerate(zip(normalized_file, normalized_old)):
        if f != o:
            print(f"  First mismatch at line {i}:")
            print(f"    File: {repr(f[:80])}")
            print(f"    Old:  {repr(o[:80])}")
            break
