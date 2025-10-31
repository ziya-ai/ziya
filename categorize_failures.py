#!/usr/bin/env python3
import subprocess
import json
import os

failing_tests = [
    "test_alarm_actions_refactor",
    "test_ambiguous_context_lines",
    "test_d3_network_typescript",
    "test_file_utils_changes",
    "test_included_inline_unicode",
    "test_indentation_regression",
    "test_indented_context",
    "test_json_escape_sequence",
    "test_long_multipart_emptylines",
    "test_markdown_renderer_language_cache",
    "test_MRE_comment_only_changes",
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
    "test_vega_lite_fold_transform_fix",
]

categories = {
    "infrastructure": [],  # Missing files, no metadata
    "expected_fail": [],   # Marked as expected to fail
    "wrong_expected": [],  # Applies correctly but expected file wrong
    "ambiguity": [],       # Ambiguity detection rejecting
    "corruption": [],      # Applying but corrupting content
    "not_applying": []     # Not applying at all
}

for test_name in failing_tests:
    case_name = test_name.replace("test_", "")
    case_dir = f"tests/diff_test_cases/{case_name}"
    
    # Check infrastructure
    if not os.path.exists(case_dir):
        categories["infrastructure"].append(f"{test_name}: directory missing")
        continue
    
    metadata_path = f"{case_dir}/metadata.json"
    if not os.path.exists(metadata_path):
        categories["infrastructure"].append(f"{test_name}: no metadata.json")
        continue
    
    # Check expected_to_fail
    with open(metadata_path) as f:
        metadata = json.load(f)
    
    if metadata.get("expected_to_fail"):
        categories["expected_fail"].append(test_name)
        continue
    
    # Run test and capture output
    result = subprocess.run(
        ["python", "tests/run_diff_tests.py", "-k", test_name],
        capture_output=True,
        text=True,
        timeout=10
    )
    
    output = result.stdout + result.stderr
    
    # Categorize by output patterns
    if "ambiguous" in output.lower() or "equally close" in output.lower():
        categories["ambiguity"].append(test_name)
    elif "no actual changes" in output.lower() or "marked hunk" in output.lower() and "failed" in output.lower():
        categories["not_applying"].append(test_name)
    elif "difference between expected and got" in output.lower():
        # Check if it's corruption or wrong expected
        if "'''" in output or '"""' in output or "wrong" in output.lower():
            categories["corruption"].append(test_name)
        else:
            categories["wrong_expected"].append(test_name)
    else:
        categories["not_applying"].append(test_name)

print("=" * 80)
print("FAILURE CATEGORIES")
print("=" * 80)

for category, tests in categories.items():
    if tests:
        print(f"\n{category.upper()}: {len(tests)}")
        for test in tests:
            print(f"  - {test}")

print("\n" + "=" * 80)
print(f"TOTAL: {sum(len(t) for t in categories.values())} tests")
print("=" * 80)
