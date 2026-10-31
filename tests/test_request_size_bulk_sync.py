"""
RequestSizeMiddleware: the bulk-sync route gets a larger body limit than the
20 MB default.

The frontend batches bulk-sync by size (conversationSyncApi.planBulkSyncBatches),
so only a single conversation larger than the default limit reaches it.
Before this change the middleware matched path PREFIXES only, which cannot
express /api/v1/projects/{pid}/chats/bulk-sync, so that conversation was
refused with 413 on every sync and never reached the server.
"""
from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

MB = 1024 * 1024


def _app(**kw):
    from app.middleware.request_size import RequestSizeMiddleware

    app = FastAPI()

    async def echo(request: Request):
        return {"bytes": len(await request.body())}

    for p in ("/api/v1/projects/{pid}/chats/bulk-sync",
              "/api/v1/projects/{pid}/chats/bulk-get",
              "/api/v1/projects/{pid}/chats/bulk-sync/extra",
              "/api/transcribe/jobs"):
        app.add_api_route(p, echo, methods=["POST"])
    app.add_middleware(RequestSizeMiddleware, default_max_size_mb=1, **kw)
    return TestClient(app)


def test_bulk_sync_pattern_raises_only_that_route():
    from app.middleware.request_size import BULK_SYNC_PATH_PATTERN

    client = _app(path_patterns={BULK_SYNC_PATH_PATTERN: 4 * MB})
    body = b"\0" * (2 * MB)
    ok = client.post("/api/v1/projects/p1/chats/bulk-sync", content=body)
    assert ok.status_code == 200 and ok.json()["bytes"] == len(body)
    # Neighbouring routes keep the default: the pattern is a full match.
    assert client.post("/api/v1/projects/p1/chats/bulk-get", content=body).status_code == 413
    assert client.post("/api/v1/projects/p1/chats/bulk-sync/extra", content=body).status_code == 413
    # The raised limit is still a limit.
    assert client.post("/api/v1/projects/p1/chats/bulk-sync", content=body * 3).status_code == 413


def test_prefix_limits_still_work_alongside_patterns():
    from app.middleware.request_size import BULK_SYNC_PATH_PATTERN

    client = _app(path_limits={"/api/transcribe": 4 * MB},
                  path_patterns={BULK_SYNC_PATH_PATTERN: 4 * MB})
    body = b"\0" * (2 * MB)
    assert client.post("/api/transcribe/jobs", content=body).status_code == 200


def test_bulk_sync_limit_comes_from_env(monkeypatch):
    from app.middleware.request_size import bulk_sync_request_limit

    monkeypatch.delenv("ZIYA_BULK_SYNC_MAX_MB", raising=False)
    assert bulk_sync_request_limit() == 256 * MB
    monkeypatch.setenv("ZIYA_BULK_SYNC_MAX_MB", "40")
    assert bulk_sync_request_limit() == 40 * MB


def test_real_server_applies_the_bulk_sync_limit():
    """The tests above build their own app; check app/server.py wires it."""
    from app.middleware.request_size import (
        BULK_SYNC_PATH_PATTERN, RequestSizeMiddleware,
    )
    from app.server import app as server_app

    mws = [m for m in server_app.user_middleware if m.cls is RequestSizeMiddleware]
    assert len(mws) == 1
    patterns = mws[0].kwargs.get("path_patterns") or {}
    assert patterns.get(BULK_SYNC_PATH_PATTERN, 0) > 20 * MB
