"""/api/accurate-token-count answers for the REQUEST's project, whole batch.

Two defects in the precalculated-cache branch, observed alongside the
"every folder reads 0/N" report (2026-10-03):

  * The background cache was one flat {relative_path: count} dict,
    overwritten by whichever project's background pass finished last, so a
    request for project B could be served project A's count for any file
    sharing a relative path.
  * When the cache held ANY requested file, the route returned only the
    cached ones; the rest of the batch was silently missing, so the client
    never got a count for them.

Driven through the real route; the project root comes from the same
``get_project_root`` the X-Project-Root middleware feeds.
"""

from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.utils import directory_util as du


@pytest.fixture
def client():
    from app.routes.token_routes import router
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


@pytest.fixture(autouse=True)
def clean_cache():
    saved = dict(du._accurate_token_cache)
    du._accurate_token_cache.clear()
    yield
    du._accurate_token_cache.clear()
    du._accurate_token_cache.update(saved)


def _post(client, root, paths):
    with patch("app.context.get_project_root", return_value=str(root)):
        resp = client.post("/api/accurate-token-count", json={"file_paths": paths})
    assert resp.status_code == 200, resp.text
    return resp.json()["results"]


def test_other_projects_cache_is_not_used(client, tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    for d in (a, b):
        d.mkdir()
    (b / "same.py").write_text("x = 1\n")
    du._accurate_token_cache[du._accurate_cache_key(str(a))] = {"same.py": 99999}

    res = _post(client, b, ["same.py"])
    assert "error" not in res["same.py"]
    assert 0 < res["same.py"]["accurate_count"] < 99999


def test_own_projects_cache_is_used(client, tmp_path):
    (tmp_path / "f.py").write_text("x = 1\n")
    du._accurate_token_cache[du._accurate_cache_key(str(tmp_path))] = {"f.py": 4242}
    assert _post(client, tmp_path, ["f.py"])["f.py"]["accurate_count"] == 4242


def test_partial_cache_hit_still_answers_every_file(client, tmp_path):
    (tmp_path / "cached.py").write_text("x = 1\n")
    (tmp_path / "fresh.py").write_text("y = 2\n")
    du._accurate_token_cache[du._accurate_cache_key(str(tmp_path))] = {"cached.py": 7}

    res = _post(client, tmp_path, ["cached.py", "fresh.py", "missing.py"])
    assert res["cached.py"]["accurate_count"] == 7
    assert res["fresh.py"]["accurate_count"] > 0 and "error" not in res["fresh.py"]
    assert res["missing.py"]["error"] == "File not found"


def test_get_cached_accurate_counts_is_scoped(tmp_path):
    du._accurate_token_cache[du._accurate_cache_key(str(tmp_path))] = {"x": 1}
    assert du.get_cached_accurate_counts(str(tmp_path)) == {"x": 1}
    assert du.get_cached_accurate_counts(str(tmp_path / "other")) == {}
    assert du.get_cached_accurate_counts(None) == {}
