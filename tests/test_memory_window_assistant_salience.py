# iter-8 change (backlog H16: assistant-inclusive per-window salience admission)
# was REVERTED at iter-8 verification — composite rose (+0.0389) but PRECISION
# regressed -0.0421 (>0.03) and extracted_count exploded to 251 (>2x ideal=198).
# The tests that exercised the reverted `include_assistant` code path are moved to:
#   .ziya/memory-improvement/rejected-tests/iter-8/test_memory_window_assistant_salience.py
# See state.json backlog H16 rejection_outcome and changes[iteration 8].
