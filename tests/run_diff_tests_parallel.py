#!/usr/bin/env python3
"""
Parallel test runner for diff tests.
Runs tests in parallel using multiprocessing for significant speedup.
"""
import sys
import os
import time
import multiprocessing as mp
from pathlib import Path

# Add project root to path
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, project_root)

import unittest
import tempfile
import shutil
from run_diff_tests import DiffRegressionTest

def run_single_test(test_case_name):
    """Run a single test case and return its result."""
    # Create a fresh test instance
    temp_dir = tempfile.mkdtemp()
    os.environ['ZIYA_USER_CODEBASE_DIR'] = temp_dir
    
    # Suppress logging
    import logging
    logging.disable(logging.CRITICAL)
    
    try:
        # Create test instance
        test = DiffRegressionTest(f'test_{test_case_name}')
        
        # Run the test
        suite = unittest.TestSuite()
        suite.addTest(test)
        runner = unittest.TextTestRunner(stream=open(os.devnull, 'w'), verbosity=0)
        
        start = time.time()
        result = runner.run(suite)
        elapsed = time.time() - start
        
        return {
            'name': test_case_name,
            'passed': result.wasSuccessful(),
            'time': elapsed,
            'errors': len(result.errors),
            'failures': len(result.failures),
            'error_msg': str(result.failures[0][1]) if result.failures else (str(result.errors[0][1]) if result.errors else None)
        }
    except Exception as e:
        return {
            'name': test_case_name,
            'passed': False,
            'time': 0,
            'errors': 1,
            'failures': 0,
            'error_msg': str(e)
        }
    finally:
        # Cleanup
        try:
            shutil.rmtree(temp_dir)
        except:
            pass

def main():
    # Parse arguments
    quiet = '--quiet' in sys.argv
    force = '--force' in sys.argv
    
    # Get all test case names
    test_cases_dir = Path(__file__).parent / 'diff_test_cases'
    test_names = [d.name for d in test_cases_dir.iterdir() if d.is_dir()]
    
    # Determine number of workers
    num_workers = min(mp.cpu_count(), len(test_names))
    
    if not quiet:
        print(f"Running {len(test_names)} tests with {num_workers} workers...")
        print()
    
    # Run tests in parallel
    start_time = time.time()
    with mp.Pool(num_workers) as pool:
        results = pool.map(run_single_test, test_names)
    total_time = time.time() - start_time
    
    # Sort results by time (slowest first)
    results.sort(key=lambda x: x['time'], reverse=True)
    
    # Count results
    passed = sum(1 for r in results if r['passed'])
    failed = len(results) - passed
    
    # Print summary
    if not quiet:
        print("\n" + "="*80)
        print(f"Summary: \033[92m{passed} passed\033[0m, \033[91m{failed} failed\033[0m, {len(results)} total")
        print("="*80)
        print("\n")
    
    # Print test results table
    print("="*75)
    print("Test Mode Summary")
    print("="*75)
    print(f"{'Test Name':<45} {'Status':<10} {'Time':<10}")
    print("-"*75)
    
    for result in results:
        status = "\033[92mPASS\033[0m" if result['passed'] else "\033[91mFAIL\033[0m"
        test_name = f"test_{result['name']}"
        
        # Color code timing
        time_str = f"{result['time']:.2f}s"
        if result['time'] > 5:
            time_str = f"\033[91m{time_str}\033[0m"
        elif result['time'] > 2:
            time_str = f"\033[38;5;214m{time_str}\033[0m"
        elif result['time'] > 1:
            time_str = f"\033[93m{time_str}\033[0m"
        
        print(f"{test_name:<45} {status:<18} {time_str}")
    
    print("-"*75)
    print(f"{'TOTAL':<45} {passed}/{len(results)} passed ({failed} failed)   {total_time:.2f}s")
    print("-"*75)
    print("="*75)
    print()
    print(f"Timing: Total {total_time:.2f}s, Average {total_time/len(results):.2f}s per test")
    
    # Show slowest tests
    slow_tests = [r for r in results if r['time'] > 5]
    if slow_tests:
        print(f"\033[91m{len(slow_tests)} tests took longer than 5 seconds\033[0m")
    
    # Exit with appropriate code
    sys.exit(0 if failed == 0 else 1)

if __name__ == '__main__':
    main()
