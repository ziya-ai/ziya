"""
Master test that runs ALL diff test cases via pytest.

DiffRegressionTest (in run_diff_tests.py) dynamically generates a
test_<name> method for every directory in diff_test_cases/ at import
time.  Re-exporting the class under a Test* alias is all that's needed
for pytest to collect every case — hand-written and auto-generated.

Usage:
    pytest tests/test_all_diff_cases.py -v
    pytest tests/test_all_diff_cases.py -k basic_addition   # single case
"""

from tests.run_diff_tests import DiffRegressionTest as TestAllDiffCases  # noqa: F401
