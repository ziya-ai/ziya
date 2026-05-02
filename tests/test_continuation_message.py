"""Tests for the continuation message branch logic in ZiyaREPL."""
import pytest


def _make_diff_results(*statuses):
    """Build a diff_results list with the given statuses."""
    return [(f"file{i}.py", status, "msg") for i, status in enumerate(statuses)]


def _get_continuation_branch(diff_results):
    """
    Inline the branch logic from cli.py so we can unit-test it
    without spinning up a full ZiyaREPL instance.
    """
    skipped = [r for r in diff_results if r[1] == "skipped"]
    applied = [r for r in diff_results if r[1] == "applied"]

    if skipped and not applied:
        return "all_skipped"
    else:
        return "confirm_or_continue"


class TestContinuationBranch:
    def test_all_applied(self):
        results = _make_diff_results("applied", "applied")
        assert _get_continuation_branch(results) == "confirm_or_continue"

    def test_all_skipped(self):
        results = _make_diff_results("skipped", "skipped")
        assert _get_continuation_branch(results) == "all_skipped"

    def test_empty_results(self):
        assert _get_continuation_branch([]) == "confirm_or_continue"

    def test_applied_and_skipped(self):
        """When some were applied and some skipped, user chose to skip — confirm is appropriate."""
        results = _make_diff_results("applied", "skipped")
        assert _get_continuation_branch(results) == "confirm_or_continue"

    def test_single_applied(self):
        results = _make_diff_results("applied")
        assert _get_continuation_branch(results) == "confirm_or_continue"

    def test_single_skipped(self):
        results = _make_diff_results("skipped")
        assert _get_continuation_branch(results) == "all_skipped"
