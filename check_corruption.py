#!/usr/bin/env python3
"""Check for tests with significant line count differences (potential corruption)."""

import subprocess
import re

# Run tests with verbose output
result = subprocess.run(
    ['python', 'tests/run_diff_tests.py', '-v'],
    capture_output=True,
    text=True,
    cwd='/Users/dcohn/workspace/ziya-release-verify'
)

output = result.stdout + result.stderr

# Parse test failures with line counts
test_pattern = r'TEST FAILED: (\S+)'
expected_pattern = r'Expected Length: (\d+) lines'
got_pattern = r'Got Length:\s+(\d+) lines'

tests = []
lines = output.split('\n')
i = 0
while i < len(lines):
    if 'TEST FAILED:' in lines[i]:
        test_match = re.search(test_pattern, lines[i])
        if test_match:
            test_name = test_match.group(1)
            # Look ahead for Expected and Got lengths
            for j in range(i, min(i+20, len(lines))):
                if 'Expected Length:' in lines[j] and 'Got Length:' in lines[j+1]:
                    exp_match = re.search(expected_pattern, lines[j])
                    got_match = re.search(got_pattern, lines[j+1])
                    if exp_match and got_match:
                        expected = int(exp_match.group(1))
                        got = int(got_match.group(1))
                        diff = got - expected
                        tests.append((test_name, expected, got, diff))
                    break
    i += 1

# Sort by absolute difference (potential corruption)
tests.sort(key=lambda x: abs(x[3]), reverse=True)

print("Tests with line count differences (sorted by magnitude):")
print(f"{'Test Name':<45} {'Expected':>8} {'Got':>8} {'Diff':>6}")
print("=" * 75)
for test_name, expected, got, diff in tests:
    sign = '+' if diff > 0 else ''
    print(f"{test_name:<45} {expected:>8} {got:>8} {sign}{diff:>5}")
