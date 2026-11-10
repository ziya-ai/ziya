"""A warm AST pass must not re-serialise the cache, and projects index one
at a time.

Regression for the startup stall where three projects indexed concurrently
(one per reconnecting tab) and each warm pass re-gzipped an unchanged
46 MB cache, starving the event loop for minutes.
"""
import os
import threading
import time

import pytest

import app.utils.ast_parser.disk_cache as disk_cache
import app.utils.ast_parser.integration as integration
from app.utils.ast_parser.ziya_ast_enhancer import ZiyaASTEnhancer


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.setenv("ZIYA_HOME", str(tmp_path / "home"))
    src = tmp_path / "proj"
    src.mkdir()
    (src / "a.py").write_text("def a():\n    return 1\n")
    (src / "b.py").write_text("class B:\n    pass\n")
    return src


def _saves(monkeypatch):
    calls = []
    real = disk_cache.save_cache

    def spy(root, entries):
        calls.append(len(entries))
        real(root, entries)

    monkeypatch.setattr(disk_cache, "save_cache", spy)
    return calls


def test_warm_pass_skips_save_and_cold_pass_saves(project, monkeypatch):
    calls = _saves(monkeypatch)
    ZiyaASTEnhancer(ast_resolution="medium")._process_directory(
        str(project), lambda p: False, 15)
    assert calls == [2], "cold pass must persist the cache"

    ZiyaASTEnhancer(ast_resolution="medium")._process_directory(
        str(project), lambda p: False, 15)
    assert calls == [2], "warm pass with nothing changed must not re-save"


def test_changed_file_triggers_save(project, monkeypatch):
    calls = _saves(monkeypatch)
    ZiyaASTEnhancer(ast_resolution="medium")._process_directory(
        str(project), lambda p: False, 15)
    time.sleep(0.02)
    (project / "a.py").write_text("def a():\n    return 2\n\ndef c():\n    pass\n")
    e = ZiyaASTEnhancer(ast_resolution="medium")
    e._process_directory(str(project), lambda p: False, 15)
    assert calls == [2, 2]
    assert str(project / "a.py") in e.ast_cache, "re-parsed file is in the live index"


def test_removed_file_triggers_save(project, monkeypatch):
    calls = _saves(monkeypatch)
    ZiyaASTEnhancer(ast_resolution="medium")._process_directory(
        str(project), lambda p: False, 15)
    (project / "b.py").unlink()
    ZiyaASTEnhancer(ast_resolution="medium")._process_directory(
        str(project), lambda p: False, 15)
    assert calls == [2, 1], "a pruned file set must be written back"


def test_projects_index_one_at_a_time(tmp_path, monkeypatch):
    monkeypatch.setattr(integration, "_initialized_projects", set())
    monkeypatch.setattr(integration, "_indexing_in_progress", set())
    monkeypatch.setattr(integration, "_enhancers", {})
    active, peak = [0], [0]
    lock = threading.Lock()

    def fake_process(self, codebase_dir, ignored, max_depth):
        with lock:
            active[0] += 1
            peak[0] = max(peak[0], active[0])
        time.sleep(0.05)
        with lock:
            active[0] -= 1
        return {"files_processed": 1}

    monkeypatch.setattr(ZiyaASTEnhancer, "process_codebase", fake_process)
    roots = [tmp_path / f"p{i}" for i in range(3)]
    for r in roots:
        r.mkdir()
    ts = [threading.Thread(target=integration.initialize_ast_capabilities, args=(str(r),))
          for r in roots]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert peak[0] == 1, f"{peak[0]} projects indexed concurrently"
