"""
Cleanup utility for the diff utils module.

This module provides functions to clean up the diff utils module by removing unused files
and consolidating implementations.
"""

import os
import shutil
import logging
from typing import List, Dict, Set

logger = logging.getLogger("ZIYA")

# Files that are known to be unused and can be safely removed
UNUSED_FILES = [
    "difflib_fix_implementation_final.py",
    "difflib_fix_implementation_clean.py",
    "difflib_fix_implementation.py",
    "improved_difflib_fixes.py",
    "improved_difflib_core.py",
    "test_difflib_fixes.py"
]

# Files that should be kept as they are actively used
CORE_FILES = [
    "app/utils/diff_utils/application/difflib_apply.py",
    "app/utils/diff_utils/pipeline/pipeline_manager.py",
    "app/utils/diff_utils/application/hunk_applier.py",
    "app/utils/diff_utils/application/sequential_hunk_applier.py",
    "app/utils/diff_utils/application/hunk_ordering.py",
    "app/utils/diff_utils/application/line_matching.py",
    "app/utils/diff_utils/debug/diff_analyzer.py"
]

def find_unused_files(root_dir: str) -> List[str]:
    """
    Find unused files in the diff utils module.
    
    Args:
        root_dir: The root directory to search in
        
    Returns:
        A list of unused file paths
    """
    unused_files = []
    
    # Walk through the directory structure
    for dirpath, _, filenames in os.walk(root_dir):
        for filename in filenames:
            if filename in UNUSED_FILES:
                unused_files.append(os.path.join(dirpath, filename))
    
    return unused_files

def backup_files(files: List[str], backup_dir: str) -> Dict[str, str]:
    """
    Backup files before removing them.
    
    Args:
        files: List of file paths to backup
        backup_dir: Directory to store backups in
        
    Returns:
        A dictionary mapping original paths to backup paths
    """
    os.makedirs(backup_dir, exist_ok=True)
    
    backup_map = {}
    for file_path in files:
        backup_path = os.path.join(backup_dir, os.path.basename(file_path))
        shutil.copy2(file_path, backup_path)
        backup_map[file_path] = backup_path
    
    return backup_map

def remove_files(files: List[str]) -> None:
    """
    Remove files from the filesystem.
    
    Args:
        files: List of file paths to remove
    """
    for file_path in files:
        try:
            os.remove(file_path)
            logger.info(f"Removed unused file: {file_path}")
        except Exception as e:
            logger.error(f"Failed to remove file {file_path}: {str(e)}")

def find_duplicate_implementations(root_dir: str) -> Dict[str, List[str]]:
    """
    Find duplicate implementations of the same functionality.
    
    Args:
        root_dir: The root directory to search in
        
    Returns:
        A dictionary mapping functionality names to lists of file paths
    """
    # This is a simplified implementation that would need to be expanded
    # with actual code analysis to find true duplicates
    
    # For now, we'll just look for files with similar names
    implementations = {}
    
    for dirpath, _, filenames in os.walk(root_dir):
        for filename in filenames:
            if not filename.endswith('.py'):
                continue
                
            # Extract the base functionality name
            base_name = filename.split('_')[0] if '_' in filename else filename.split('.')[0]
            
            if base_name not in implementations:
                implementations[base_name] = []
            
            implementations[base_name].append(os.path.join(dirpath, filename))
    
    # Filter to only include entries with multiple implementations
    return {k: v for k, v in implementations.items() if len(v) > 1}

def cleanup_diff_utils(root_dir: str, backup_dir: str = None, dry_run: bool = True) -> Dict:
    """
    Clean up the diff utils module by removing unused files and consolidating implementations.
    
    Args:
        root_dir: The root directory of the project
        backup_dir: Directory to store backups in (optional)
        dry_run: If True, don't actually remove files, just report what would be done
        
    Returns:
        A dictionary with the cleanup results
    """
    results = {
        "unused_files": [],
        "duplicate_implementations": {},
        "backed_up": {},
        "removed": []
    }
    
    # Find unused files
    diff_utils_dir = os.path.join(root_dir, "app", "utils", "diff_utils")
    unused_files = find_unused_files(root_dir)
    results["unused_files"] = unused_files
    
    # Find duplicate implementations
    duplicate_implementations = find_duplicate_implementations(diff_utils_dir)
    results["duplicate_implementations"] = duplicate_implementations
    
    if not dry_run:
        # Backup files if a backup directory is provided
        if backup_dir and unused_files:
            results["backed_up"] = backup_files(unused_files, backup_dir)
        
        # Remove unused files
        remove_files(unused_files)
        results["removed"] = unused_files
    
    return results

def print_cleanup_report(results: Dict) -> None:
    """
    Print a report of the cleanup results.
    
    Args:
        results: The cleanup results dictionary
    """
    print("\n" + "=" * 80)
    print("Diff Utils Cleanup Report")
    print("=" * 80)
    
    # Report on unused files
    print("\nUnused Files:")
    print("-" * 80)
    if results["unused_files"]:
        for file_path in results["unused_files"]:
            print(f"- {file_path}")
    else:
        print("No unused files found.")
    
    # Report on duplicate implementations
    print("\nPotential Duplicate Implementations:")
    print("-" * 80)
    if results["duplicate_implementations"]:
        for base_name, file_paths in results["duplicate_implementations"].items():
            print(f"\n{base_name}:")
            for file_path in file_paths:
                print(f"- {file_path}")
    else:
        print("No duplicate implementations found.")
    
    # Report on backed up files
    if "backed_up" in results and results["backed_up"]:
        print("\nBacked Up Files:")
        print("-" * 80)
        for original, backup in results["backed_up"].items():
            print(f"- {original} -> {backup}")
    
    # Report on removed files
    if "removed" in results and results["removed"]:
        print("\nRemoved Files:")
        print("-" * 80)
        for file_path in results["removed"]:
            print(f"- {file_path}")
    
    print("\n" + "=" * 80)

if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="Clean up the diff utils module")
    parser.add_argument("--root-dir", default=".", help="Root directory of the project")
    parser.add_argument("--backup-dir", default="./backup/diff_utils", help="Directory to store backups in")
    parser.add_argument("--dry-run", action="store_true", help="Don't actually remove files, just report what would be done")
    
    args = parser.parse_args()
    
    results = cleanup_diff_utils(args.root_dir, args.backup_dir, args.dry_run)
    print_cleanup_report(results)
