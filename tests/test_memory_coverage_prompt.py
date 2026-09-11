# Intentionally empty.
#
# The iteration-1 H3 coverage-priming prompt change these tests pinned was
# REVERTED at verification (it regressed the golden scorer: composite
# 0.7103 -> 0.6987, precision -0.0651 and granularity -0.0742). The tests
# assert a PRIORITIZE directive that no longer exists in
# EXTRACTION_SYSTEM_PROMPT and would fail on the reverted code, so they have
# been moved out of the collected suite to:
#     .ziya/memory-improvement/rejected-tests/iter-1/test_memory_coverage_prompt.py
# See state.json backlog item H3 (status: rejected) for why the hypothesis
# was rejected and what a future coverage lever must avoid.
