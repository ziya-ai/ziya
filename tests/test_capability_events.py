"""
✦ pickup events (design/capabilities-hub.md, slice 5).

The seam is three hops: GetSkillDetailsTool's real output -> the
capability_used key -> a key GET /bench actually returns.  The last hop is
the one that can silently break (the tool matches by id, the bench keys by
name), so it is asserted against real SkillStorage, not a hand-written key.
"""
from __future__ import annotations

import asyncio
import inspect

import pytest

from app.mcp.tools.skill_tools import GetSkillDetailsTool
from app.services import bench as svc
from app.services.token_service import TokenService
from app.storage.skills import SkillStorage
from app.utils.capability_events import capability_used_event

PID = "proj1"


def _display(tool_name, result, **extra):
    return {"type": "tool_display", "tool_name": tool_name, "result": result, **extra}


def _run_tool(skill_name):
    return asyncio.run(GetSkillDetailsTool().execute(skill_name=skill_name))


def test_real_tool_output_yields_a_key_the_bench_returns(tmp_path, monkeypatch):
    monkeypatch.setenv("ZIYA_HOME", str(tmp_path))
    storage = SkillStorage(tmp_path / "projects" / PID, TokenService())
    monkeypatch.setattr(svc, "_skill_storage", lambda pid: storage)
    bench_keys = {i.spec.key for i in svc._skill_items(PID)}

    # The model asks by id; the bench keys by name.  They must meet.
    out = _run_tool("code_review")
    ev = capability_used_event(_display("get_skill_details", out["content"]))
    assert ev == {"type": "capability_used", "key": "skill:Code Review"}
    assert ev["key"] in bench_keys


@pytest.mark.parametrize("tool_name", ["get_skill_details", "mcp_get_skill_details"])
def test_prefixed_and_bare_tool_names(tool_name):
    ev = capability_used_event(_display(tool_name, "[Skill: Packet Diagrams]\n\nbody"))
    assert ev == {"type": "capability_used", "key": "skill:Packet Diagrams"}


def test_failed_lookup_emits_nothing():
    out = _run_tool("no_such_skill_xyz")
    assert out.get("error") is True
    assert capability_used_event(_display("get_skill_details", out["message"])) is None


def test_other_tools_and_error_chunks_emit_nothing():
    text = "[Skill: Code Review]\n\nbody"
    assert capability_used_event(_display("file_read", text)) is None
    assert capability_used_event(_display("get_skill_details_v2", text)) is None
    assert capability_used_event(_display("get_skill_details", text, is_error=True)) is None
    assert capability_used_event({"type": "tool_start", "tool_name": "get_skill_details"}) is None
    assert capability_used_event("not a dict") is None


def test_content_field_is_a_fallback_for_result():
    chunk = {"type": "tool_display", "tool_name": "get_skill_details",
             "content": "[Skill: Code Review]\n\nbody"}
    assert capability_used_event(chunk)["key"] == "skill:Code Review"


def test_stream_chunks_relays_the_event_in_both_loops():
    """Source guard: stream_chunks drops unregistered chunk types, so the
    event is built and yielded there -- in the main loop AND the diff-
    validation retry loop, which re-streams tool calls too."""
    from app import server
    src = inspect.getsource(server.stream_chunks)
    assert src.count("capability_used_event(chunk)") == 1
    assert src.count("capability_used_event(retry_chunk)") == 1
