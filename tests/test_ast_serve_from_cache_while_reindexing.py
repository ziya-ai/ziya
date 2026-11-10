"""The last persisted AST index is served immediately; re-indexing improves it.

Opening a project used to mean minutes with no AST at all while the full
walk ran.  Now the disk cache is hydrated into the live enhancer first (no
tree walk), and the pass that follows re-parses only changed files,
prunes deleted ones and rebuilds the project AST.
"""
import time

import pytest

import app.utils.ast_parser.ziya_ast_enhancer as Z
from app.utils.ast_parser.disk_cache import load_cache
from app.utils.ast_parser.ziya_ast_enhancer import ZiyaASTEnhancer


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.setenv("ZIYA_HOME", str(tmp_path / "home"))
    src = tmp_path / "proj"
    src.mkdir()
    (src / "a.py").write_text("def alpha():\n    return 1\n")
    (src / "b.py").write_text("def beta():\n    return 2\n")
    ZiyaASTEnhancer(ast_resolution="medium")._process_directory(
        str(src), lambda p: False, 15)  # cold pass: writes the disk cache
    return src


def _names(ast):
    return {n.name for n in ast.nodes.values()}


def test_hydration_serves_without_a_walk(project, monkeypatch):
    e = ZiyaASTEnhancer(ast_resolution="medium")
    walked = []
    monkeypatch.setattr(Z.os, "walk", lambda *a, **k: walked.append(a) or iter(()))
    n = e._hydrate_from_cache(load_cache(str(project)))
    assert n == 2 and walked == []
    assert set(e.ast_cache) == {str(project / "a.py"), str(project / "b.py")}
    assert "project" in e.query_engines
    assert {"alpha", "beta"} <= _names(e.project_ast)


def test_hydration_is_idempotent_and_skips_empty(project):
    e = ZiyaASTEnhancer(ast_resolution="medium")
    assert e._hydrate_from_cache({}) == 0
    assert e._hydrate_from_cache(load_cache(str(project))) == 2
    assert e._hydrate_from_cache(load_cache(str(project))) == 0, "already live"


def test_reindex_replaces_stale_nodes_in_project_ast(project):
    time.sleep(0.02)
    (project / "a.py").write_text("def gamma():\n    return 3\n")
    e = ZiyaASTEnhancer(ast_resolution="medium")
    e._process_directory(str(project), lambda p: False, 15)
    names = _names(e.project_ast)
    assert "gamma" in names, "re-parsed symbol reaches the project AST"
    assert "alpha" not in names, "stale hydrated symbol must not win the merge"
    assert "gamma" in _names(e.ast_cache[str(project / "a.py")])
    assert e.query_engines[str(project / "a.py")].ast is e.ast_cache[str(project / "a.py")]


def test_reindex_prunes_deleted_files(project):
    (project / "b.py").unlink()
    e = ZiyaASTEnhancer(ast_resolution="medium")
    e._process_directory(str(project), lambda p: False, 15)
    assert str(project / "b.py") not in e.ast_cache
    assert str(project / "b.py") not in e.query_engines
    assert "beta" not in _names(e.project_ast)


def test_truncated_walk_keeps_hydrated_entries(project, monkeypatch):
    # A deadline-cut walk saw nothing; hydrated files must survive, since
    # "not reached" is not "deleted".
    monkeypatch.setenv("ZIYA_AST_TIMEOUT", "-1")
    e = ZiyaASTEnhancer(ast_resolution="medium")
    e._process_directory(str(project), lambda p: False, 15)
    assert set(e.ast_cache) == {str(project / "a.py"), str(project / "b.py")}


def test_unchanged_pass_reuses_hydrated_objects(project):
    e = ZiyaASTEnhancer(ast_resolution="medium")
    e._hydrate_from_cache(load_cache(str(project)))
    before = dict(e.ast_cache)
    e._process_directory(str(project), lambda p: False, 15)
    assert all(e.ast_cache[k] is v for k, v in before.items()), "no second from_dict"
