"""change_ast_resolution must re-index the requested project.

The route runs it on a bare threading.Thread.  A thread has no contextvars,
so inside it get_project_root() falls through to ZIYA_USER_CODEBASE_DIR or
os.getcwd() -- the server's launch directory -- and both the enhancer lookup
(keyed by root) and the directory walked come out wrong.  The root is now
resolved in the request and passed in.
"""
import os
import threading

import pytest

from app.utils.ast_parser import integration


class _FakeEnhancer:
    def __init__(self):
        self.ast_resolution = "minimal"
        self.ast_cache = {"stale": 1}
        self.query_engines = {"stale": 1}
        self.project_ast = object()
        self.processed = []

    def process_codebase(self, codebase_dir, patterns, max_depth):
        self.processed.append(codebase_dir)
        return {"files_processed": 0}


@pytest.fixture
def project(tmp_path, monkeypatch):
    root = os.path.abspath(str(tmp_path))
    enh = _FakeEnhancer()
    monkeypatch.setitem(integration._enhancers, root, enh)
    monkeypatch.setattr("app.utils.directory_util.get_ignored_patterns", lambda d: [])
    monkeypatch.delenv("ZIYA_USER_CODEBASE_DIR", raising=False)
    monkeypatch.delenv("ZIYA_AST_RESOLUTION", raising=False)
    assert os.getcwd() != root, "test needs cwd to differ from the project"
    return root, enh


def _run_in_bare_thread(fn):
    """Run fn the way ast_routes does: a thread with no request context."""
    err = []

    def _target():
        try:
            fn()
        except Exception as e:  # noqa: BLE001 -- surfaced to the test
            err.append(e)

    t = threading.Thread(target=_target)
    t.start()
    t.join(10)
    assert not err, err


def test_explicit_root_reindexes_that_project(project):
    root, enh = project
    _run_in_bare_thread(
        lambda: integration.change_ast_resolution("medium", project_root=root))
    assert enh.processed == [root]
    assert enh.ast_resolution == "medium"
    assert enh.ast_cache == {} and enh.query_engines == {}


def test_without_root_a_bare_thread_misses_the_project(project):
    """Negative control: the pre-fix call shape.  In a context-less thread the
    root falls back to cwd, no enhancer is found for it, and the project the
    user asked to re-index is never touched."""
    root, enh = project
    _run_in_bare_thread(lambda: integration.change_ast_resolution("medium"))
    assert enh.processed == []
    assert enh.ast_resolution == "minimal"
