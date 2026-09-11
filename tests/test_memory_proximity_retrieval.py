# iter-6 [backlog H14] REVERTED — this test asserted a phrase-coherence /
# proximity bonus in MemoryStorage.search that did not clear the acceptance
# bar (see .ziya/memory-improvement/state.json changes[iteration==6]).
#
# The H14 change (app/storage/memory.py) was reverted byte-identical to
# .ziya/memory-improvement/backup/iter-6/, so this test's core assertion
# (a phrase-coherent memory outranking a scattered sibling) now FAILS on the
# restored code. The full test module is preserved at:
#   .ziya/memory-improvement/rejected-tests/iter-6/test_memory_proximity_retrieval.py
# in case the proximity lever is revisited with a deterministic/seeded
# extraction harness that can resolve a sub-noise-floor retrievability signal.
#
# Intentionally empty (no tests) so the suite stays green on the reverted code.
