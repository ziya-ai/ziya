# Test Results Directory

This directory contains saved test results from previous runs of the diff tests. These files are used to compare test results between runs to identify improvements or regressions.

## File Format

Test results are stored in JSON files with the following naming convention:
- `test_results_normal.json` - Results from tests run in normal mode
- `test_results_difflib.json` - Results from tests run in force-difflib mode

Each file contains:
- Timestamp of the test run
- Mode used for the test
- Results for each test case (PASS, FAIL, ERROR)

## Usage

To save test results for future comparison:
```bash
python tests/run_diff_tests.py --save-results
```

To compare current test results with previous runs:
```bash
python tests/run_diff_tests.py --compare-with-previous
```

You can combine these flags to save the current results after comparing:
```bash
python tests/run_diff_tests.py --compare-with-previous --save-results
```

For multi-mode testing with comparison:
```bash
python tests/run_diff_tests.py --multi-entry --compare-with-previous --save-results
```
