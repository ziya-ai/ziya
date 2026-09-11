# Iteration 12 (backlog H20) was REVERTED at verification.
#
# The H20 change (assistant-taught durable window admission with a net-zero
# global candidate budget) scored composite 0.7227 vs the latest accepted
# 0.7203 = +0.0024, which FAILS the >=+0.01 acceptance bar. Decisively, its
# TARGET dimension coverage moved the WRONG way (0.4692 -> 0.4554, -0.0138):
# net-zero reallocation displaced user-window candidates with assistant-taught
# durable ones one-for-one, trading covered golden facts rather than adding
# them, so it cannot raise coverage. The small composite bump came from
# granularity/self_containment (+0.04 each) which the extractor change does not
# target -- run-to-run extraction noise (H15 floor ~+/-0.03).
#
# The extractor was reverted byte-identical to backup/iter-12 (sha cc6cc511)
# and this test (which imports the now-absent H20 symbols
# ASSIST_WINDOW_DURABLE_LAYERS / _apply_global_candidate_budget and would
# ImportError at collection) was archived to:
#     .ziya/memory-improvement/rejected-tests/iter-12/test_memory_h20_netzero_budget.py
#
# See state.json scorecards[iteration 12] and backlog H20 for the full rationale.
