#!/usr/bin/env python3
import os
import json
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

failing_tests = [
    "test_alarm_actions_refactor",
    "test_ambiguous_context_lines",
    "test_d3_network_typescript",
    "test_duplicate_state_declaration",
    "test_file_utils_changes",
    "test_included_inline_unicode",
    "test_indentation_regression",
    "test_indented_context",
    "test_json_escape_sequence",
    "test_long_multipart_emptylines",
    "test_markdown_renderer_language_cache",
    "test_MRE_comment_only_changes",
    "test_MRE_css_padding_already_applied",
    "test_MRE_duplicate_state_declaration",
    "test_MRE_identical_adjacent_blocks",
    "test_MRE_inconsistent_indentation",
    "test_MRE_incorrect_hunk_offsets",
    "test_multi_chunk_changes",
    "test_multi_hunk_same_function",
    "test_multihunk2",
    "test_not_already_applied",
    "test_not_matching_context_multipart",
    "test_repro_original_hunk_issue",
    "test_simple_three_hunk_insert",
    "test_truly_ambiguous_equal_distance",
    "test_vega_lite_fold_transform_fix"
]

test_cases_dir = "tests/diff_test_cases"

categories = {
    "missing_files": [],
    "malformed_metadata": [],
    "expected_to_fail": [],
    "legitimate_failures": []
}

for test_name in failing_tests:
    case_name = test_name.replace("test_", "")
    case_dir = os.path.join(test_cases_dir, case_name)
    
    if not os.path.exists(case_dir):
        categories["missing_files"].append(case_name)
        continue
    
    metadata_path = os.path.join(case_dir, "metadata.json")
    if not os.path.exists(metadata_path):
        categories["malformed_metadata"].append(case_name)
        continue
    
    try:
        with open(metadata_path, 'r') as f:
            metadata = json.load(f)
        
        if metadata.get("expected_to_fail", False):
            categories["expected_to_fail"].append({
                "name": case_name,
                "description": metadata.get("description", "No description")
            })
        else:
            categories["legitimate_failures"].append({
                "name": case_name,
                "description": metadata.get("description", "No description")
            })
    except Exception as e:
        categories["malformed_metadata"].append(f"{case_name}: {str(e)}")

print("=" * 80)
print("FAILING TESTS ANALYSIS")
print("=" * 80)

print(f"\n📁 Missing Files: {len(categories['missing_files'])}")
for name in categories["missing_files"]:
    print(f"  - {name}")

print(f"\n⚠️  Malformed Metadata: {len(categories['malformed_metadata'])}")
for name in categories["malformed_metadata"]:
    print(f"  - {name}")

print(f"\n✓ Expected to Fail: {len(categories['expected_to_fail'])}")
for item in categories["expected_to_fail"]:
    print(f"  - {item['name']}")
    print(f"    {item['description']}")

print(f"\n❌ Legitimate Failures: {len(categories['legitimate_failures'])}")
for item in categories["legitimate_failures"]:
    print(f"  - {item['name']}")
    print(f"    {item['description']}")

print("\n" + "=" * 80)
print(f"TOTAL: {len(failing_tests)} failing tests")
print("=" * 80)
