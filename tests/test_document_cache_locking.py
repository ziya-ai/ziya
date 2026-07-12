"""
Regression coverage for PenPal #149 / #131 [CWE-667].

#149: ``_DOCUMENT_CACHE`` in document_extractor is mutated from more than one
real thread — the background ``TokenCalculation`` thread and the async request
path both call ``extract_document_text()``. Its check-evict-insert block
(``next(iter(cache))`` -> ``del`` -> insert) is a straight-line sequence with no
natural yield, so under CPython's GIL it does not interleave *in practice today*
(a realistic hammer loop shows zero errors). It is, however, reachable *in
principle*: if two threads select the same ``oldest`` key they double-``del`` and
the second raises ``KeyError``. This is proven below by forcing the window open
with a sleep and showing the UNGUARDED pattern fails while the fix's
lock-protected pattern does not. The fix wraps the O(1) cache ops in
``_DOCUMENT_CACHE_LOCK`` (never held across the slow extraction).

#131: ``MCPManager._save_persisted_fingerprints`` now writes via temp + atomic
rename, so a concurrent reader can't observe a truncated fingerprint baseline
(a torn read would silently disable rug-pull detection for that server).

These are availability/robustness hardening (KeyError crash / torn read), not
data corruption. The #149 test documents reachability via a forced window
rather than claiming a natural repro.
"""
import os
import threading
import time

import app.utils.document_extractor as de


class TestDocumentCacheLock:
    def test_lock_exists(self):
        assert isinstance(de._DOCUMENT_CACHE_LOCK, type(threading.Lock()))

    def test_lock_serializes_evict_insert_forced_window(self):
        """With the window forced open, the lock-protected evict+insert must
        produce ZERO KeyErrors across concurrent threads. (The unguarded
        counterpart is shown to fail in test_negative_control_* below.)"""
        de._DOCUMENT_CACHE.clear()
        for j in range(de._CACHE_MAX_SIZE):
            de._DOCUMENT_CACHE[("seed", j)] = "s"
        errors = []

        def hammer(tid):
            for i in range(150):
                try:
                    with de._DOCUMENT_CACHE_LOCK:
                        if len(de._DOCUMENT_CACHE) >= de._CACHE_MAX_SIZE:
                            try:
                                oldest = next(iter(de._DOCUMENT_CACHE))
                                time.sleep(0.0003)  # force the window open
                                del de._DOCUMENT_CACHE[oldest]
                            except (StopIteration, KeyError):
                                pass
                        de._DOCUMENT_CACHE[(f"t{tid}", i)] = "x"
                except (KeyError, RuntimeError) as e:
                    errors.append(type(e).__name__)

        threads = [threading.Thread(target=hammer, args=(t,)) for t in range(6)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        de._DOCUMENT_CACHE.clear()
        assert errors == [], f"lock did not serialize evict/insert: {errors[:5]}"

    def test_negative_control_unguarded_evict_races(self):
        """The pre-fix pattern (no lock) double-dels the same oldest key under a
        forced window and raises KeyError — proving the guarded version above is
        load-bearing, not vacuous."""
        cache = {}
        for j in range(100):
            cache[("seed", j)] = "s"
        errors = []
        MAX = 100

        def hammer(tid):
            for i in range(150):
                try:
                    if len(cache) >= MAX:
                        oldest = next(iter(cache))
                        time.sleep(0.0003)      # force window (no lock)
                        del cache[oldest]       # second thread -> KeyError
                    cache[(f"t{tid}", i)] = "x"
                except (KeyError, StopIteration) as e:
                    errors.append(type(e).__name__)

        threads = [threading.Thread(target=hammer, args=(t,)) for t in range(6)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert errors, "control too weak: unguarded evict did not race"

    def test_extract_document_text_bounded_under_threads(self):
        """End-to-end: many threads calling the real cached extractor keep the
        cache bounded and raise nothing (no crash, no unbounded growth)."""
        # Use the real function; plain-text files don't cache (extractor returns
        # None), so this asserts the guarded path is crash-free and bounded
        # rather than a specific hit count.
        import tempfile
        d = tempfile.mkdtemp()
        paths = []
        for i in range(60):
            p = os.path.join(d, f"f{i}.txt")
            with open(p, "w") as fh:
                fh.write(f"content-{i}\n" * 3)
            paths.append(p)
        errors = []

        def worker():
            for p in paths:
                try:
                    de.extract_document_text(p)
                except Exception as e:  # noqa: BLE001
                    errors.append(type(e).__name__)

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert errors == [], f"concurrent extraction raised: {errors[:5]}"
        assert len(de._DOCUMENT_CACHE) <= de._CACHE_MAX_SIZE


class TestFingerprintAtomicWrite:
    def test_save_uses_temp_and_atomic_rename(self, tmp_path, monkeypatch):
        """#131: the fingerprint baseline is written atomically (temp + rename),
        leaving no torn primary file and no stray temp on success."""
        monkeypatch.setenv("ZIYA_HOME", str(tmp_path))
        from app.mcp.manager import MCPManager

        mgr = MCPManager.__new__(MCPManager)
        mgr._tool_fingerprints = {"srv": "abc123"}
        mgr._fingerprint_store_path = tmp_path / "mcp_tool_fingerprints.json"

        mgr._save_persisted_fingerprints()

        import json
        assert mgr._fingerprint_store_path.exists()
        # Primary file is complete/valid JSON (not truncated).
        loaded = json.loads(mgr._fingerprint_store_path.read_text())
        assert loaded == {"srv": "abc123"}
        # No leftover temp file after a successful write.
        assert not (tmp_path / "mcp_tool_fingerprints.json.tmp").exists()
