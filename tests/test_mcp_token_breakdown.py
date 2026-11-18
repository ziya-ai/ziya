"""app/utils/mcp_token_breakdown.py: the MCP status heading's token split.

Reported 2026-11-14: the heading counted every enabled tool of every
connected server as in context, so a within-reach server showed its full
schema weight (only its catalog line is sent) and a shelved server showed
weight for tools that are never sent.

The filter fixtures are built by ``mcp_ondemand.mcp_filter`` -- the function
the request path uses -- so a change to how a placement is classified there
moves these numbers too.
"""
from pathlib import Path

import pytest

from app.utils import mcp_token_breakdown as tb
from app.utils.mcp_ondemand import ALL, mcp_filter


def _srv(tools, enabled=True, disabled_tokens=0):
    total = sum(tools.values()) + disabled_tokens
    return {"enabled": enabled, "total": total, "enabled_tokens": sum(tools.values()), "tools": tools}


SERVERS = {
    "hand": _srv({"h1": 600, "h2": 400}),
    "builtin": _srv({"b1": 200}),                         # not placeable: no placement
    "reach_idle": _srv({"r1": 3000, "r2": 2000}),
    "reach_all": _srv({"a1": 700}),
    "reach_some": _srv({"s1": 500, "s2": 1500, "s3": 1000}),
    "shelf": _srv({"x1": 4000}),
    "pruned": _srv({"p1": 300}, disabled_tokens=900),     # on hand, one tool disabled
    "off_in_config": _srv({"o1": 800}, enabled=False),
}
PLACEMENTS = {"hand": "always", "pruned": "always", "reach_idle": "ondemand",
              "reach_all": "ondemand", "reach_some": "ondemand", "shelf": "off"}
LOADED = {"reach_all": ALL, "reach_some": ["s1", "s3"]}


def _run(**kw):
    filt = mcp_filter(PLACEMENTS, LOADED)
    return tb.breakdown(SERVERS, filt, catalog_tokens=120, instruction_tokens=1000, **kw)


def test_only_on_hand_loaded_summaries_and_instructions_are_counted_as_sent():
    b = _run()
    assert b["on_hand"] == {"tokens": 1000 + 200 + 300, "servers": 3, "tools": 4}
    # Fully loaded server plus the two named tools of the partly loaded one.
    assert b["loaded"] == {"tokens": 700 + 500 + 1000, "servers": 2, "tools": 3}
    assert b["reach_summaries"] == {"tokens": 120, "servers": 3}
    assert b["sent_total"] == 1500 + 2200 + 120 + 1000
    assert b["not_sent"] == {"reach_unloaded": 5000 + 1500, "shelved": 4000,
                             "disabled": 900 + 800}
    assert b["deferral"] is False and b["deferred"]["servers"] == 0


def test_every_token_lands_in_exactly_one_bucket():
    """Nothing double-counted or dropped: the buckets sum to the installed total."""
    for deferral in (False, True):
        b = _run(deferral=deferral)
        placed = (b["on_hand"]["tokens"] + b["loaded"]["tokens"] + b["deferred"]["tokens"]
                  + sum(b["not_sent"].values()))
        assert placed == sum(s["total"] for s in SERVERS.values()), deferral


def test_the_old_heading_overstated_what_is_sent():
    """Direction: the figure the heading used to show (all enabled tools)
    is larger than what the breakdown counts as sent for tools."""
    old = sum(s["enabled_tokens"] for s in SERVERS.values() if s["enabled"])
    b = _run()
    assert b["on_hand"]["tokens"] + b["loaded"]["tokens"] < old


def test_deferral_reports_within_reach_as_deferred_and_outside_the_total():
    b = _run(deferral=True)
    # The loaded record is not consulted on the native path.
    assert b["loaded"] == {"tokens": 0, "servers": 0, "tools": 0}
    assert b["deferred"] == {"tokens": 5000 + 700 + 3000, "servers": 3, "tools": 6}
    assert b["not_sent"]["reach_unloaded"] == 0
    assert b["sent_total"] == 1500 + 120 + 1000
    # Shelved servers are dropped on that path too.
    assert b["not_sent"]["shelved"] == 4000


def test_per_server_rows_say_where_each_server_sits_and_what_it_sends():
    """The server rows used to show every server's full weight as active."""
    p = _run()["per_server"]
    assert p["hand"] == {"placement": "on_hand", "sent": 1000, "held": 0}
    assert p["builtin"]["placement"] == "on_hand"
    assert p["reach_idle"] == {"placement": "reach", "sent": 0, "held": 5000}
    assert p["reach_all"] == {"placement": "loaded", "sent": 700, "held": 0}
    assert p["reach_some"] == {"placement": "partial", "sent": 1500, "held": 1500}
    assert p["shelf"] == {"placement": "shelved", "sent": 0, "held": 4000}
    assert p["off_in_config"] == {"placement": "disabled", "sent": 0, "held": 800}
    assert set(p) == set(SERVERS)
    # The rows and the heading agree.
    b = _run()
    assert sum(r["sent"] for r in p.values()) == b["on_hand"]["tokens"] + b["loaded"]["tokens"]
    d = _run(deferral=True)["per_server"]
    assert {d[n]["placement"] for n in ("reach_idle", "reach_all", "reach_some")} == {"deferred"}
    assert d["reach_all"] == {"placement": "deferred", "sent": 0, "held": 700}


def test_no_filter_means_everything_enabled_is_on_hand():
    b = tb.breakdown(SERVERS, None, catalog_tokens=999, instruction_tokens=0)
    assert b["on_hand"]["servers"] == 7
    assert b["reach_summaries"] == {"tokens": 0, "servers": 0}   # no catalog without reach
    assert b["not_sent"] == {"reach_unloaded": 0, "shelved": 0, "disabled": 900 + 800}


# -- the seam to the request path -------------------------------------------

def test_for_request_uses_the_request_paths_filter_and_catalog(monkeypatch):
    from app.utils import bench_prompt, mcp_ondemand
    seen = {}

    def fake_filter(root, cid, **kw):
        seen["filter"] = (root, cid)
        return mcp_filter(PLACEMENTS, LOADED)

    def fake_catalog(root, cid, native=False):
        seen["catalog"] = (root, cid, native)
        return "x" * 40

    monkeypatch.setattr(bench_prompt, "project_id_for_root", lambda root: "p1")
    monkeypatch.setattr(bench_prompt, "mcp_filter_for_root", fake_filter)
    monkeypatch.setattr(mcp_ondemand, "catalog_section_for_root", fake_catalog)
    b = tb.breakdown_for_request(SERVERS, "/proj", "c1", count_text=len,
                                 instruction_tokens=10, deferral=False)
    assert seen == {"filter": ("/proj", "c1"), "catalog": ("/proj", "c1", False)}
    assert b["scope"] == "conversation" and b["conversation_id"] == "c1"
    assert b["reach_summaries"]["tokens"] == 40
    assert b["loaded"]["servers"] == 2
    # No conversation: project placements, nothing loaded.
    b = tb.breakdown_for_request(SERVERS, "/proj", None, count_text=len, deferral=True)
    assert b["scope"] == "project" and b["conversation_id"] is None
    assert seen["catalog"] == ("/proj", None, True)       # native wording is what gets counted


def test_for_request_degrades_to_all_on_hand_and_never_raises(monkeypatch):
    from app.utils import bench_prompt
    monkeypatch.setattr(bench_prompt, "project_id_for_root", lambda root: None)
    b = tb.breakdown_for_request(SERVERS, None, "c1", count_text=len, deferral=False)
    assert b["scope"] == "none" and b["conversation_id"] is None
    assert b["on_hand"]["servers"] == 7

    def boom(*a, **k):
        raise RuntimeError("bench down")
    monkeypatch.setattr(bench_prompt, "project_id_for_root", lambda root: "p1")
    monkeypatch.setattr(bench_prompt, "mcp_filter_for_root", boom)
    b = tb.breakdown_for_request(SERVERS, "/proj", "c1", count_text=len, deferral=False)
    assert b["scope"] == "none" and b["on_hand"]["servers"] == 7


@pytest.mark.parametrize("endpoint,cfg,model_id,want", [
    ("anthropic", {}, "claude-sonnet-4-6", True),
    ("anthropic", {}, "claude-opus-4-1", False),
    ("bedrock", {}, "anthropic.claude-sonnet-4-6", False),          # Invoke API: opt-in only
    ("bedrock", {"supports_tool_search": True}, {"us": "us.anthropic.claude-fable-5-2"}, True),
    ("bedrock", {"endpoint_override": "bedrock-mantle"}, {"us": "anthropic.claude-fable-5"}, True),
    ("openai", {}, "gpt-x", False),
])
def test_deferral_follows_the_prompt_builders_gate(monkeypatch, endpoint, cfg, model_id, want):
    from app.agents.models import ModelManager
    monkeypatch.setattr("app.config.env_registry.ziya_env", lambda k, *a: endpoint)
    monkeypatch.setattr(ModelManager, "get_model_alias", classmethod(lambda cls: "m"))
    monkeypatch.setattr(ModelManager, "get_model_config", classmethod(lambda cls, e, m=None: cfg))
    monkeypatch.setattr(ModelManager, "get_model_id", classmethod(lambda cls, mi=None: model_id))
    assert tb.deferral_for_current_model() is want


def test_deferral_is_false_when_model_state_is_unreadable(monkeypatch):
    from app.agents.models import ModelManager

    def boom(cls, *a, **k):
        raise ValueError("no such model")
    monkeypatch.setattr(ModelManager, "get_model_config", classmethod(boom))
    assert tb.deferral_for_current_model() is False


def test_status_route_returns_the_breakdown():
    """Source-level seam: the route passes the conversation and attaches the
    result.  Fails until the mcp_routes.py change is applied."""
    src = (Path(__file__).resolve().parents[1] / "app" / "routes" / "mcp_routes.py").read_text()
    assert "def get_mcp_status(conversation_id: Optional[str] = None)" in src
    assert "breakdown_for_request(" in src
    assert '"breakdown": token_breakdown' in src
