# H18 (iteration 10) was REVERTED at iter-10 verification.
#
# The precision-filtered assistant-taught window admission scored composite
# 0.7173 vs latest accepted 0.7203 (-0.0030), failing the >=+0.01 bar. The
# layer filter DID hold precision within tolerance (0.9182, -0.0166) and
# controlled count (191 < 198) -- succeeding where H16 (-0.0421) and H17
# (-0.1208) failed -- but the target dim coverage did NOT rise (0.4692 ->
# 0.4612), so there was no net benefit. app/memory/extractor.py was restored
# byte-identical to backup/iter-10 (sha cc6cc511); the symbols this test
# imported (ASSIST_ONLY_WINDOW_CANDIDATE_CAP, ASSIST_WINDOW_DURABLE_LAYERS)
# no longer exist, so the test would fail at collection on the reverted code.
#
# The original test is preserved at:
#   .ziya/memory-improvement/rejected-tests/iter-10/test_memory_h18_assistant_window_layer_filter.py
