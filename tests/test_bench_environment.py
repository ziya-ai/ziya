"""
Environment tier (design/capabilities-hub.md, slice 6b-i): builtin tool
categories are bench items nobody can place, and their toggles persist.
"""
from __future__ import annotations

import asyncio

import pytest

import app.mcp.builtin_tools as bt
from app.models.activation import PlacementRejected
from app.services import bench as svc
from app.services.activation import validate_placement

PID = "proj1"


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("ZIYA_HOME", str(tmp_path))
    for cat in bt.BUILTIN_TOOL_CATEGORIES:
        monkeypatch.delenv(f"ZIYA_ENABLE_{cat.upper()}", raising=False)
    bt.invalidate_category_cache()
    monkeypatch.setattr(svc, "_skill_items", lambda pid: [])
    monkeypatch.setattr(svc, "_mcp_items", lambda: [])
    yield
    bt.invalidate_category_cache()


def test_persisted_toggle_survives_cache_clear_and_env_still_wins(env, monkeypatch):
    cat = "fileio"
    assert bt.BUILTIN_TOOL_CATEGORIES[cat]["enabled_by_default"] is True
    assert bt.is_builtin_category_enabled(cat) is True
    bt.set_builtin_category_enabled(cat, False)
    assert bt.is_builtin_category_enabled(cat) is False           # took effect now
    monkeypatch.delenv(f"ZIYA_ENABLE_{cat.upper()}")               # simulate a restart:
    bt.invalidate_category_cache()                                 # no env, cold cache
    assert bt.is_builtin_category_enabled(cat) is False           # persisted value read
    monkeypatch.setenv(f"ZIYA_ENABLE_{cat.upper()}", "true")       # explicit launch override
    bt.invalidate_category_cache()
    assert bt.is_builtin_category_enabled(cat) is True


def test_setter_clears_the_category_cache_the_old_route_did_not(env):
    cat = "ast"
    assert bt.is_builtin_category_enabled(cat) is True             # populates cache
    bt.set_builtin_category_enabled(cat, False)
    assert bt._category_enabled_cache.get(cat) in (None, False)
    assert bt.is_builtin_category_enabled(cat) is False
    with pytest.raises(KeyError):
        bt.set_builtin_category_enabled("nope", True)


def test_categories_are_environment_items_and_never_placeable(env):
    items = svc.collect_items(PID)
    keys = {i.spec.key for i in items}
    assert "builtin:fileio" in keys and "builtin:skills" in keys
    assert "builtin:pcap_analysis" not in keys                     # hidden
    assert all(i.spec.tier == "environment" and i.spec.kind == "builtin" for i in items)
    fileio = next(i for i in items if i.spec.key == "builtin:fileio")
    assert fileio.health["toggleable"] is True and fileio.health["tool_count"] >= 1
    assert fileio.tokens > 0
    with pytest.raises(PlacementRejected) as ei:
        validate_placement([i.spec for i in items], "builtin:fileio", "user", "off")
    assert ei.value.reason == "environment_not_placeable"
    res = svc.resolve_bench(PID, None, items)
    assert all(r.effective == "always" and r.origin == "environment" for r in res.items)


def test_detail_for_a_category_lists_its_tools(env):
    d = svc.item_detail(PID, "builtin:fileio")
    assert d["kind"] == "builtin" and d["category"] == "fileio"
    assert "file_read" in d["tools"]
    with pytest.raises(svc.ItemNotFound):
        svc.item_detail(PID, "builtin:nope")


def test_environment_route_toggles_reweights_and_warns_for_skills(env):
    from fastapi import HTTPException
    from app.api.bench import EnvironmentRequest, get_bench, put_environment
    before = asyncio.run(get_bench(PID, None))
    fileio_tokens = next(i["tokens"] for i in before["items"] if i["key"] == "builtin:fileio")
    assert fileio_tokens > 0

    after = asyncio.run(put_environment(PID, EnvironmentRequest(category="fileio", enabled=False), None))
    item = next(i for i in after["items"] if i["key"] == "builtin:fileio")
    assert item["health"]["enabled"] is False
    assert after["weight"]["environment"] == before["weight"]["environment"] - fileio_tokens
    assert "warning" not in after

    warned = asyncio.run(put_environment(PID, EnvironmentRequest(category="skills", enabled=False), None))
    assert "within-reach" in warned["warning"]
    assert "warning" not in asyncio.run(put_environment(PID, EnvironmentRequest(category="skills", enabled=True), None))

    with pytest.raises(HTTPException) as ei:
        asyncio.run(put_environment(PID, EnvironmentRequest(category="nope", enabled=True), None))
    assert ei.value.status_code == 404


def test_legacy_toggle_route_goes_through_the_setter():
    import inspect
    from app.routes import mcp_routes
    src = inspect.getsource(mcp_routes.toggle_builtin_tool_category)
    assert "set_builtin_category_enabled(" in src
    assert "os.environ[" not in src
