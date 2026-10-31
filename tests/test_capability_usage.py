"""
Last-use stamps that order the bench's "within reach" and "shelved" lanes.

Covers the store (app/utils/capability_usage.py) and each seam a use or a
read crosses:

  * get_skill_details success -> skill:<name> stamped (a failed lookup is not)
  * MCPManager.call_tool past the permission check -> mcp:<server> stamped
  * GET /bench -> every item carries ``last_used`` (null when never used)
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.utils import capability_usage as cu


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    monkeypatch.setenv("ZIYA_HOME", str(tmp_path))
    cu._recent.clear()
    yield tmp_path
    cu._recent.clear()


# -- store -----------------------------------------------------------------

def test_record_and_read_round_trip():
    assert cu.last_used_map() == {}
    cu.record_use("skill:a", at_ms=1_000)
    cu.record_mcp_use("jira")
    m = cu.last_used_map()
    assert m["skill:a"] == 1_000
    assert m["mcp:jira"] > 1_000


def test_a_stamp_only_moves_forward_across_processes():
    cu.record_use("skill:a", at_ms=5_000_000)
    cu._recent.clear()                     # as if another process wrote
    cu.record_use("skill:a", at_ms=1_000)
    assert cu.last_used_map()["skill:a"] == 5_000_000


def test_same_process_writes_a_key_at_most_once_per_interval():
    cu.record_use("skill:a", at_ms=10_000_000)
    cu.record_use("skill:a", at_ms=10_000_000 + cu.MIN_INTERVAL_MS - 1)
    assert cu.last_used_map()["skill:a"] == 10_000_000
    cu.record_use("skill:a", at_ms=10_000_000 + cu.MIN_INTERVAL_MS)
    assert cu.last_used_map()["skill:a"] == 10_000_000 + cu.MIN_INTERVAL_MS


def test_unreadable_or_malformed_file_reads_empty(_home):
    path = _home / "state" / "capability_usage.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"last_used": {"skill:a": "yesterday", "skill:b": 7, "x": True}}))
    assert cu.last_used_map() == {"skill:b": 7}
    path.write_text("not json")
    assert cu.last_used_map() == {}
    cu.record_use("skill:c", at_ms=1)     # recovers rather than raising
    assert cu.last_used_map() == {"skill:c": 1}


def test_bad_keys_are_ignored():
    cu.record_use("")
    cu.record_use("nocolon")
    assert cu.last_used_map() == {}


# -- seam: skill load ------------------------------------------------------

@pytest.mark.asyncio
async def test_get_skill_details_stamps_the_bench_key():
    from app.data.built_in_skills import get_skill_by_id
    from app.mcp.tools.skill_tools import GetSkillDetailsTool
    name = get_skill_by_id("code_review")["name"]

    res = await GetSkillDetailsTool().execute(skill_name="code_review")
    assert f"[Skill: {name}]" in res["content"]
    assert f"skill:{name}" in cu.last_used_map()


@pytest.mark.asyncio
async def test_failed_skill_lookup_stamps_nothing():
    from app.mcp.tools.skill_tools import GetSkillDetailsTool
    res = await GetSkillDetailsTool().execute(skill_name="no-such-skill-zz9")
    assert res.get("error")
    assert cu.last_used_map() == {}


# -- seam: MCP tool call ---------------------------------------------------

@pytest.mark.asyncio
async def test_mcp_tool_call_stamps_its_server():
    from app.mcp.manager import MCPManager

    async def _call(*a, **k):
        return {"content": [{"type": "text", "text": "ok"}]}

    m = MCPManager()
    m.clients = {"usage-srv": SimpleNamespace(
        is_connected=True, tools=[SimpleNamespace(name="usage_probe_tool_zz9")],
        call_tool=_call)}
    m._quarantined_servers = set()
    try:
        await m.call_tool("mcp_usage_probe_tool_zz9", {})
    except Exception:
        pass  # dispatch past the stamp is not what this test is about
    assert "mcp:usage-srv" in cu.last_used_map()


@pytest.mark.asyncio
async def test_quarantined_server_call_stamps_nothing():
    from app.mcp.manager import MCPManager
    m = MCPManager()
    m.clients = {"q-srv": SimpleNamespace(
        is_connected=True, tools=[SimpleNamespace(name="usage_q_tool_zz9")])}
    m._quarantined_servers = {"q-srv"}
    res = await m.call_tool("usage_q_tool_zz9", {})
    assert res and res.get("error")
    assert cu.last_used_map() == {}


# -- seam: GET /bench ------------------------------------------------------

def test_bench_view_carries_last_used(monkeypatch):
    from app.api import bench as bench_api
    from app.models.activation import ItemSpec
    from app.services import bench as svc

    items = [
        svc.BenchItem(ItemSpec(key="skill:used", kind="skill"), "used", "user", tokens=1),
        svc.BenchItem(ItemSpec(key="skill:never", kind="skill"), "never", "user", tokens=1),
    ]
    monkeypatch.setattr(svc, "collect_items", lambda pid: items)
    cu.record_use("skill:used", at_ms=42_000)

    app = FastAPI(); app.include_router(bench_api.router)
    r = TestClient(app).get("/api/v1/projects/p1/bench")
    assert r.status_code == 200, r.text
    by_key = {i["key"]: i for i in r.json()["items"]}
    assert by_key["skill:used"]["last_used"] == 42_000
    assert by_key["skill:never"]["last_used"] is None
