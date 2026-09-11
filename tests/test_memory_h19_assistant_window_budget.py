# H19 (assistant-taught window admission + durable-layer filter + survivor
# budget=2) was REJECTED at iter-11 verification and the extractor change was
# reverted byte-identical to backup/iter-11 (sha cc6cc511).
#
# The scorer sweep gave composite 0.7449 (+0.0246 over the latest accepted
# 0.7203) with coverage +0.0671 and retrievability +0.0683 both moving the
# right way and precision held within tolerance (-0.0250) — but extracted_count
# reached 213, breaching the 2x-ideal over-production ceiling of 198. That
# guardrail is a hard REVERT trigger (step (d) condition 4) and was the exact
# accept gate the H19 hypothesis pre-registered ("extracted_count<=198 …
# budget=2 may breach 198 — if so, this lever is dead").
#
# This test imports symbols (ASSIST_ONLY_WINDOW_SURVIVOR_BUDGET,
# ASSIST_WINDOW_DURABLE_LAYERS) that no longer exist on the reverted code, so
# it cannot live here. The full test is preserved for the record at:
#   .ziya/memory-improvement/rejected-tests/iter-11/test_memory_h19_assistant_window_budget.py
