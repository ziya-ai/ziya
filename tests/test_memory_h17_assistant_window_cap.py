# H17 (iteration 9) REJECTED — reverted at iter-9 verification.
#
# The assistant-taught-window admission + tight per-window cap
# (ASSIST_ONLY_WINDOW_CANDIDATE_CAP) controlled over-production
# (extracted_count 194 < 198, unlike H16's 251) but precision still
# regressed hard (0.9348 -> 0.814, -0.1208) and the composite fell
# (0.7203 -> 0.7042, -0.0161). Two step-(d) REVERT triggers fired, so
# app/memory/extractor.py was restored byte-identical to backup/iter-9
# (sha cc6cc511) and this test — which imports the now-absent
# ASSIST_ONLY_WINDOW_CANDIDATE_CAP symbol — was archived to:
#     .ziya/memory-improvement/rejected-tests/iter-9/test_memory_h17_assistant_window_cap.py
# See state.json scorecards[iteration 9] and backlog H17 for the lesson.
