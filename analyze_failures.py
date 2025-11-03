import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))

from tests.run_diff_tests import run_single_test

tests = [
    'MRE_incorrect_hunk_offsets',
    'alarm_actions_refactor', 
    'ambiguous_context_lines',
    'included_inline_unicode',
    'json_escape_sequence',
    'multi_hunk_same_function',
    'mcp_registry_test_connection',
]

for test_name in tests:
    print(f"\n{'='*80}")
    print(f"TEST: {test_name}")
    print('='*80)
    result = run_single_test(test_name, verbose=True)
    if not result['passed']:
        print(f"\nFAILURE REASON: {result.get('error', 'Unknown')}")
