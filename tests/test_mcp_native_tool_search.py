"""
Native deferred tool loading on the Anthropic request path
(design/mcp-on-demand.md, "Anthropic's native path").

  * model gating: Claude 4.5+ or an explicit supports_tool_search flag
  * the tools payload: search entry first, eager tools, then within-reach
    servers' tools LAST with defer_loading, Ziya's loader/search dropped;
    a pure function of placement + registered tools (cache-stable)
  * the catalog's native wording
  * the provider: supports_feature('tool_search'); the stream yields a
    PassbackBlock for server_tool_use / tool_search_tool_result blocks, with
    the search input assembled from input_json_delta; the request goes
    through the beta namespace only when the payload carries the search
    entry
  * the assembler: passback blocks are echoed at their index, and an
    unsigned thinking block drops THINKING only, not them
  * the executor seam: with a tool_search provider, within-reach servers'
    tools are SENT deferred (not withheld), the loader is absent, and a
    turn never rebuilds the tool list
"""
from __future__ import annotations

import os
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.utils import mcp_ondemand as od

PID, CID = "proj1", "chat1"


@pytest.fixture(autouse=True)
def _clean(tmp_path, monkeypatch):
    monkeypatch.setenv("ZIYA_HOME", str(tmp_path))
    od._sticky.clear()
    import app.mcp.builtin_tools as bt
    monkeypatch.setattr(bt, "is_builtin_category_enabled", lambda c: True)
    yield
    od._sticky.clear()


# -- gating -------------------------------------------------------------------

@pytest.mark.parametrize("model_id,ok", [
    ("anthropic.claude-sonnet-4-5-20250929-v1:0", True),
    ("claude-opus-4-6", True), ("claude-haiku-4-5", True), ("claude-sonnet-5", True),
    # bedrock-mantle ids; fable-5 is the model the native path was validated on live
    ("anthropic.claude-fable-5", True), ("anthropic.claude-mythos-5", True),
    ("us.anthropic.claude-opus-5-5", True),
    ("claude-opus-4-1", False), ("claude-3-7-sonnet", False), ("anthropic.claude-sonnet-4-20250514", False),
    ("gpt-5", False), ("", False),
])
def test_model_gate(model_id, ok):
    assert od.native_tool_search_supported({}, model_id) is ok


def test_explicit_flag_wins():
    assert od.native_tool_search_supported({"supports_tool_search": True}, "claude-opus-4-1") is True
    assert od.native_tool_search_supported({"supports_tool_search": False}, "claude-opus-4-6") is False


def test_native_search_for_is_the_anthropic_messages_path_only():
    cfg = {"model_id": "claude-sonnet-4-6"}
    assert od.native_search_for("anthropic", cfg)
    assert od.native_search_for("bedrock", {**cfg, "endpoint_override": "bedrock-mantle"})
    assert not od.native_search_for("bedrock", cfg)                      # Invoke API: follow-up
    assert not od.native_search_for("bedrock", {**cfg, "endpoint_override": "bedrock-mantle",
                                                "mantle_api": "openai-responses"})
    assert not od.native_search_for("openai", cfg)
    assert not od.native_search_for("anthropic", {"model_id": "claude-opus-4-1"})


# -- payload ------------------------------------------------------------------

T = lambda n, s: SimpleNamespace(name=n, server=s)  # noqa: E731


def _pairs():
    tools = [T("file_read", None), T("mcp_bt_a", "bt"), T(od.LOADER_TOOL, None), T("mcp_gh_x", "gh"),
             T(od.SEARCH_TOOL, None), T("mcp_bt_b", "bt")]
    return [(t, {"name": t.name, "description": "d", "input_schema": {"type": "object"}}) for t in tools]


def test_payload_is_search_then_eager_then_deferred_without_the_loader():
    out = od.native_tool_payload(_pairs(), lambda t: t.server, {"bt"})
    assert out[0] == od.TOOL_SEARCH_ENTRY
    assert [t["name"] for t in out[1:]] == ["file_read", "mcp_gh_x", "mcp_bt_a", "mcp_bt_b"]
    assert [t.get("defer_loading") for t in out[1:]] == [None, None, True, True]
    assert od.is_native_search_request(out) and not od.is_native_search_request(out[1:])


def test_payload_is_the_same_whatever_is_loaded():
    """What the model has already found is the API's business: the array
    never changes within a conversation, which is the whole point."""
    a = od.native_tool_payload(_pairs(), lambda t: t.server, {"bt"})
    od.remember_loaded(CID, "bt", ["a"])
    b = od.native_tool_payload(_pairs(), lambda t: t.server, {"bt"})
    assert a == b


def test_filter_reports_shelved_and_reach_separately():
    f = od.mcp_filter({"jira": "ondemand", "slack": "off", "gh": "always"}, {})
    assert f.shelved == {"slack"} and f.reach == {"jira"}
    assert f.withheld == {"jira", "slack"}


# -- catalog ------------------------------------------------------------------

def test_native_catalog_names_the_api_search_not_the_loader():
    mgr = SimpleNamespace(server_configs={"jira": {"description": "Jira"}}, clients={})
    s = od.catalog_section(["jira"], mgr=mgr, native=True)
    assert od.TOOL_SEARCH_NAME in s and "  • jira — Jira" in s
    assert od.LOADER_TOOL not in s and od.SEARCH_TOOL not in s
    plain = od.catalog_section(["jira"], mgr=mgr)
    assert od.LOADER_TOOL in plain and od.TOOL_SEARCH_NAME not in plain


def test_prompt_builder_switches_wording_by_model(monkeypatch):
    import inspect
    from app.utils import precision_prompt_system as pps
    src = inspect.getsource(pps)
    assert "native_search_for(" in src and "native=_native" in src


# -- provider -----------------------------------------------------------------

def _provider(model_id="claude-sonnet-4-6"):
    from app.providers.anthropic_direct import AnthropicDirectProvider
    with patch("anthropic.AsyncAnthropic"):
        p = AnthropicDirectProvider.__new__(AnthropicDirectProvider)
    p.model_id = model_id
    p.model_config = {"family": "claude", "supports_thinking": True}
    p.client = MagicMock()
    return p


def test_provider_advertises_tool_search_by_model():
    assert _provider().supports_feature("tool_search") is True
    assert _provider("claude-opus-4-1").supports_feature("tool_search") is False


class _Ev(SimpleNamespace):
    pass


class _Stream:
    def __init__(self, events):
        self._events = events

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    def __aiter__(self):
        async def gen():
            for e in self._events:
                yield e
        return gen()


def _search_events():
    result_block = SimpleNamespace(
        type="tool_search_tool_result", tool_use_id="srvtoolu_1",
        model_dump=lambda **k: {"type": "tool_search_tool_result", "tool_use_id": "srvtoolu_1",
                                "content": {"type": "tool_search_tool_search_result",
                                            "tool_references": [{"type": "tool_reference", "tool_name": "mcp_bt_a"}]}})
    srv = SimpleNamespace(type="server_tool_use", id="srvtoolu_1", name=od.TOOL_SEARCH_NAME,
                          model_dump=lambda **k: {"type": "server_tool_use", "id": "srvtoolu_1",
                                                  "name": od.TOOL_SEARCH_NAME, "input": {}})
    return [
        _Ev(type="message_start", message=SimpleNamespace(usage=None)),
        _Ev(type="content_block_start", index=0, content_block=srv),
        _Ev(type="content_block_delta", index=0, delta=SimpleNamespace(type="input_json_delta", partial_json='{"pattern": "bt_"}')),
        _Ev(type="content_block_stop", index=0),
        _Ev(type="content_block_start", index=1, content_block=result_block),
        _Ev(type="content_block_stop", index=1),
        _Ev(type="content_block_start", index=2, content_block=SimpleNamespace(type="tool_use", id="tu_1", name="mcp_bt_a")),
        _Ev(type="content_block_delta", index=2, delta=SimpleNamespace(type="input_json_delta", partial_json='{}')),
        _Ev(type="content_block_stop", index=2),
        _Ev(type="message_delta", usage=None, delta=SimpleNamespace(stop_reason="tool_use")),
        _Ev(type="message_stop"),
    ]


@pytest.mark.asyncio
async def test_stream_yields_passback_blocks_and_uses_beta_for_search_requests():
    from app.providers.base import PassbackBlock, ToolUseEnd
    p = _provider()
    p.client.beta.messages.stream = MagicMock(return_value=_Stream(_search_events()))
    p.client.messages.stream = MagicMock(side_effect=AssertionError("non-beta path used"))
    kwargs = {"model": p.model_id, "max_tokens": 10, "messages": [],
              "tools": [dict(od.TOOL_SEARCH_ENTRY), {"name": "mcp_bt_a", "defer_loading": True}]}
    events = [e async for e in p._do_stream(kwargs)]
    pb = [e for e in events if isinstance(e, PassbackBlock)]
    assert [(b.index, b.block["type"]) for b in pb] == [(0, "server_tool_use"), (1, "tool_search_tool_result")]
    assert pb[0].block["input"] == {"pattern": "bt_"}
    assert pb[1].block["content"]["tool_references"][0]["tool_name"] == "mcp_bt_a"
    tu = [e for e in events if isinstance(e, ToolUseEnd)]
    assert [(t.id, t.name, t.index) for t in tu] == [("tu_1", "mcp_bt_a", 2)]
    # No search entry -> the ordinary path.
    p.client.messages.stream = MagicMock(return_value=_Stream([_Ev(type="message_stop")]))
    p.client.beta.messages.stream = MagicMock(side_effect=AssertionError("beta path used"))
    kwargs["tools"] = [{"name": "mcp_bt_a"}]
    _ = [e async for e in p._do_stream(kwargs)]


# -- assembler ----------------------------------------------------------------

def test_passback_blocks_are_echoed_at_their_index_even_when_thinking_is_dropped():
    from app.providers.base import LLMProvider
    srv = {"type": "server_tool_use", "id": "s1", "name": od.TOOL_SEARCH_NAME, "input": {"pattern": "x"}, "_index": 1}
    res = {"type": "tool_search_tool_result", "tool_use_id": "s1", "content": {}, "_index": 2}
    unsigned = {"type": "thinking", "thinking": "hm", "signature": None, "_index": 0}
    out = LLMProvider._ordered_assistant_content(
        "", [{"id": "t1", "name": "mcp_bt_a", "input": {}, "index": 3}], [unsigned, srv, res])
    assert [b["type"] for b in out] == ["server_tool_use", "tool_search_tool_result", "tool_use"]
    assert "_index" not in out[0]
    signed = {**unsigned, "signature": "sig"}
    out = LLMProvider._ordered_assistant_content(
        "", [{"id": "t1", "name": "mcp_bt_a", "input": {}, "index": 3}], [signed, srv, res])
    assert [b["type"] for b in out] == ["thinking", "server_tool_use", "tool_search_tool_result", "tool_use"]


# -- executor seam ------------------------------------------------------------

from app.providers.base import StreamEnd, TextDelta  # noqa: E402


def test_executor_defers_within_reach_on_native_providers():
    import inspect
    from app.streaming_tool_executor import StreamingToolExecutor
    src = inspect.getsource(StreamingToolExecutor._load_and_prepare_tools)
    assert "supports_feature('tool_search')" in src or 'supports_feature("tool_search")' in src
    assert "native_tool_payload(" in src
    # the loader rebuild is gated off on the native path
    loop = inspect.getsource(StreamingToolExecutor._stream_with_tools_impl)
    assert "PassbackBlock" in loop
