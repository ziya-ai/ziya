import sys
import os
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ['ZIYA_USER_CODEBASE_DIR'] = tempfile.gettempdir()

# Suppress logging
import logging
logging.getLogger().setLevel(logging.CRITICAL)

from app.utils.code_util import use_git_to_apply_code_diff

failing_tests = [
    'MRE_comment_only_changes',
    'MRE_context_empty_line',
    'MRE_duplicate_state_declaration',
    'MRE_incorrect_hunk_offsets',
    'alarm_actions_refactor',
    'ambiguous_context_lines',
    'folder_context_fix',
    'included_inline_unicode',
    'json_escape_sequence',
    'long_multipart_emptylines',
    'mcp_registry_test_connection',
    'multi_hunk_same_function',
]

results = []

for test_name in failing_tests:
    test_dir = Path(f'tests/diff_test_cases/{test_name}')
    
    # Find files
    original_file = None
    for ext in ['.py', '.ts', '.tsx', '.js']:
        orig = test_dir / f'original{ext}'
        if orig.exists():
            original_file = orig
            expected_file = test_dir / f'expected{ext}'
            break
    
    if not original_file:
        results.append((test_name, 'MISSING_FILES', 0, 0, ''))
        continue
    
    try:
        original = original_file.read_text()
        diff = (test_dir / 'changes.diff').read_text()
        expected = expected_file.read_text()
        
        with tempfile.TemporaryDirectory() as tmpdir:
            test_file = Path(tmpdir) / original_file.name.replace('original', 'test')
            test_file.write_text(original)
            
            result = use_git_to_apply_code_diff(diff, str(test_file))
            actual = test_file.read_text()
            
            status = result.get('status', 'unknown')
            exp_lines = len(expected.splitlines())
            act_lines = len(actual.splitlines())
            
            if actual == expected:
                results.append((test_name, 'CONTENT_MATCH', exp_lines, act_lines, status))
            elif exp_lines == act_lines:
                # Same line count, check if it's just whitespace
                exp_stripped = [l.strip() for l in expected.splitlines()]
                act_stripped = [l.strip() for l in actual.splitlines()]
                if exp_stripped == act_stripped:
                    results.append((test_name, 'WHITESPACE_ONLY', exp_lines, act_lines, status))
                else:
                    results.append((test_name, 'CONTENT_DIFF', exp_lines, act_lines, status))
            else:
                diff_lines = act_lines - exp_lines
                results.append((test_name, f'LINE_DIFF({diff_lines:+d})', exp_lines, act_lines, status))
    except Exception as e:
        results.append((test_name, f'ERROR: {str(e)[:30]}', 0, 0, ''))

# Print results
print(f"{'Test':<40} {'Result':<20} {'Exp':<6} {'Act':<6} {'Status'}")
print("=" * 90)
for test, result, exp, act, status in results:
    print(f"{test:<40} {result:<20} {exp:<6} {act:<6} {status}")

# Summary
content_match = sum(1 for _, r, _, _, _ in results if r == 'CONTENT_MATCH')
whitespace = sum(1 for _, r, _, _, _ in results if r == 'WHITESPACE_ONLY')
fixable = content_match + whitespace

print(f"\n{'='*90}")
print(f"Content matches: {content_match}")
print(f"Whitespace only: {whitespace}")
print(f"Potentially fixable: {fixable}/{len(results)}")
