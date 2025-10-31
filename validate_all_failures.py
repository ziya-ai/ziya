#!/usr/bin/env python3
import subprocess
import json
import os

failing_tests = [
    "test_alarm_actions_refactor",
    "test_ambiguous_context_lines",
    "test_d3_network_typescript",
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
    "test_repro_original_hunk_issue",
    "test_vega_lite_fold_transform_fix",
]

results = {}

for test_name in failing_tests:
    case_name = test_name.replace("test_", "")
    case_dir = f"tests/diff_test_cases/{case_name}"
    
    result = subprocess.run(
        ["python", "tests/run_diff_tests.py", "-k", test_name],
        capture_output=True,
        text=True,
        timeout=15
    )
    
    output = result.stdout + result.stderr
    
    # Extract key info
    info = {
        "hunks_succeeded": "All processed hunks succeeded" in output,
        "hunks_failed": "marked hunk" in output.lower() and "failed" in output.lower(),
        "malformed": "malformed" in output.lower(),
        "ambiguous": "ambiguous" in output.lower() or "equally close" in output.lower(),
        "test_failed": "TEST FAILED:" in output or ("FAIL" in output and test_name in output and "PASS" not in output),
    }
    
    # Determine category
    if not info["test_failed"]:
        category = "PASS"
    elif info["malformed"]:
        category = "MALFORMED_DIFF"
    elif info["ambiguous"]:
        category = "AMBIGUITY_REJECTED"
    elif info["hunks_succeeded"]:
        category = "WRONG_OUTPUT"
    elif info["hunks_failed"]:
        category = "HUNKS_FAILED"
    else:
        category = "UNKNOWN"
    
    results[test_name] = category

# Print summary
categories = {}
for test, cat in results.items():
    categories.setdefault(cat, []).append(test)

print("=" * 80)
print("VALIDATION SUMMARY")
print("=" * 80)

for category in sorted(categories.keys()):
    tests = categories[category]
    print(f"\n{category}: {len(tests)}")
    for test in sorted(tests):
        print(f"  - {test}")

print("\n" + "=" * 80)
print(f"TOTAL: {len(failing_tests)} tests analyzed")
print("=" * 80)
