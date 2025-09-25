#!/bin/bash
# Generated test integration script for Ziya
# Review this script before running!

echo "Ziya Test Integration Script"
echo "============================"
echo "This script will organize tests into backend_system_tests categories."
echo "Please review each action before proceeding."
echo ""

# Create additional categories if needed
mkdir -p backend_system_tests/diff
mkdir -p backend_system_tests/model
mkdir -p backend_system_tests/streaming
mkdir -p backend_system_tests/auth

# High value integrations - move immediately
echo "Moving high-value tests..."
mv 'test_middleware_order.py' backend_system_tests/diff/  # Quality: 8/10
mv 'test_post_instructions.py' backend_system_tests/diff/  # Quality: 8/10
mv 'test_apply_state_additional.py' backend_system_tests/diff/  # Quality: 8/10
mv 'test_nova_str_id_bug.py' backend_system_tests/diff/  # Quality: 8/10
mv 'run_diff_tests.py' backend_system_tests/diff/  # Quality: 10/10
mv 'test_ziya_string.py' backend_system_tests/diff/  # Quality: 8/10
mv 'test_directory_reading_regression.py' backend_system_tests/integration/  # Quality: 10/10
mv 'test_nova_wrapper_annotations.py' backend_system_tests/model/  # Quality: 8/10
mv 'test_nova_integration.py' backend_system_tests/diff/  # Quality: 10/10
mv 'test_llm_interaction_regression.py' backend_system_tests/diff/  # Quality: 10/10
mv 'test_llm_interaction_model_specific.py' backend_system_tests/diff/  # Quality: 8/10
mv 'test_MRE_no_diff_git_header.py' backend_system_tests/diff/  # Quality: 8/10
mv 'test_agent_string_handling.py' backend_system_tests/diff/  # Quality: 8/10
mv 'test_MRE_invisible_unicode_improved.py' backend_system_tests/diff/  # Quality: 8/10
mv 'test_enhanced_patch_apply.py' backend_system_tests/diff/  # Quality: 8/10
mv 'test_improved_line_calculation.py' backend_system_tests/diff/  # Quality: 8/10
mv 'test_langserve_error.py' backend_system_tests/diff/  # Quality: 8/10
mv 'test_nova_lite_fix.py' backend_system_tests/diff/  # Quality: 8/10
mv 'test_MRE_line_endings_preservation.py' backend_system_tests/diff/  # Quality: 8/10
mv 'test_nova_wrapper.py' backend_system_tests/diff/  # Quality: 8/10
mv 'test_complex_json.py' backend_system_tests/diff/  # Quality: 8/10
mv 'test_custom_bedrock.py' backend_system_tests/diff/  # Quality: 8/10
mv 'test_whitespace_handler.py' backend_system_tests/diff/  # Quality: 8/10
mv 'test_llm_result_compatibility.py' backend_system_tests/diff/  # Quality: 8/10
mv 'test_nova_wrapper_comprehensive.py' backend_system_tests/diff/  # Quality: 10/10
mv 'test_enhanced_pipeline.py' backend_system_tests/diff/  # Quality: 8/10
mv 'test_runlog_conversion.py' backend_system_tests/diff/  # Quality: 8/10
mv 'test_nova_wrapper_integration.py' backend_system_tests/diff/  # Quality: 10/10
mv 'test_fixed_middleware.py' backend_system_tests/diff/  # Quality: 8/10
mv 'test_langserve_integration.py' backend_system_tests/diff/  # Quality: 10/10
mv 'test_sse_conversion.py' backend_system_tests/diff/  # Quality: 8/10
mv 'test_nova_generation_compatibility.py' backend_system_tests/diff/  # Quality: 10/10
mv 'test_directory_scan_performance.py' backend_system_tests/diff/  # Quality: 8/10
mv 'test_llm_interaction_edge_cases.py' backend_system_tests/diff/  # Quality: 8/10
mv 'test_two_functions_explicit.py' backend_system_tests/diff/  # Quality: 8/10
mv 'integration_test.py' backend_system_tests/diff/  # Quality: 10/10
mv 'test_MRE_whitespace_preservation.py' backend_system_tests/diff/  # Quality: 8/10
mv 'test_model_summary.py' backend_system_tests/diff/  # Quality: 8/10
mv 'test_multi_chunk_changes.py' backend_system_tests/diff/  # Quality: 8/10
mv 'test_api_endpoints.py' backend_system_tests/diff/  # Quality: 10/10
mv 'test_model_math.py' backend_system_tests/diff/  # Quality: 8/10
mv 'test_middleware_integration.py' backend_system_tests/diff/  # Quality: 10/10
mv 'test_cli_options.py' backend_system_tests/diff/  # Quality: 8/10
mv 'test_stream_agent_response.py' backend_system_tests/diff/  # Quality: 8/10
mv 'test_context_extraction.py' backend_system_tests/diff/  # Quality: 8/10
mv 'test_token_counting_methods.py' backend_system_tests/diff/  # Quality: 10/10
mv 'test_nova_wrapper_imports.py' backend_system_tests/model/  # Quality: 8/10
mv 'test_directory_reading_regression.py' backend_system_tests/integration/  # Quality: 10/10
mv 'test_enhanced_fuzzy_match.py' backend_system_tests/diff/  # Quality: 8/10
mv 'test_prompt_extensions.py' backend_system_tests/diff/  # Quality: 10/10
mv 'test_comment_handler.py' backend_system_tests/diff/  # Quality: 8/10
mv 'test_model_connections.py' backend_system_tests/diff/  # Quality: 8/10
mv 'test_error_tracking.py' backend_system_tests/diff/  # Quality: 8/10
mv 'test_integration.py' backend_system_tests/diff/  # Quality: 10/10
mv 'test_apply_state.py' backend_system_tests/diff/  # Quality: 10/10
mv 'test_all_diff_cases.py' backend_system_tests/diff/  # Quality: 10/10
mv 'test_malformed_hunks.py' backend_system_tests/diff/  # Quality: 8/10
mv 'test_nova_message_handling.py' backend_system_tests/diff/  # Quality: 10/10
mv 'test_model_api_calls.py' backend_system_tests/diff/  # Quality: 8/10
mv 'test_duplicate_detection.py' backend_system_tests/diff/  # Quality: 8/10
mv 'test_nova_lite_errors.py' backend_system_tests/diff/  # Quality: 8/10
mv 'test_llm_interaction_regression_async.py' backend_system_tests/diff/  # Quality: 10/10
mv 'test_nova_pipeline.py' backend_system_tests/diff/  # Quality: 8/10
mv 'test_nova_lite_streaming.py' backend_system_tests/diff/  # Quality: 8/10
mv 'test_ziya_string_integration.py' backend_system_tests/diff/  # Quality: 10/10
mv 'test_model_transactions.py' backend_system_tests/diff/  # Quality: 8/10
mv 'test_streaming_nova.py' backend_system_tests/diff/  # Quality: 8/10
mv 'test_nova_pydantic_validation.py' backend_system_tests/diff/  # Quality: 8/10
mv 'test_pipeline_apply.py' backend_system_tests/diff/  # Quality: 10/10
mv 'test_llm_result_validation.py' backend_system_tests/diff/  # Quality: 8/10
mv 'test_nova_wrapper_runtime.py' backend_system_tests/diff/  # Quality: 8/10
mv 'test_streaming_middleware.py' backend_system_tests/diff/  # Quality: 8/10
mv 'test_regression_other_models.py' backend_system_tests/diff/  # Quality: 10/10
mv 'test_integration_nova_generation.py' backend_system_tests/diff/  # Quality: 10/10
mv 'test_ziya_bedrock.py' backend_system_tests/diff/  # Quality: 8/10
mv 'diff_regression_tests.py' backend_system_tests/diff/  # Quality: 10/10
mv 'test_direct_whitespace_handler.py' backend_system_tests/diff/  # Quality: 8/10
mv 'test_all_models.py' backend_system_tests/diff/  # Quality: 8/10

# Diff tests - move to diff category
echo "Moving diff tests..."
mv 'test_streaming_mock.py' backend_system_tests/diff/  # Quality: 3/10
mv 'test_streaming_mock_updated.py' backend_system_tests/diff/  # Quality: 3/10


echo "Integration complete!"
echo "Next steps:"
echo "1. Review moved tests for compatibility"
echo "2. Update import paths if needed"
echo "3. Run test suite: python run_backend_system_tests.py"
echo "4. Update documentation"
