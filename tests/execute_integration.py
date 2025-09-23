#!/usr/bin/env python3
"""
Execute the strategic integration automatically.
"""

import os
import shutil
from pathlib import Path

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
        'test_prompt_extensions.py',
        'test_post_instructions.py',
        'test_agent_string_handling.py',
        'test_ziya_string.py',
        'test_context_extraction.py',
        'test_duplicate_detection.py',
        'test_cli_options.py',
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

def execute_integration():
    """Execute the integration automatically."""
    print("🚀 EXECUTING STRATEGIC TEST INTEGRATION")
    print("=" * 50)
    print("NOTE: Diff tests excluded - using existing tests/run_diff_tests.py system")
    print()
    
    # Create directory structure
    base_dir = Path('backend_system_tests')
    categories = list(INTEGRATION_PLAN.keys())
    
    for category in categories:
        category_dir = base_dir / category
        category_dir.mkdir(parents=True, exist_ok=True)
        print(f"✅ Created category: {category}")
    
    # Move tests
    moved_count = 0
    total_count = sum(len(tests) for tests in INTEGRATION_PLAN.values())
    
    for category, test_files in INTEGRATION_PLAN.items():
        print(f"\n📁 Processing {category.upper()} category ({len(test_files)} tests)")
        
        for test_file in test_files:
            source_path = Path(test_file)
            target_path = Path('backend_system_tests') / category / test_file
            
            if source_path.exists():
                try:
                    if target_path.exists():
                        print(f"  ⚠️  {test_file} already exists in {category}, skipping")
                        continue
                    
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

if __name__ == "__main__":
    execute_integration()
