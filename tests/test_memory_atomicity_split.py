# Intentionally empty — the H9 atomicity-splitter change this file tested was
# REVERTED at iter-2 verification (composite regressed 0.7103 -> 0.6344; the
# structural splitter over-fired on real conversations and worsened
# granularity/self_containment/precision, extracted_count 232 > 2x-ideal 198).
# The original tests are preserved for provenance at:
#   .ziya/memory-improvement/rejected-tests/iter-2/test_memory_atomicity_split.py
# Do not re-add them without a splitter whose real-corpus scorecard clears the
# ACCEPT bar.
