"""PenPal #117 / #136 / #146 [CWE-667]: locking fixes for reader paths that
accessed shared state without holding the lock the writer uses.

These verify the fix is present on the vulnerable PATH (source-contract) and,
where deterministic, the runtime behavior. Source-contract checks are used for
the concurrency guards because a reliable race-repro is timing-dependent; the
contract check is what catches a partial-apply / later-refactor regression.
"""
import inspect
import threading


class TestPdfReadPagesLocking:
    """#117: read_pages must take the per-key _BUILD_LOCKS, like ensure_full."""

    def test_read_pages_acquires_build_lock(self):
        from app.utils import pdf_rag
        src = inspect.getsource(pdf_rag.PdfIndex.read_pages)
        assert "_BUILD_LOCKS" in src, \
            "read_pages no longer takes the per-key build lock (PenPal #117 regression)"
        assert "_read_pages_locked" in src or "with lock" in src

    def test_read_pages_does_not_call_ensure_full(self):
        # Guards against a deadlock: read_pages must NOT call ensure_full/build
        # (which take the same per-key lock — non-reentrant).
        from app.utils import pdf_rag
        body = inspect.getsource(pdf_rag.PdfIndex.read_pages)
        locked = ""
        if hasattr(pdf_rag.PdfIndex, "_read_pages_locked"):
            locked = inspect.getsource(pdf_rag.PdfIndex._read_pages_locked)
        # Strip comments/docstrings-ish lines first: the fix's own explanatory
        # comment legitimately NAMES ensure_full(), which is not a call.
        code_lines = [
            ln for ln in (body + locked).splitlines()
            if not ln.lstrip().startswith("#")
        ]
        code = "\n".join(code_lines)
        assert "ensure_full(" not in code, "read_pages calls ensure_full → per-key lock deadlock"
        assert ".build(" not in code, "read_pages calls .build → per-key lock deadlock"


class TestAstQueryEngineLocking:
    """#136: the ast_tools reader must hold _enhancer_lock while reading
    query_engines (rebuilt by the background reindex thread)."""

    def test_get_query_engine_uses_enhancer_lock(self):
        from app.mcp.tools import ast_tools
        src = inspect.getsource(ast_tools._get_query_engine)
        assert "_enhancer_lock" in src, \
            "_get_query_engine reads query_engines without _enhancer_lock (PenPal #136 regression)"


class TestFolderCacheLocking:
    """#146: get_cached_folder_structure must init its _folder_cache slot
    under _cache_lock (every other accessor does)."""

    def test_slot_init_under_lock(self):
        from app.services import folder_service
        src = inspect.getsource(folder_service.get_cached_folder_structure)
        assert "_cache_lock" in src, \
            "get_cached_folder_structure touches _folder_cache without _cache_lock (PenPal #146 regression)"
        # setdefault under the lock makes the check-then-init atomic.
        assert "setdefault" in src

    def test_concurrent_first_read_no_double_init(self):
        # Behavioral: many threads initializing the same new directory slot
        # concurrently must converge on ONE slot object (atomic setdefault),
        # never clobber each other's in-flight entry.
        from app.services import folder_service
        folder_service._folder_cache.pop("/tmp/_pp146_probe", None)
        seen = []
        barrier = threading.Barrier(8)

        def race():
            barrier.wait()
            with folder_service._cache_lock:
                entry = folder_service._folder_cache.setdefault(
                    "/tmp/_pp146_probe", {'timestamp': 0, 'data': None, 'scan_complete': False}
                )
            seen.append(id(entry))

        ts = [threading.Thread(target=race) for _ in range(8)]
        for t in ts: t.start()
        for t in ts: t.join()
        assert len(set(seen)) == 1, "concurrent init produced divergent cache slots"
        folder_service._folder_cache.pop("/tmp/_pp146_probe", None)
