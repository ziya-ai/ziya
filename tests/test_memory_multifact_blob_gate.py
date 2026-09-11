# iter-3 (backlog H10) verbose multi-fact-blob DROP rule was REVERTED at
# verification: the change scored composite 0.7105 vs the latest accepted
# 0.7103 (+0.0002), below the required >=+0.01 acceptance bar (a neutral
# change, not an improvement).  The extractor was restored byte-identical to
# backup/iter-3, so the symbols these tests referenced
# (_is_verbose_multifact_blob, _atomicity_sentence_count,
# _distinct_hard_fact_tokens, _MULTIFACT_MIN_*) no longer exist.
#
# The original test file is preserved at:
#   .ziya/memory-improvement/rejected-tests/iter-3/test_memory_multifact_blob_gate.py
