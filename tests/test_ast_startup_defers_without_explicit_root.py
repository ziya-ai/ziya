"""Startup indexes the launch directory only when it was given with --root.

setup_environment always sets ZIYA_USER_CODEBASE_DIR (to cwd when no --root),
so the old "defer when unset" guard never fired and every start indexed
whatever directory the shell happened to be in -- a 61 MB index of
~/workspace for a server whose real project is chosen in the browser.
"""
import threading
from unittest import mock

import pytest


@pytest.fixture(autouse=True)
def _no_leftover_threads():
    yield
    for t in threading.enumerate():
        if t.daemon and t is not threading.current_thread():
            t.join(timeout=5)


def _run(monkeypatch, tmp_path, explicit):
    monkeypatch.setenv("ZIYA_ENABLE_AST", "true")
    monkeypatch.setenv("ZIYA_USER_CODEBASE_DIR", str(tmp_path))
    if explicit:
        monkeypatch.setenv("ZIYA_EXPLICIT_ROOT", "true")
    else:
        monkeypatch.delenv("ZIYA_EXPLICIT_ROOT", raising=False)
    from app.utils import context_enhancer
    with mock.patch.object(context_enhancer, "initialize_ast_capabilities",
                           return_value={"files_processed": 1}) as init, \
         mock.patch.object(context_enhancer, "_broadcast_ast_complete"):
        context_enhancer.initialize_ast_if_enabled()
        for t in threading.enumerate():
            if t.daemon and t is not threading.current_thread():
                t.join(timeout=10)
        return init.call_count


def test_implicit_cwd_root_is_not_indexed_at_startup(tmp_path, monkeypatch):
    assert _run(monkeypatch, tmp_path, explicit=False) == 0


def test_explicit_root_is_indexed_at_startup(tmp_path, monkeypatch):
    assert _run(monkeypatch, tmp_path, explicit=True) == 1
