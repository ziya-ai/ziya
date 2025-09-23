#!/usr/bin/env python3
"""
Fix for the files_processed counter bug in Ziya directory scanning.

The bug: files_processed counter is only incremented in count_tokens_accurate() 
but the main processing loop uses estimate_tokens_fast() and never increments the counter.

This results in logs showing "177 dirs, 0 files" even when files are being processed.
"""

import os
import sys

def apply_fix():
    """Apply the fix to directory_util.py"""
    
    file_path = os.path.join(os.path.dirname(__file__), 'app', 'utils', 'directory_util.py')
    
    if not os.path.exists(file_path):
        print(f"Error: {file_path} not found")
        return False
    
    # Read the current file
    with open(file_path, 'r') as f:
        content = f.read()
    
    # Find the section where we need to add the counter increment
    # Look for the line: elif os.path.isfile(entry_path):
    
    old_code = '''            elif os.path.isfile(entry_path):
                tokens = estimate_tokens_fast(entry_path)
                if tokens > 0:
                    result['children'][entry] = {'token_count': tokens}
                    total_tokens += tokens'''
    
    new_code = '''            elif os.path.isfile(entry_path):
                tokens = estimate_tokens_fast(entry_path)
                if tokens > 0:
                    scan_stats['files_processed'] += 1  # Fix: increment counter for processed files
                    result['children'][entry] = {'token_count': tokens}
                    total_tokens += tokens'''
    
    if old_code in content:
        # Apply the fix
        fixed_content = content.replace(old_code, new_code)
        
        # Write the fixed content back
        with open(file_path, 'w') as f:
            f.write(fixed_content)
        
        print("✅ Fix applied successfully!")
        print("The files_processed counter will now be incremented correctly.")
        return True
    else:
        print("❌ Could not find the target code section to fix.")
        print("The file may have been modified or the bug may already be fixed.")
        return False

def verify_fix():
    """Verify that the fix has been applied correctly."""
    
    file_path = os.path.join(os.path.dirname(__file__), 'app', 'utils', 'directory_util.py')
    
    if not os.path.exists(file_path):
        print(f"Error: {file_path} not found")
        return False
    
    with open(file_path, 'r') as f:
        content = f.read()
    
    # Check if the fix is present
    fix_marker = "scan_stats['files_processed'] += 1  # Fix: increment counter for processed files"
    
    if fix_marker in content:
        print("✅ Fix verification successful!")
        print("The files_processed counter increment is present in the code.")
        return True
    else:
        print("❌ Fix verification failed!")
        print("The files_processed counter increment is not found in the code.")
        return False

def test_fix():
    """Test the fix by running a directory scan."""
    
    print("Testing the fix...")
    
    # Add the app directory to the path
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'app'))
    
    try:
        from app.utils.directory_util import get_folder_structure, get_ignored_patterns, _scan_progress
        
        # Reset scan progress
        _scan_progress.clear()
        _scan_progress.update({"active": False, "progress": {}, "cancelled": False})
        
        current_dir = os.getcwd()
        ignored_patterns = get_ignored_patterns(current_dir)
        
        print(f"Testing directory scan on: {current_dir}")
        
        # Perform scan
        structure = get_folder_structure(current_dir, ignored_patterns, max_depth=2)
        
        if isinstance(structure, dict) and 'error' not in structure:
            # Count files in structure
            def count_files(struct):
                count = 0
                if isinstance(struct, dict):
                    for key, value in struct.items():
                        if key.startswith('_'):
                            continue
                        if isinstance(value, dict):
                            if 'children' in value:
                                count += count_files(value['children'])
                            elif 'token_count' in value and not value.get('children'):
                                count += 1
                return count
            
            file_count = count_files(structure)
            
            if file_count > 0:
                print(f"✅ Test successful! Found {file_count} files in structure.")
                print("The files_processed counter bug appears to be fixed.")
                return True
            else:
                print("❌ Test failed! No files found in structure.")
                print("The bug may still be present.")
                return False
        else:
            print(f"❌ Test failed! Error in structure: {structure.get('error', 'Unknown error')}")
            return False
            
    except Exception as e:
        print(f"❌ Test failed with exception: {e}")
        import traceback
        traceback.print_exc()
        return False

def main():
    """Main function to apply and test the fix."""
    
    print("=" * 60)
    print("ZIYA FILES_PROCESSED COUNTER BUG FIX")
    print("=" * 60)
    
    print("\n1. Verifying current state...")
    if verify_fix():
        print("Fix already appears to be applied.")
        choice = input("Do you want to test it anyway? (y/n): ").lower().strip()
        if choice != 'y':
            return
    else:
        print("Fix not detected. Applying fix...")
        if not apply_fix():
            print("Failed to apply fix. Exiting.")
            return
        
        print("\n2. Verifying fix was applied...")
        if not verify_fix():
            print("Fix verification failed. Exiting.")
            return
    
    print("\n3. Testing the fix...")
    if test_fix():
        print("\n✅ SUCCESS! The files_processed counter bug has been fixed.")
        print("Directory scans should now correctly report the number of files processed.")
    else:
        print("\n❌ FAILURE! The fix did not resolve the issue.")
        print("Further investigation may be needed.")

if __name__ == "__main__":
    main()
