#!/usr/bin/env python3
"""Validate that test case diffs match expected outputs using system patch."""

import os
import subprocess
import tempfile
from pathlib import Path

TEST_DIR = Path("tests/diff_test_cases")

def validate_test_case(test_name):
    """Check if applying the diff to original produces the expected output."""
    test_path = TEST_DIR / test_name
    
    original_file = test_path / "original.py"
    if not original_file.exists():
        # Try other extensions
        for ext in ['.ts', '.tsx', '.js', '.json', '.yaml']:
            original_file = test_path / f"original{ext}"
            if original_file.exists():
                break
        else:
            return None, "No original file found"
    
    diff_file = test_path / "changes.diff"
    expected_file = test_path / f"expected{original_file.suffix}"
    
    if not diff_file.exists() or not expected_file.exists():
        return None, "Missing diff or expected file"
    
    # Create temp file with original content
    with tempfile.NamedTemporaryFile(mode='w', suffix=original_file.suffix, delete=False) as f:
        f.write(original_file.read_text())
        temp_file = f.name
    
    try:
        # Try system patch
        result = subprocess.run(
            ['patch', '-s', temp_file, str(diff_file)],
            capture_output=True,
            text=True
        )
        
        if result.returncode != 0:
            os.unlink(temp_file)
            return False, f"Patch failed: {result.stderr[:100]}"
        
        # Compare with expected
        patched_content = Path(temp_file).read_text()
        expected_content = expected_file.read_text()
        
        os.unlink(temp_file)
        
        if patched_content == expected_content:
            return True, "Valid"
        else:
            return False, f"Output mismatch: {len(patched_content)} vs {len(expected_content)} chars"
    
    except Exception as e:
        if os.path.exists(temp_file):
            os.unlink(temp_file)
        return None, f"Error: {str(e)}"

if __name__ == "__main__":
    test_cases = sorted([d.name for d in TEST_DIR.iterdir() if d.is_dir()])
    
    valid = []
    invalid = []
    unknown = []
    
    for test_name in test_cases:
        result, message = validate_test_case(test_name)
        if result is True:
            valid.append(test_name)
        elif result is False:
            invalid.append((test_name, message))
        else:
            unknown.append((test_name, message))
    
    print(f"Valid test cases: {len(valid)}")
    print(f"Invalid test cases: {len(invalid)}")
    print(f"Unknown/skipped: {len(unknown)}")
    print()
    
    if invalid:
        print("INVALID TEST CASES:")
        for name, msg in invalid:
            print(f"  {name}: {msg}")
