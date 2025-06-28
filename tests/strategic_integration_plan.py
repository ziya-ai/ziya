#!/usr/bin/env python3
"""
Strategic Integration Plan for Ziya Tests

This script creates a strategic plan for integrating the high-value tests
found in the tests directory into the backend_system_tests structure.

NOTE: Diff-related tests are excluded as they're already well-organized
under tests/run_diff_tests.py and tests/diff_test_cases/ hierarchy.
"""

import os
import shutil
from pathlib import Path
from typing import Dict, List, Tuple

# High-value tests categorized by functionality (EXCLUDING diff tests)
INTEGRATION_PLAN = {
    'model': [
        'test_nova_integration.py',
        'test_nova_wrapper_comprehensive.py',
        'test_nova_wrapper_integration.py',
        'test_nova_generation_compatibility.py',
        'test_nova_message_handling.py',
        'test_nova_str_id_bug.py',
        'test_nova_wrapper_annotations.py',
        'test_nova_wrapper.py',
        'test_custom_bedrock.py',
        'test_nova_lite_fix.py',
        'test_nova_wrapper_imports.py',
        'test_nova_lite_errors.py',
        'test_nova_pipeline.py',
        'test_nova_lite_streaming.py',
        'test_nova_pydantic_validation.py',
        'test_nova_wrapper_runtime.py',
        'test_ziya_bedrock.py',
        'test_nova_wrapper_specific.py',
        'test_nova_wrapper_syntax.py',
        'test_all_models.py',
        'test_model_summary.py',
        'test_model_math.py',
        'test_model_connections.py',
        'test_model_api_calls.py',
        'test_model_transactions.py',
        'test_regression_other_models.py',
    ],
    
    'integration': [
        'test_integration_nova_generation.py',
        'test_langserve_integration.py',
        'test_middleware_integration.py',
        'test_integration.py',
        'test_ziya_string_integration.py',
        'integration_test.py',
        # test_api_endpoints.py already moved
    ],
    
    'streaming': [
        'test_llm_interaction_regression.py',
        'test_llm_interaction_regression_async.py',
        'test_llm_interaction_model_specific.py',
        'test_llm_interaction_edge_cases.py',
        'test_streaming_nova.py',
        'test_streaming_middleware.py',
        'test_streaming.py',
        'test_stream_agent_response.py',
        'test_stream_endpoint.py',
        'test_stream_endpoint_simple.py',
        'test_sse_conversion.py',
        'test_runlog_conversion.py',
    ],
    
    'core': [
        # test_directory_reading_regression.py already moved
        # test_token_counting_methods.py already moved
        'test_prompt_extensions.py',
        'test_post_instructions.py',
        'test_agent_string_handling.py',
        'test_ziya_string.py',
        'test_context_extraction.py',
        'test_duplicate_detection.py',
        'test_cli_options.py',
    ],
    
    'performance': [
        # test_directory_scan_performance.py already moved
    ],
    
    'middleware': [
        'test_middleware_order.py',
        'test_fixed_middleware.py',
        'test_complex_json.py',
        'test_langserve_error.py',
        'test_aimessagechunk_error.py',
    ],
    
    'auth': [
        'test_aws_auth.py',
    ],
    
    'validation': [
        'test_llm_result_compatibility.py',
        'test_llm_result_validation.py',
    ]
}

def create_integration_structure():
    """Create the backend_system_tests directory structure."""
    base_dir = Path('backend_system_tests')
    
    categories = list(INTEGRATION_PLAN.keys())
    
    for category in categories:
        category_dir = base_dir / category
        category_dir.mkdir(parents=True, exist_ok=True)
        print(f"✅ Created category: {category}")
    
    return categories

def move_tests_strategically():
    """Move tests according to the strategic plan."""
    print("🚀 STRATEGIC TEST INTEGRATION")
    print("=" * 50)
    print("NOTE: Diff tests excluded - using existing tests/run_diff_tests.py system")
    print()
    
    # Create directory structure
    categories = create_integration_structure()
    
    moved_count = 0
    total_count = sum(len(tests) for tests in INTEGRATION_PLAN.values())
    
    for category, test_files in INTEGRATION_PLAN.items():
        print(f"\n📁 Processing {category.upper()} category ({len(test_files)} tests)")
        
        for test_file in test_files:
            source_path = Path(test_file)
            target_path = Path('backend_system_tests') / category / test_file
            
            if source_path.exists():
                try:
                    # Check if target already exists
                    if target_path.exists():
                        print(f"  ⚠️  {test_file} already exists in {category}, skipping")
                        continue
                    
                    # Move the file
                    shutil.move(str(source_path), str(target_path))
                    moved_count += 1
                    print(f"  ✅ Moved {test_file} to {category}/")
                    
                except Exception as e:
                    print(f"  ❌ Failed to move {test_file}: {e}")
            else:
                print(f"  ⚠️  {test_file} not found, skipping")
    
    print(f"\n📊 INTEGRATION SUMMARY")
    print(f"Total tests planned: {total_count}")
    print(f"Tests successfully moved: {moved_count}")
    print(f"Success rate: {moved_count/total_count*100:.1f}%")
    
    return moved_count

def update_test_imports():
    """Update import paths in moved tests to work from new locations."""
    print(f"\n🔧 UPDATING IMPORT PATHS")
    print("=" * 30)
    
    backend_tests_dir = Path('backend_system_tests')
    
    # Common import fixes
    import_fixes = [
        # Fix relative imports to app
        ("sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'app'))",
         "sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'app'))"),
        
        ("sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'app'))",
         "sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'app'))"),
        
        # Fix path to parent directory
        ("os.path.dirname(__file__)", "os.path.join(os.path.dirname(__file__), '..', '..')"),
    ]
    
    fixed_files = 0
    
    for category_dir in backend_tests_dir.iterdir():
        if category_dir.is_dir():
            for test_file in category_dir.glob('test_*.py'):
                try:
                    with open(test_file, 'r') as f:
                        content = f.read()
                    
                    original_content = content
                    
                    # Apply import fixes
                    for old_import, new_import in import_fixes:
                        content = content.replace(old_import, new_import)
                    
                    # Write back if changed
                    if content != original_content:
                        with open(test_file, 'w') as f:
                            f.write(content)
                        fixed_files += 1
                        print(f"  ✅ Fixed imports in {test_file}")
                
                except Exception as e:
                    print(f"  ❌ Failed to fix imports in {test_file}: {e}")
    
    print(f"Fixed imports in {fixed_files} files")

def create_category_readmes():
    """Create README files for each category explaining the tests."""
    print(f"\n📝 CREATING CATEGORY DOCUMENTATION")
    print("=" * 35)
    
    category_descriptions = {
        'model': "Tests for LLM model integration, Nova wrapper, and model-specific functionality.",
        'integration': "Integration tests that verify multiple components working together.",
        'streaming': "Tests for streaming responses, async operations, and real-time functionality.",
        'core': "Core functionality tests including directory reading, token counting, and basic operations.",
        'performance': "Performance tests, benchmarks, and timeout behavior validation.",
        'middleware': "Tests for middleware components, request/response processing, and pipeline operations.",
        'auth': "Authentication and authorization tests, AWS integration, and security functionality.",
        'validation': "Input validation, data validation, and result verification tests."
    }
    
    backend_tests_dir = Path('backend_system_tests')
    
    for category, description in category_descriptions.items():
        category_dir = backend_tests_dir / category
        if category_dir.exists():
            readme_path = category_dir / 'README.md'
            
            # Count tests in this category
            test_files = list(category_dir.glob('test_*.py'))
            test_count = len(test_files)
            
            readme_content = f"""# {category.title()} Tests

{description}

## Test Files ({test_count} tests)

"""
            
            for test_file in sorted(test_files):
                readme_content += f"- `{test_file.name}`\n"
            
            readme_content += f"""
## Running Tests

```bash
# Run all {category} tests
python run_backend_system_tests.py --category {category}

# Run specific test
python -m unittest {category}.test_specific_file

# Run with verbose output
python run_backend_system_tests.py --category {category} --verbose
```

## Integration Notes

These tests have been integrated from the main tests directory as part of the strategic
test organization effort. They may require some import path adjustments or dependency
installation to run properly.

## Diff Tests Note

Diff-related functionality is tested separately using the existing `tests/run_diff_tests.py`
system and `tests/diff_test_cases/` hierarchy, which provides comprehensive diff testing.
"""
            
            with open(readme_path, 'w') as f:
                f.write(readme_content)
            
            print(f"  ✅ Created README for {category} ({test_count} tests)")

def main():
    """Execute the strategic integration plan."""
    print("🎯 ZIYA STRATEGIC TEST INTEGRATION")
    print("=" * 50)
    print("Organizing high-value tests into backend_system_tests/")
    print("(Excluding diff tests - using existing run_diff_tests.py system)")
    print()
    
    # Confirm before proceeding
    response = input("Proceed with integration? (y/n): ").lower().strip()
    if response != 'y':
        print("Integration cancelled.")
        return
    
    # Execute integration steps
    moved_count = move_tests_strategically()
    
    if moved_count > 0:
        update_test_imports()
        create_category_readmes()
        
        print(f"\n🎉 INTEGRATION COMPLETE!")
        print(f"Successfully integrated {moved_count} high-value tests")
        print(f"")
        print(f"Test organization:")
        print(f"- Diff tests: tests/run_diff_tests.py (existing system)")
        print(f"- Other tests: backend_system_tests/ (newly organized)")
        print(f"")
        print(f"Next steps:")
        print(f"1. Run test suite: python run_backend_system_tests.py")
        print(f"2. Fix any import issues in failing tests")
        print(f"3. Install missing dependencies as needed")
        print(f"4. Update main project documentation")
    else:
        print("No tests were moved. Check file paths and permissions.")

if __name__ == "__main__":
    main()
