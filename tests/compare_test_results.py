#!/usr/bin/env python3

import subprocess
import sys
import re

def run_tests_and_get_results():
    """Run tests and return a dict of test_name -> status"""
    try:
        result = subprocess.run(
            ['python', 'tests/run_diff_tests.py', '--quiet'],
            capture_output=True,
            text=True,
            cwd='/Users/dcohn/workplace/ziya-release-debug'
        )
        
        output = result.stdout
        test_results = {}
        
        # Parse the test results table
        lines = output.split('\n')
        in_table = False
        
        for line in lines:
            if '| Test Name' in line and '| Status |' in line:
                in_table = True
                continue
            elif in_table and line.startswith('+'):
                continue
            elif in_table and '| TOTAL' in line:
                break
            elif in_table and line.startswith('|'):
                # Parse test line: | test_name | PASS/FAIL |
                parts = [p.strip() for p in line.split('|') if p.strip()]
                if len(parts) >= 2:
                    test_name = parts[0].replace('\x1b[92m', '').replace('\x1b[91m', '').replace('\x1b[0m', '')
                    status = parts[1].replace('\x1b[92m', '').replace('\x1b[91m', '').replace('\x1b[0m', '')
                    test_results[test_name] = status
        
        return test_results
        
    except Exception as e:
        print(f"Error running tests: {e}")
        return {}

def main():
    print("Getting current test results...")
    current_results = run_tests_and_get_results()
    
    print("Stashing changes and getting baseline...")
    subprocess.run(['git', 'stash', 'push', '-m', 'temp_comparison', 'app/utils/diff_utils/application/patch_apply.py'], 
                  cwd='/Users/dcohn/workplace/ziya-release-debug')
    
    baseline_results = run_tests_and_get_results()
    
    print("Restoring changes...")
    subprocess.run(['git', 'stash', 'pop'], cwd='/Users/dcohn/workplace/ziya-release-debug')
    
    # Compare results
    print("\n" + "="*80)
    print("REGRESSION ANALYSIS")
    print("="*80)
    
    regressions = []
    improvements = []
    
    all_tests = set(baseline_results.keys()) | set(current_results.keys())
    
    for test in sorted(all_tests):
        baseline_status = baseline_results.get(test, 'MISSING')
        current_status = current_results.get(test, 'MISSING')
        
        if baseline_status == 'PASS' and current_status == 'FAIL':
            regressions.append(test)
        elif baseline_status == 'FAIL' and current_status == 'PASS':
            improvements.append(test)
    
    print(f"\nREGRESSIONS ({len(regressions)} tests):")
    print("-" * 40)
    for test in regressions:
        print(f"  ❌ {test}")
    
    print(f"\nIMPROVEMENTS ({len(improvements)} tests):")
    print("-" * 40)
    for test in improvements:
        print(f"  ✅ {test}")
    
    print(f"\nNET EFFECT: {len(improvements) - len(regressions)} tests")
    
    baseline_passed = sum(1 for status in baseline_results.values() if status == 'PASS')
    current_passed = sum(1 for status in current_results.values() if status == 'PASS')
    
    print(f"BASELINE: {baseline_passed} passed")
    print(f"CURRENT:  {current_passed} passed")
    print(f"CHANGE:   {current_passed - baseline_passed:+d}")

if __name__ == '__main__':
    main()
