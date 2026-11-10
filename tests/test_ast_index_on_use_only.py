"""AST indexing starts when a project is USED, not when a request names it.

After a restart every open tab reconnects with its own X-Project-Root, and
the middleware used to start an indexer per project -- three CPU-bound
threads sharing the GIL with the event loop, minutes of unresponsive UI.
Now only a chat turn or an AST tool call starts indexing.
"""
import os

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import app.middleware.project_context as pc


@pytest.fixture
def kickoffs(monkeypatch):
    calls = []
    monkeypatch.setattr(pc, "ensure_ast_indexed", lambda root: calls.append(root))
    return calls


def test_middleware_does_not_start_indexing(tmp_path, kickoffs, monkeypatch):
    # The staticmethod delegates to the module function; patch that too so
    # a regression back to self._ensure_ast_indexed(...) is also caught.
    monkeypatch.setattr(pc.ProjectContextMiddleware, "_ensure_ast_indexed",
                        staticmethod(lambda root: kickoffs.append(("legacy", root))))
    app = FastAPI()
    app.add_middleware(pc.ProjectContextMiddleware)
    seen = {}

    @app.get("/ping")
    async def ping():
        from app.context import get_project_root_or_none
        seen["root"] = get_project_root_or_none()
        return {"ok": True}

    TestClient(app).get("/ping", headers={"X-Project-Root": str(tmp_path)})
    assert seen["root"] and os.path.samefile(seen["root"], tmp_path), \
        "the ContextVar is still set from the header"
    assert kickoffs == [], "a request naming a project must not index it"


def test_ast_tool_miss_starts_indexing(tmp_path, kickoffs, monkeypatch):
    import app.utils.ast_parser.integration as integration
    from app.mcp.tools import ast_tools
    monkeypatch.setattr(integration, "get_current_enhancer", lambda: None)
    monkeypatch.setattr(integration, "_get_project_root", lambda: str(tmp_path))
    assert ast_tools._get_enhancer() is None
    assert kickoffs == [str(tmp_path)]


def test_ast_tool_hit_does_not_restart_indexing(tmp_path, kickoffs, monkeypatch):
    import app.utils.ast_parser.integration as integration
    from app.mcp.tools import ast_tools
    sentinel = object()
    monkeypatch.setattr(integration, "get_current_enhancer", lambda: sentinel)
    assert ast_tools._get_enhancer() is sentinel
    assert kickoffs == []


def test_stream_path_calls_ensure_ast_indexed_source_seam():
    # stream_chunks is too entangled to drive here; assert the call exists at
    # the point where the turn's project root is resolved.
    src = open(os.path.join(os.path.dirname(pc.__file__), "..", "server.py")).read()
    assert "ensure_ast_indexed(project_root)" in src
