"""
Debug script to help diagnose issues with the difflib implementation.
This script will run specific test cases with detailed logging.
"""

import os
import sys
import logging
import json
import tempfile
import shutil

# Add project root to Python path
project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, project_root)

# Configure logging
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

# Import necessary modules
from app.utils.code_util import (
    use_git_to_apply_code_diff,
    is_hunk_already_applied,
    calculate_block_similarity,
    find_best_chunk_position,
    apply_diff_with_difflib_hybrid_forced,
    parse_unified_diff_exact_plus
)

def debug_constant_duplicate_check():
    """Debug the constant_duplicate_check test case."""
    print("Debugging constant_duplicate_check test case...")
    
    # Load test case
    test_case_dir = os.path.join(project_root, 'tests', 'diff_test_cases', 'constant_duplicate_check')
    
    with open(os.path.join(test_case_dir, 'original.py'), 'r') as f:
        original = f.read()
    
    with open(os.path.join(test_case_dir, 'changes.diff'), 'r') as f:
        diff = f.read()
    
    with open(os.path.join(test_case_dir, 'expected.py'), 'r') as f:
        expected = f.read()
    
    # Create a temporary directory for testing
    temp_dir = tempfile.mkdtemp()
    try:
        # Set up environment
        os.environ['ZIYA_USER_CODEBASE_DIR'] = temp_dir
        os.environ['ZIYA_FORCE_DIFFLIB'] = '1'
        
        # Create test file
        test_file_path = os.path.join(temp_dir, 'test.py')
        with open(test_file_path, 'w') as f:
            f.write(original)
        
        # Parse the diff to understand what's happening
        print("\nParsing diff content:")
        hunks = list(parse_unified_diff_exact_plus(diff, test_file_path))
        for i, hunk in enumerate(hunks):
            print(f"\nHunk #{i+1}:")
            print(f"  old_start: {hunk['old_start']}")
            print(f"  old_count: {len(hunk['old_block'])}")
            print(f"  old_block: {hunk['old_block']}")
            print(f"  new_lines: {hunk['new_lines']}")
        
        # Apply the diff
        print("\nApplying diff...")
        use_git_to_apply_code_diff(diff, test_file_path)
        
        # Read the result
        with open(test_file_path, 'r') as f:
            result = f.read()
        
        # Compare with expected
        print("\nResult comparison:")
        print(f"Expected:\n{expected}")
        print(f"Got:\n{result}")
        print(f"Match: {result == expected}")
        
        if result != expected:
            print("\nDifferences:")
            import difflib
            diff_lines = list(difflib.unified_diff(
                expected.splitlines(),
                result.splitlines(),
                fromfile='Expected',
                tofile='Got'
            ))
            for line in diff_lines:
                print(line)
        
        # Test is_hunk_already_applied function
        print("\nTesting is_hunk_already_applied:")
        original_lines = original.splitlines()
        for i, hunk in enumerate(hunks):
            for pos in range(len(original_lines) + 1):
                if is_hunk_already_applied(original_lines, hunk, pos):
                    print(f"Hunk #{i+1} is already applied at position {pos}")
        
        # Apply the diff again to see if it detects already applied changes
        print("\nApplying diff again...")
        use_git_to_apply_code_diff(diff, test_file_path)
        
        # Read the result after second application
        with open(test_file_path, 'r') as f:
            result2 = f.read()
        
        print(f"Result after second application:\n{result2}")
        print(f"Match with expected: {result2 == expected}")
        
    finally:
        # Clean up
        shutil.rmtree(temp_dir)

def debug_escape_sequence_content():
    """Debug the escape_sequence_content test case."""
    print("Debugging escape_sequence_content test case...")
    
    # Load test case
    test_case_dir = os.path.join(project_root, 'tests', 'diff_test_cases', 'escape_sequence_content')
    
    with open(os.path.join(test_case_dir, 'original.py'), 'r') as f:
        original = f.read()
    
    with open(os.path.join(test_case_dir, 'changes.diff'), 'r') as f:
        diff = f.read()
    
    with open(os.path.join(test_case_dir, 'expected.py'), 'r') as f:
        expected = f.read()
    
    # Create a temporary directory for testing
    temp_dir = tempfile.mkdtemp()
    try:
        # Set up environment
        os.environ['ZIYA_USER_CODEBASE_DIR'] = temp_dir
        os.environ['ZIYA_FORCE_DIFFLIB'] = '1'
        
        # Create test file
        test_file_path = os.path.join(temp_dir, 'test.py')
        with open(test_file_path, 'w') as f:
            f.write(original)
        
        # Parse the diff to understand what's happening
        print("\nParsing diff content:")
        hunks = list(parse_unified_diff_exact_plus(diff, test_file_path))
        for i, hunk in enumerate(hunks):
            print(f"\nHunk #{i+1}:")
            print(f"  old_start: {hunk['old_start']}")
            print(f"  old_count: {len(hunk['old_block'])}")
            print(f"  old_block: {hunk['old_block']}")
            print(f"  new_lines: {hunk['new_lines']}")
        
        # Apply the diff
        print("\nApplying diff...")
        use_git_to_apply_code_diff(diff, test_file_path)
        
        # Read the result
        with open(test_file_path, 'r') as f:
            result = f.read()
        
        # Compare with expected
        print("\nResult comparison:")
        print(f"Expected:\n{expected}")
        print(f"Got:\n{result}")
        print(f"Match: {result == expected}")
        
    finally:
        # Clean up
        shutil.rmtree(temp_dir)

def debug_line_calculation_fix():
    """Debug the line_calculation_fix test case."""
    print("Debugging line_calculation_fix test case...")
    
    # Load test case
    test_case_dir = os.path.join(project_root, 'tests', 'diff_test_cases', 'line_calculation_fix')
    
    with open(os.path.join(test_case_dir, 'original.py'), 'r') as f:
        original = f.read()
    
    with open(os.path.join(test_case_dir, 'changes.diff'), 'r') as f:
        diff = f.read()
    
    with open(os.path.join(test_case_dir, 'expected.py'), 'r') as f:
        expected = f.read()
    
    # Create a temporary directory for testing
    temp_dir = tempfile.mkdtemp()
    try:
        # Set up environment
        os.environ['ZIYA_USER_CODEBASE_DIR'] = temp_dir
        os.environ['ZIYA_FORCE_DIFFLIB'] = '1'
        
        # Create test file
        test_file_path = os.path.join(temp_dir, 'test.py')
        with open(test_file_path, 'w') as f:
            f.write(original)
        
        # Parse the diff to understand what's happening
        print("\nParsing diff content:")
        hunks = list(parse_unified_diff_exact_plus(diff, test_file_path))
        for i, hunk in enumerate(hunks):
            print(f"\nHunk #{i+1}:")
            print(f"  old_start: {hunk['old_start']}")
            print(f"  old_count: {len(hunk['old_block'])}")
            print(f"  old_block: {hunk['old_block']}")
            print(f"  new_lines: {hunk['new_lines']}")
        
        # Test find_best_chunk_position
        print("\nTesting find_best_chunk_position:")
        original_lines = original.splitlines()
        for i, hunk in enumerate(hunks):
            old_start = hunk['old_start'] - 1  # Convert to 0-based
            best_pos, best_ratio = find_best_chunk_position(original_lines, hunk['old_block'], old_start)
            print(f"Hunk #{i+1}: best_pos={best_pos}, best_ratio={best_ratio:.2f}")
            
            # Check if the position is correct
            if best_pos + len(hunk['old_block']) <= len(original_lines):
                window = original_lines[best_pos:best_pos + len(hunk['old_block'])]
                print(f"  Window at best_pos: {window}")
                print(f"  Old block: {hunk['old_block']}")
                print(f"  Match: {window == hunk['old_block']}")
        
        # Apply the diff
        print("\nApplying diff...")
        use_git_to_apply_code_diff(diff, test_file_path)
        
        # Read the result
        with open(test_file_path, 'r') as f:
            result = f.read()
        
        # Compare with expected
        print("\nResult comparison:")
        print(f"Expected:\n{expected}")
        print(f"Got:\n{result}")
        print(f"Match: {result == expected}")
        
    finally:
        # Clean up
        shutil.rmtree(temp_dir)

def debug_alarm_actions_refactor():
    """Debug the alarm_actions_refactor test case."""
    print("Debugging alarm_actions_refactor test case...")
    
    # Load test case
    test_case_dir = os.path.join(project_root, 'tests', 'diff_test_cases', 'alarm_actions_refactor')
    
    with open(os.path.join(test_case_dir, 'original.ts'), 'r') as f:
        original = f.read()
    
    with open(os.path.join(test_case_dir, 'changes.diff'), 'r') as f:
        diff = f.read()
    
    with open(os.path.join(test_case_dir, 'expected.ts'), 'r') as f:
        expected = f.read()
    
    # Create a temporary directory for testing
    temp_dir = tempfile.mkdtemp()
    try:
        # Set up environment
        os.environ['ZIYA_USER_CODEBASE_DIR'] = temp_dir
        os.environ['ZIYA_FORCE_DIFFLIB'] = '1'
        
        # Create test file and directory structure
        os.makedirs(os.path.join(temp_dir, 'lib', 'stacks'), exist_ok=True)
        test_file_path = os.path.join(temp_dir, 'lib', 'stacks', 'CloudWatchMonitoringCDKStack.ts')
        with open(test_file_path, 'w') as f:
            f.write(original)
        
        # Parse the diff to understand what's happening
        print("\nParsing diff content:")
        hunks = list(parse_unified_diff_exact_plus(diff, test_file_path))
        for i, hunk in enumerate(hunks):
            print(f"\nHunk #{i+1}:")
            print(f"  old_start: {hunk['old_start']}")
            print(f"  old_count: {len(hunk['old_block'])}")
            print(f"  old_block: {hunk['old_block']}")
            print(f"  new_lines: {hunk['new_lines']}")
        
        # Apply the diff
        print("\nApplying diff...")
        use_git_to_apply_code_diff(diff, test_file_path)
        
        # Read the result
        with open(test_file_path, 'r') as f:
            result = f.read()
        
        # Compare with expected
        print("\nResult comparison:")
        print(f"Expected length: {len(expected)}")
        print(f"Got length: {len(result)}")
        print(f"Match: {result == expected}")
        
        if result != expected:
            print("\nDifferences:")
            import difflib
            diff_lines = list(difflib.unified_diff(
                expected.splitlines(),
                result.splitlines(),
                fromfile='Expected',
                tofile='Got'
            ))
            for line in diff_lines:
                print(line)
        
    finally:
        # Clean up
        shutil.rmtree(temp_dir)

if __name__ == "__main__":
    # Run debug functions
    debug_constant_duplicate_check()
    print("\n" + "=" * 80 + "\n")
    debug_escape_sequence_content()
    print("\n" + "=" * 80 + "\n")
    debug_line_calculation_fix()
    print("\n" + "=" * 80 + "\n")
    debug_alarm_actions_refactor()
