# Iteration 4 (backlog H11) was REVERTED at verification.
#
# The worked multi-fact -> atomic SPLIT example added to
# EXTRACTION_SYSTEM_PROMPT scored composite 0.7094 vs the latest accepted
# 0.7103 (-0.0009, fails the >=+0.01 bar) and — critically — the TARGET
# dimension granularity did NOT move at all (0.7842 -> 0.7842): a single
# worked example did not shift the extraction model's output distribution.
#
# The change was reverted (app/memory/extractor.py restored to backup
# sha cc6cc511). This test asserts the reverted prompt content, so it is
# preserved (not deleted) at:
#     .ziya/memory-improvement/rejected-tests/iter-4/test_memory_split_example_prompt.py
#
# See .ziya/memory-improvement/state.json (scorecards iter-4, backlog H11)
# for the full rejection rationale.
