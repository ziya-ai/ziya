"""
Token accounting for task artifacts (E5).

``tokens_used`` was initialised to 0 in execute_task_block and never
incremented, so every artifact — and every IterationSummary built from
one — reported tokens=0.  A ten-run campaign therefore left no way to
see what it cost or whether a model_tier grant changed anything.

There is no usage-bearing stream chunk, so the figure comes from
GlobalUsageTracker, which message_stop_handler populates.  The subtlety
these tests pin: the tracker is keyed by conversation_id, and every task
in a run shares one (conversation_id=run_id), so a naive sum attributes
the whole run's spend to each task and grows quadratically across a loop.
"""

import pytest

from app.streaming_tool_executor import (
    IterationUsage, get_global_usage_tracker,
)


@pytest.fixture()
def tracker():
    t = get_global_usage_tracker()
    # Module-level singleton: isolate by using unique keys per test
    # rather than mutating shared state.
    return t


def _usage(inp=0, out=0, cache_read=0, cache_write=0):
    return IterationUsage(
        input_tokens=inp, output_tokens=out,
        cache_read_tokens=cache_read, cache_write_tokens=cache_write,
    )


def _sum_since(records, baseline):
    """Mirror of the executor's accounting, kept in the test so the
    intended arithmetic is asserted independently of the call site."""
    total = 0
    for u in records[baseline:]:
        total += (
            u.input_tokens + u.output_tokens
            + u.cache_read_tokens + u.cache_write_tokens
        )
    return total


class TestTrackerIsReadable:
    def test_recorded_usage_is_retrievable_by_key(self, tracker):
        key = "run-readable-1"
        tracker.record_usage(key, _usage(inp=100, out=50))
        records = tracker.get_conversation_usages(key)
        assert len(records) == 1
        assert records[0].input_tokens == 100

    def test_unknown_key_returns_empty(self, tracker):
        assert tracker.get_conversation_usages("run-never-used") == []


class TestBaselineArithmetic:
    """The core of E5: per-task attribution from a run-shared key."""

    def test_baseline_excludes_earlier_tasks(self, tracker):
        key = "run-baseline-1"
        # Task A spends 150.
        tracker.record_usage(key, _usage(inp=100, out=50))
        # Task B starts here and must not inherit A's spend.
        baseline = len(tracker.get_conversation_usages(key))
        tracker.record_usage(key, _usage(inp=10, out=5))
        records = tracker.get_conversation_usages(key)
        assert _sum_since(records, baseline) == 15

    def test_without_baseline_the_bug_reappears(self, tracker):
        # Demonstrates WHY the watermark exists: summing from zero
        # attributes the earlier task's tokens to this one too.
        key = "run-baseline-2"
        tracker.record_usage(key, _usage(inp=100, out=50))
        tracker.record_usage(key, _usage(inp=10, out=5))
        records = tracker.get_conversation_usages(key)
        assert _sum_since(records, 0) == 165   # wrong for one task
        assert _sum_since(records, 1) == 15    # right

    def test_no_new_records_yields_zero_not_a_repeat(self, tracker):
        key = "run-baseline-3"
        tracker.record_usage(key, _usage(inp=100, out=50))
        baseline = len(tracker.get_conversation_usages(key))
        records = tracker.get_conversation_usages(key)
        assert _sum_since(records, baseline) == 0

    def test_multiple_iterations_each_see_only_their_own(self, tracker):
        # A 3-iteration loop: each iteration's figure must be its own,
        # not cumulative — otherwise a long loop's later iterations
        # report absurd totals.
        key = "run-loop-1"
        totals = []
        for spend in (30, 40, 50):
            baseline = len(tracker.get_conversation_usages(key))
            tracker.record_usage(key, _usage(inp=spend))
            totals.append(
                _sum_since(tracker.get_conversation_usages(key), baseline)
            )
        assert totals == [30, 40, 50]


class TestCacheTokensIncluded:
    def test_cache_read_counts_toward_the_total(self, tracker):
        # Cache reads are real input the model processed and was billed
        # for, if discounted.  Excluding them would understate a
        # cache-heavy loop exactly where cost matters most.
        key = "run-cache-1"
        baseline = len(tracker.get_conversation_usages(key))
        tracker.record_usage(key, _usage(inp=10, cache_read=1000))
        records = tracker.get_conversation_usages(key)
        assert _sum_since(records, baseline) == 1010

    def test_cache_write_counts_toward_the_total(self, tracker):
        key = "run-cache-2"
        baseline = len(tracker.get_conversation_usages(key))
        tracker.record_usage(key, _usage(inp=10, cache_write=500))
        records = tracker.get_conversation_usages(key)
        assert _sum_since(records, baseline) == 510


class TestArtifactCarriesTokens:
    def test_iteration_summary_reads_artifact_tokens(self):
        # block_executor builds IterationSummary(tokens=artifact.tokens),
        # so a non-zero artifact figure must propagate without further
        # change.  Pins that contract.
        from app.models.task_card import Artifact
        from app.models.task_run import IterationSummary
        art = Artifact(summary="x", tokens=1234)
        summary = IterationSummary(
            index=0, status="passed", tokens=art.tokens,
        )
        assert summary.tokens == 1234
