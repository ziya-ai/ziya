#!/usr/bin/env python3
"""
Ziya Backend System Test Runner

This script runs validated regression tests for Ziya's backend systems.
It collects tests from the backend_system_tests directory and provides
comprehensive reporting and filtering capabilities.

Usage:
    python run_backend_system_tests.py                    # Run all tests
    python run_backend_system_tests.py --category core    # Run specific category
    python run_backend_system_tests.py --list             # List available tests
    python run_backend_system_tests.py --verbose          # Verbose output
    python run_backend_system_tests.py --fast             # Skip slow tests
    python run_backend_system_tests.py --report           # Generate detailed report

Categories:
    - core: Core functionality tests (directory reading, file processing)
    - diff: Diff application and patch handling tests
    - token: Token counting and estimation tests
    - performance: Performance and timeout tests
    - integration: Integration and API tests
"""

import os
import sys
import time
import argparse
import unittest
import importlib.util
from typing import List, Dict, Any, Optional, Tuple
from pathlib import Path
import json
from datetime import datetime

# Add the app directory to the path (go up one level from tests/)
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'app'))

class TestResult:
    """Container for test results with metadata."""
    
    def __init__(self, name: str, category: str, status: str, 
                 duration: float, error: Optional[str] = None):
        self.name = name
        self.category = category
        self.status = status  # 'passed', 'failed', 'skipped', 'error'
        self.duration = duration
        self.error = error
        self.timestamp = datetime.now()

class ZiyaTestRunner:
    """Main test runner for Ziya backend system tests."""
    
    def __init__(self):
        self.test_dir = Path(__file__).parent / "backend_system_tests"
        self.results: List[TestResult] = []
        self.verbose = False
        self.fast_mode = False
        
    def discover_tests(self) -> Dict[str, List[str]]:
        """Discover all test files organized by category."""
        tests_by_category = {}
        
        if not self.test_dir.exists():
            print(f"Warning: Test directory {self.test_dir} does not exist")
            return tests_by_category
        
        # Walk through the test directory structure
        for category_dir in self.test_dir.iterdir():
            if not category_dir.is_dir() or category_dir.name.startswith('.'):
                continue
                
            category = category_dir.name
            test_files = []
            
            # Find all test files in this category
            for test_file in category_dir.glob("test_*.py"):
                test_files.append(str(test_file))
            
            if test_files:
                tests_by_category[category] = test_files
        
        return tests_by_category
    
    def load_test_module(self, test_file_path: str) -> Optional[unittest.TestSuite]:
        """Load a test module and return its test suite."""
        try:
            # Get module name from file path
            module_name = Path(test_file_path).stem
            
            # Load the module
            spec = importlib.util.spec_from_file_location(module_name, test_file_path)
            if spec is None or spec.loader is None:
                return None
                
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            
            # Find test classes in the module
            test_classes = []
            for name in dir(module):
                obj = getattr(module, name)
                if (isinstance(obj, type) and 
                    issubclass(obj, unittest.TestCase) and 
                    obj != unittest.TestCase):
                    test_classes.append(obj)
            
            if not test_classes:
                return None
            
            # Create test suite
            suite = unittest.TestSuite()
            for test_class in test_classes:
                tests = unittest.TestLoader().loadTestsFromTestCase(test_class)
                suite.addTest(tests)
            
            return suite
            
        except Exception as e:
            print(f"Error loading test module {test_file_path}: {e}")
            return None
    
    def run_test_suite(self, suite: unittest.TestSuite, category: str, 
                      test_name: str) -> List[TestResult]:
        """Run a test suite and collect results."""
        results = []
        
        # Custom test result class to capture individual test results
        class CustomTestResult(unittest.TestResult):
            def __init__(self, runner_instance):
                super().__init__()
                self.runner = runner_instance
                self.current_test = None
                self.start_time = None
            
            def startTest(self, test):
                super().startTest(test)
                self.current_test = test
                self.start_time = time.time()
                if self.runner.verbose:
                    print(f"  Running {test._testMethodName}...")
            
            def addSuccess(self, test):
                super().addSuccess(test)
                duration = time.time() - self.start_time
                results.append(TestResult(
                    name=f"{test.__class__.__name__}.{test._testMethodName}",
                    category=category,
                    status='passed',
                    duration=duration
                ))
            
            def addError(self, test, err):
                super().addError(test, err)
                duration = time.time() - self.start_time
                error_msg = self._exc_info_to_string(err, test)
                results.append(TestResult(
                    name=f"{test.__class__.__name__}.{test._testMethodName}",
                    category=category,
                    status='error',
                    duration=duration,
                    error=error_msg
                ))
            
            def addFailure(self, test, err):
                super().addFailure(test, err)
                duration = time.time() - self.start_time
                error_msg = self._exc_info_to_string(err, test)
                results.append(TestResult(
                    name=f"{test.__class__.__name__}.{test._testMethodName}",
                    category=category,
                    status='failed',
                    duration=duration,
                    error=error_msg
                ))
            
            def addSkip(self, test, reason):
                super().addSkip(test, reason)
                duration = time.time() - self.start_time if self.start_time else 0
                results.append(TestResult(
                    name=f"{test.__class__.__name__}.{test._testMethodName}",
                    category=category,
                    status='skipped',
                    duration=duration,
                    error=reason
                ))
        
        # Run the tests
        test_result = CustomTestResult(self)
        suite.run(test_result)
        
        return results
    
    def run_tests(self, categories: Optional[List[str]] = None) -> bool:
        """Run tests for specified categories or all categories."""
        tests_by_category = self.discover_tests()
        
        if not tests_by_category:
            print("No tests found!")
            return False
        
        # Filter by categories if specified
        if categories:
            filtered_tests = {}
            for category in categories:
                if category in tests_by_category:
                    filtered_tests[category] = tests_by_category[category]
                else:
                    print(f"Warning: Category '{category}' not found")
            tests_by_category = filtered_tests
        
        if not tests_by_category:
            print("No tests found for specified categories!")
            return False
        
        print("=" * 60)
        print("ZIYA BACKEND SYSTEM TESTS")
        print("=" * 60)
        
        total_start_time = time.time()
        
        # Run tests by category
        for category, test_files in tests_by_category.items():
            print(f"\n📁 Category: {category.upper()}")
            print("-" * 40)
            
            for test_file in test_files:
                test_name = Path(test_file).stem
                print(f"\n🧪 Running {test_name}...")
                
                # Check if this is a slow test and we're in fast mode
                if self.fast_mode and self._is_slow_test(test_file):
                    print(f"  ⏭️  Skipping slow test in fast mode")
                    continue
                
                suite = self.load_test_module(test_file)
                if suite is None:
                    print(f"  ❌ Failed to load test module")
                    continue
                
                # Run the test suite
                test_results = self.run_test_suite(suite, category, test_name)
                self.results.extend(test_results)
                
                # Print summary for this test file
                passed = sum(1 for r in test_results if r.status == 'passed')
                failed = sum(1 for r in test_results if r.status == 'failed')
                errors = sum(1 for r in test_results if r.status == 'error')
                skipped = sum(1 for r in test_results if r.status == 'skipped')
                
                total_tests = len(test_results)
                if total_tests > 0:
                    print(f"  📊 Results: {passed} passed, {failed} failed, {errors} errors, {skipped} skipped")
                else:
                    print(f"  ⚠️  No tests found in module")
        
        total_duration = time.time() - total_start_time
        
        # Print overall summary
        self._print_summary(total_duration)
        
        # Return success if no failures or errors
        failed_count = sum(1 for r in self.results if r.status in ['failed', 'error'])
        return failed_count == 0
    
    def _is_slow_test(self, test_file: str) -> bool:
        """Check if a test file is marked as slow."""
        try:
            with open(test_file, 'r') as f:
                content = f.read()
                # Look for slow test markers
                return ('SLOW_TEST' in content or 
                        'performance' in test_file.lower() or
                        'integration' in test_file.lower())
        except:
            return False
    
    def _print_summary(self, total_duration: float):
        """Print test execution summary."""
        print("\n" + "=" * 60)
        print("TEST EXECUTION SUMMARY")
        print("=" * 60)
        
        if not self.results:
            print("No tests were executed.")
            return
        
        # Overall statistics
        total_tests = len(self.results)
        passed = sum(1 for r in self.results if r.status == 'passed')
        failed = sum(1 for r in self.results if r.status == 'failed')
        errors = sum(1 for r in self.results if r.status == 'error')
        skipped = sum(1 for r in self.results if r.status == 'skipped')
        
        print(f"Total Tests: {total_tests}")
        print(f"✅ Passed: {passed}")
        print(f"❌ Failed: {failed}")
        print(f"💥 Errors: {errors}")
        print(f"⏭️  Skipped: {skipped}")
        print(f"⏱️  Total Time: {total_duration:.2f}s")
        
        # Success rate
        if total_tests > 0:
            success_rate = (passed / total_tests) * 100
            print(f"📈 Success Rate: {success_rate:.1f}%")
        
        # Category breakdown
        categories = {}
        for result in self.results:
            if result.category not in categories:
                categories[result.category] = {'passed': 0, 'failed': 0, 'error': 0, 'skipped': 0}
            categories[result.category][result.status] += 1
        
        if len(categories) > 1:
            print(f"\n📊 Results by Category:")
            for category, stats in categories.items():
                total_cat = sum(stats.values())
                passed_cat = stats['passed']
                print(f"  {category}: {passed_cat}/{total_cat} passed")
        
        # Show failures and errors
        failures = [r for r in self.results if r.status in ['failed', 'error']]
        if failures:
            print(f"\n❌ Failed Tests:")
            for failure in failures:
                print(f"  {failure.name} ({failure.category}) - {failure.status}")
                if self.verbose and failure.error:
                    # Show first few lines of error
                    error_lines = failure.error.split('\n')[:3]
                    for line in error_lines:
                        print(f"    {line}")
    
    def list_tests(self):
        """List all available tests organized by category."""
        tests_by_category = self.discover_tests()
        
        if not tests_by_category:
            print("No tests found!")
            return
        
        print("Available Tests:")
        print("=" * 40)
        
        for category, test_files in tests_by_category.items():
            print(f"\n📁 {category.upper()}")
            for test_file in test_files:
                test_name = Path(test_file).stem
                # Try to get test description
                description = self._get_test_description(test_file)
                if description:
                    print(f"  🧪 {test_name} - {description}")
                else:
                    print(f"  🧪 {test_name}")
    
    def _get_test_description(self, test_file: str) -> Optional[str]:
        """Extract test description from docstring."""
        try:
            with open(test_file, 'r') as f:
                content = f.read()
                # Look for module docstring
                lines = content.split('\n')
                in_docstring = False
                docstring_lines = []
                
                for line in lines:
                    if '"""' in line and not in_docstring:
                        in_docstring = True
                        # Get text after opening quotes
                        after_quotes = line.split('"""', 1)[1]
                        if after_quotes.strip():
                            docstring_lines.append(after_quotes.strip())
                    elif '"""' in line and in_docstring:
                        # End of docstring
                        before_quotes = line.split('"""')[0]
                        if before_quotes.strip():
                            docstring_lines.append(before_quotes.strip())
                        break
                    elif in_docstring:
                        docstring_lines.append(line.strip())
                
                if docstring_lines:
                    # Return first non-empty line
                    for line in docstring_lines:
                        if line:
                            return line
        except:
            pass
        return None
    
    def generate_report(self, output_file: str = "test_report.json"):
        """Generate a detailed test report."""
        report = {
            'timestamp': datetime.now().isoformat(),
            'summary': {
                'total_tests': len(self.results),
                'passed': sum(1 for r in self.results if r.status == 'passed'),
                'failed': sum(1 for r in self.results if r.status == 'failed'),
                'errors': sum(1 for r in self.results if r.status == 'error'),
                'skipped': sum(1 for r in self.results if r.status == 'skipped'),
                'total_duration': sum(r.duration for r in self.results),
            },
            'results': []
        }
        
        for result in self.results:
            report['results'].append({
                'name': result.name,
                'category': result.category,
                'status': result.status,
                'duration': result.duration,
                'error': result.error,
                'timestamp': result.timestamp.isoformat()
            })
        
        with open(output_file, 'w') as f:
            json.dump(report, f, indent=2)
        
        print(f"📄 Test report generated: {output_file}")


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Ziya Backend System Test Runner",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python run_backend_system_tests.py                    # Run all tests
  python run_backend_system_tests.py --category core    # Run core tests only
  python run_backend_system_tests.py --list             # List available tests
  python run_backend_system_tests.py --fast --verbose   # Fast mode with verbose output
        """
    )
    
    parser.add_argument('--category', '-c', action='append',
                       help='Run tests from specific category (can be used multiple times)')
    parser.add_argument('--list', '-l', action='store_true',
                       help='List available tests and exit')
    parser.add_argument('--verbose', '-v', action='store_true',
                       help='Verbose output')
    parser.add_argument('--fast', '-f', action='store_true',
                       help='Skip slow tests')
    parser.add_argument('--report', '-r', action='store_true',
                       help='Generate detailed JSON report')
    parser.add_argument('--report-file', default='test_report.json',
                       help='Output file for report (default: test_report.json)')
    
    args = parser.parse_args()
    
    # Create test runner
    runner = ZiyaTestRunner()
    runner.verbose = args.verbose
    runner.fast_mode = args.fast
    
    # List tests if requested
    if args.list:
        runner.list_tests()
        return
    
    # Run tests
    success = runner.run_tests(args.category)
    
    # Generate report if requested
    if args.report:
        runner.generate_report(args.report_file)
    
    # Exit with appropriate code
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
