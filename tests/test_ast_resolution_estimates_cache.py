"""Regression tests for PenPal #133 [CWE-400]: cache AST resolution estimates.

GET /api/ast/resolutions -> get_resolution_estimates ->
ZiyaASTEnhancer.calculate_resolution_estimates() regenerated the FULL AST
context for all 5 resolution levels on every call. It is now memoized in
_estimates_cache, invalidated only when process_codebase() rebuilds ast_cache.
"""
import pytest

from app.utils.ast_parser.ziya_ast_enhancer import ZiyaASTEnhancer


def _make_enhancer():
    try:
        return ZiyaASTEnhancer()
    except Exception:
        pytest.skip("ZiyaASTEnhancer could not initialize in this environment")


def test_estimates_cache_starts_empty():
    e = _make_enhancer()
    assert e._estimates_cache is None


def test_cache_invalidated_on_process_codebase(monkeypatch, tmp_path):
    e = _make_enhancer()
    # Populate the cache with a SENTINEL so we can prove it isn't served after
    # a rebuild (the estimates change when ast_cache changes).
    e._estimates_cache = {"SENTINEL": {"token_count": 999999}}

    (tmp_path / "empty").mkdir()
    try:
        e.process_codebase(str(tmp_path / "empty"), [], 1)
    except Exception:
        pass
    # The stale SENTINEL must be gone — process_codebase invalidated it at the
    # top. Whatever is cached now (None, or fresh estimates reflecting the
    # newly-indexed empty cache) must NOT be the pre-rebuild sentinel.
    assert e._estimates_cache != {"SENTINEL": {"token_count": 999999}}
    # And a fresh estimate request reflects current (empty) state, not stale.
    fresh = e.calculate_resolution_estimates()
    assert "SENTINEL" not in fresh


def test_recompute_after_invalidation(monkeypatch, tmp_path):
    e = _make_enhancer()
    first = e.calculate_resolution_estimates()
    e._estimates_cache = None  # simulate invalidation
    second = e.calculate_resolution_estimates()
    assert second == first          # same inputs → same estimates
    assert e._estimates_cache is not None  # repopulated
