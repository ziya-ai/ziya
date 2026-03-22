"""
Tests for app.config.app_config.env_bool — the canonical boolean env var parser.

Verifies that all truthy/falsy representations behave consistently,
matching the contract that "true", "1", "yes" (case-insensitive) are truthy
and everything else is falsy.
"""

import os
import unittest


class TestEnvBool(unittest.TestCase):
    """env_bool must handle all representations identically."""

    KEY = "_ZIYA_TEST_ENV_BOOL"

    def tearDown(self):
        os.environ.pop(self.KEY, None)

    def _call(self, value=None, default=False):
        from app.config.app_config import env_bool
        if value is not None:
            os.environ[self.KEY] = value
        else:
            os.environ.pop(self.KEY, None)
        return env_bool(self.KEY, default)

    # --- truthy values ---

    def test_true_lowercase(self):
        self.assertTrue(self._call("true"))

    def test_true_uppercase(self):
        self.assertTrue(self._call("TRUE"))

    def test_true_mixed_case(self):
        self.assertTrue(self._call("True"))

    def test_one(self):
        self.assertTrue(self._call("1"))

    def test_yes(self):
        self.assertTrue(self._call("yes"))

    def test_yes_uppercase(self):
        self.assertTrue(self._call("YES"))

    def test_true_with_whitespace(self):
        self.assertTrue(self._call("  true  "))

    # --- falsy values ---

    def test_false_string(self):
        self.assertFalse(self._call("false"))

    def test_zero(self):
        self.assertFalse(self._call("0"))

    def test_no(self):
        self.assertFalse(self._call("no"))

    def test_empty_string(self):
        self.assertFalse(self._call(""))

    # --- default handling ---

    def test_unset_returns_default_false(self):
        self.assertFalse(self._call(default=False))

    def test_unset_returns_default_true(self):
        self.assertTrue(self._call(default=True))
