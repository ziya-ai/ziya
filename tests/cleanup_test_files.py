#!/usr/bin/env python3
"""
Cleanup script to organize test files in the Ziya project.

This script helps identify which test files are validated regression tests
vs development cruft that should be cleaned up.
"""

import os
import glob
from pathlib import Path
from typing import List, Dict, Set

def find_test_files() -> Dict[str, List[str]]:
    """Find all test files in the project."""
    test_files = {
        'validated_tests': [],
        'potential_regression_tests': [],
        'development_cruft': [],
        'unknown': []
    }
    
    # Files in backend_system_tests are validated
    backend_test_dir = Path('backend_system_tests')
    if backend_test_dir.exists():
        for test_file in backend_test_dir.rglob('test_*.py'):
            test_files['validated_tests'].append(str(test_file))
    
    # Find all other test files in the root directory
    root_test_files = glob.glob('test_*.py')
    
    # Categorize based on naming patterns and content
    for test_file in root_test_files:
        if 'regression' in test_file.lower():
            test_files['potential_regression_tests'].append(test_file)
        elif any(keyword in test_file.lower() for keyword in ['debug', 'temp', 'scratch', 'mre']):
            test_files['development_cruft'].append(test_file)
        else:
            test_files['unknown'].append(test_file)
    
    # Add other development files
    dev_patterns = [
        'debug_*.py',
        'fix_*.py',
        'analyze_*.py',
        'compare_*.py',
        'get_actual_*.py',
        'mre.py',
        '*_test.py',  # Different naming convention
    ]
    
    for pattern in dev_patterns:
        for file_path in glob.glob(pattern):
            if file_path not in test_files['development_cruft']:
                test_files['development_cruft'].append(file_path)
    
    return test_files

def analyze_test_file(file_path: str) -> Dict[str, any]:
    """Analyze a test file to determine its purpose and quality."""
    analysis = {
        'has_unittest': False,
        'has_docstring': False,
        'has_main': False,
        'line_count': 0,
        'test_methods': 0,
        'imports_ziya': False,
        'purpose': 'unknown'
    }
    
    try:
        with open(file_path, 'r') as f:
            content = f.read()
            lines = content.split('\n')
            
        analysis['line_count'] = len(lines)
        analysis['has_unittest'] = 'unittest' in content
        analysis['has_docstring'] = '"""' in content[:500]  # Check first 500 chars
        analysis['has_main'] = 'if __name__ == "__main__"' in content
        analysis['test_methods'] = content.count('def test_')
        analysis['imports_ziya'] = 'app.utils' in content or 'from app' in content
        
        # Determine purpose from content
        if 'regression' in content.lower():
            analysis['purpose'] = 'regression_test'
        elif 'debug' in content.lower() or 'diagnostic' in content.lower():
            analysis['purpose'] = 'debug_tool'
        elif analysis['test_methods'] > 5:
            analysis['purpose'] = 'comprehensive_test'
        elif analysis['test_methods'] > 0:
            analysis['purpose'] = 'unit_test'
        else:
            analysis['purpose'] = 'script'
            
    except Exception as e:
        analysis['error'] = str(e)
    
    return analysis

def print_cleanup_recommendations():
    """Print recommendations for cleaning up test files."""
    print("=" * 60)
    print("ZIYA TEST FILE CLEANUP RECOMMENDATIONS")
    print("=" * 60)
    
    test_files = find_test_files()
    
    # Show validated tests
    print(f"\n✅ VALIDATED REGRESSION TESTS ({len(test_files['validated_tests'])})")
    print("These are properly organized and should be kept:")
    for test_file in test_files['validated_tests']:
        print(f"  📁 {test_file}")
    
    # Show potential regression tests
    print(f"\n🔍 POTENTIAL REGRESSION TESTS ({len(test_files['potential_regression_tests'])})")
    print("These might be worth moving to backend_system_tests/:")
    for test_file in test_files['potential_regression_tests']:
        analysis = analyze_test_file(test_file)
        print(f"  📄 {test_file}")
        print(f"     Lines: {analysis['line_count']}, Tests: {analysis['test_methods']}, Purpose: {analysis['purpose']}")
    
    # Show development cruft
    print(f"\n🗑️  DEVELOPMENT CRUFT ({len(test_files['development_cruft'])})")
    print("These are likely temporary/debug files that can be removed:")
    for test_file in test_files['development_cruft']:
        analysis = analyze_test_file(test_file)
        print(f"  🔧 {test_file}")
        print(f"     Lines: {analysis['line_count']}, Purpose: {analysis['purpose']}")
    
    # Show unknown files
    print(f"\n❓ UNKNOWN TEST FILES ({len(test_files['unknown'])})")
    print("These need manual review:")
    for test_file in test_files['unknown']:
        analysis = analyze_test_file(test_file)
        print(f"  ❓ {test_file}")
        print(f"     Lines: {analysis['line_count']}, Tests: {analysis['test_methods']}, Purpose: {analysis['purpose']}")
    
    # Summary and recommendations
    total_files = sum(len(files) for files in test_files.values())
    cruft_count = len(test_files['development_cruft'])
    
    print(f"\n📊 SUMMARY")
    print(f"Total test files found: {total_files}")
    print(f"Validated regression tests: {len(test_files['validated_tests'])}")
    print(f"Potential regression tests: {len(test_files['potential_regression_tests'])}")
    print(f"Development cruft: {cruft_count}")
    print(f"Unknown files: {len(test_files['unknown'])}")
    
    if cruft_count > 0:
        print(f"\n💡 RECOMMENDATIONS:")
        print(f"1. Review and potentially remove {cruft_count} development cruft files")
        print(f"2. Consider moving potential regression tests to backend_system_tests/")
        print(f"3. Review unknown files to determine their purpose")
        print(f"4. This could clean up {cruft_count}/{total_files} files ({cruft_count/total_files*100:.1f}%)")

def generate_cleanup_script():
    """Generate a script to perform the cleanup."""
    test_files = find_test_files()
    
    script_content = """#!/bin/bash
# Generated cleanup script for Ziya test files
# Review this script before running!

echo "Ziya Test File Cleanup Script"
echo "============================="
echo "This script will move/remove test files as recommended."
echo "Please review each action before proceeding."
echo ""

"""
    
    # Add commands to remove development cruft
    if test_files['development_cruft']:
        script_content += "# Remove development cruft files\n"
        script_content += "echo \"Removing development cruft files...\"\n"
        for file_path in test_files['development_cruft']:
            script_content += f"# rm '{file_path}'  # Uncomment to remove\n"
        script_content += "\n"
    
    # Add commands to move potential regression tests
    if test_files['potential_regression_tests']:
        script_content += "# Move potential regression tests\n"
        script_content += "echo \"Moving potential regression tests...\"\n"
        for file_path in test_files['potential_regression_tests']:
            script_content += f"# mv '{file_path}' backend_system_tests/core/  # Review and uncomment\n"
        script_content += "\n"
    
    script_content += """
echo "Cleanup complete!"
echo "Don't forget to run the test suite to verify everything still works:"
echo "python run_backend_system_tests.py"
"""
    
    with open('cleanup_tests.sh', 'w') as f:
        f.write(script_content)
    
    print(f"\n📝 Generated cleanup script: cleanup_tests.sh")
    print("Review the script before running it!")

def main():
    """Main function."""
    print_cleanup_recommendations()
    
    choice = input("\nGenerate cleanup script? (y/n): ").lower().strip()
    if choice == 'y':
        generate_cleanup_script()

if __name__ == "__main__":
    main()
