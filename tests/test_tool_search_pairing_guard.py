"""A replayed search call without its result must not reach the API.

Reported 2026-11-16 (conversation 27b0c90a, fable5.2, Bedrock Invoke API):
the fourth request of a turn was refused with

    messages.25: `tool_search_tool_regex` tool use with id `srvtoolu_bdrk_…`
    was found without a corresponding `tool_search_tool_result` block

and the turn ended.  How the block lost its result is not established (see
the CHANGELOG entry); the executor seam is covered by
tests/test_tool_search_passback_executor_seam.py and keeps the pair.  This
file covers the guard that makes the request valid whatever produced the
shape, and the diagnostic that names it.

``scripts/probe_tool_search_pairing.py`` recorded what the API enforces: a
use needs a result with its id in the same message; adjacency is not
required; a cache marker on either block is accepted.
"""
import copy
import logging

import pytest

from app.utils import mcp_ondemand as od

SRV = {"type": "server_tool_use", "id": "srvtoolu_1", "name": od.TOOL_SEARCH_NAME,
       "input": {"pattern": "internal"}}
RES = {"type": "tool_search_tool_result", "tool_use_id": "srvtoolu_1",
       "content": {"type": "tool_search_tool_search_result", "tool_references": []}}
TEXT = {"type": "text", "text": "Looking."}
USE = {"type": "tool_use", "id": "toolu_1", "name": "mcp_file_read", "input": {"path": "a"}}


def _conv(a0):
    return [
        {"role": "user", "content": "go"},
        {"role": "assistant", "content": a0},
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "toolu_1", "content": "ok"}]},
    ]


# -- the pure helpers ---------------------------------------------------------

@pytest.mark.parametrize("a0", [
    [TEXT, SRV, RES, USE],
    [SRV, TEXT, RES, USE],              # not adjacent: accepted by the API
    [TEXT, USE],                         # no search at all
])
def test_paired_or_absent_searches_are_left_alone(a0):
    msgs = _conv(a0)
    before = copy.deepcopy(msgs)
    out, faults = od.drop_unpaired_searches(msgs)
    assert faults == []
    assert out is msgs and msgs == before


def test_unanswered_search_is_reported_and_removed_without_touching_the_input():
    msgs = _conv([TEXT, SRV, USE])
    before = copy.deepcopy(msgs)
    assert od.unpaired_searches(msgs) == [
        (1, "srvtoolu_1", ["text", "server_tool_use", "tool_use"])]
    out, faults = od.drop_unpaired_searches(msgs)
    assert [f[:2] for f in faults] == [(1, "srvtoolu_1")]
    assert [b["type"] for b in out[1]["content"]] == ["text", "tool_use"]
    assert msgs == before                       # the live conversation is not edited
    assert out[0] is msgs[0] and out[2] is msgs[2]
    assert od.unpaired_searches(out) == []


def test_only_the_unanswered_search_goes_when_a_message_holds_two():
    srv2 = {**SRV, "id": "srvtoolu_2"}
    out, faults = od.drop_unpaired_searches(_conv([SRV, RES, srv2, USE]))
    assert [f[1] for f in faults] == ["srvtoolu_2"]
    assert [b.get("id") or b.get("tool_use_id") for b in out[1]["content"]] == [
        "srvtoolu_1", "srvtoolu_1", "toolu_1"]


def test_a_result_in_another_message_does_not_answer_the_search():
    msgs = [{"role": "user", "content": "go"},
            {"role": "assistant", "content": [SRV]},
            {"role": "user", "content": "more"},
            {"role": "assistant", "content": [RES, TEXT]}]
    out, faults = od.drop_unpaired_searches(msgs)
    assert [f[:2] for f in faults] == [(1, "srvtoolu_1")]
    # an emptied message keeps a block: an empty content list is its own 400
    assert out[1]["content"] and out[1]["content"][0]["type"] == "text"


def test_string_content_and_user_messages_are_ignored():
    msgs = [{"role": "user", "content": [SRV]}, {"role": "assistant", "content": "plain"}]
    assert od.drop_unpaired_searches(msgs) == (msgs, [])


# -- the seam: the request body BedrockProvider builds ------------------------

def _bedrock_provider():
    from app.providers.bedrock import BedrockProvider
    p = BedrockProvider.__new__(BedrockProvider)
    p.model_id = "global.anthropic.claude-fable-5-2"
    p.model_config = {"family": "claude", "supports_thinking": True, "supports_tool_search": True}
    return p


def _body(messages):
    from app.providers.base import ProviderConfig
    tools = od.native_tool_payload(
        [(None, {"name": "mcp_file_read", "input_schema": {"type": "object"}})], lambda s: s, [])
    return _bedrock_provider()._build_request_body(
        messages, "sys", tools, ProviderConfig(temperature=None))


def _long(a0):
    """Eight messages, so the conversation cache marker is placed too."""
    tail = []
    for n in range(2, 4):
        tail += [{"role": "assistant", "content": [
                    {"type": "text", "text": f"Step {n}."},
                    {"type": "tool_use", "id": f"toolu_{n}", "name": "mcp_file_read", "input": {}}]},
                 {"role": "user", "content": [
                    {"type": "tool_result", "tool_use_id": f"toolu_{n}", "content": "ok"}]}]
    return _conv(a0) + tail + [{"role": "assistant", "content": [TEXT]},
                               {"role": "user", "content": "and now?"}]


def test_request_body_never_carries_an_unanswered_search():
    from unittest.mock import patch
    msgs = _long([TEXT, SRV, USE])
    before = copy.deepcopy(msgs)
    # The app logger does not propagate to pytest's caplog; read the calls.
    with patch("app.providers.bedrock.logger") as log:
        body = _body(msgs)
    warned = [c.args[0] % c.args[1:] for c in log.warning.call_args_list]
    sent = body["messages"]
    assert len(sent) == len(msgs)                           # control: nothing else was dropped
    assert od.unpaired_searches(sent) == []
    assert [b["type"] for b in sent[1]["content"]] == ["text", "tool_use"]
    assert msgs == before
    # the log names the message, the id and the layout, so the next
    # occurrence can be attributed from the server log alone
    line = next(w for w in warned if "TOOL_SEARCH_UNPAIRED" in w)
    assert "messages.1" in line and "srvtoolu_1" in line and "server_tool_use" in line
    # and MSG_STRUCT reports the same message and id
    struct = next(c.args[0] % c.args[1:] for c in log.info.call_args_list
                  if "MSG_STRUCT" in c.args[0])
    assert "unpaired_search=[(1, 'srvtoolu_1')]" in struct


def test_msg_struct_reports_no_unpaired_search_for_a_valid_request():
    from unittest.mock import patch
    with patch("app.providers.bedrock.logger") as log:
        _body(_long([TEXT, SRV, RES, USE]))
    struct = next(c.args[0] % c.args[1:] for c in log.info.call_args_list
                  if "MSG_STRUCT" in c.args[0])
    assert struct.endswith("unpaired_search=-")
    assert not any("TOOL_SEARCH_UNPAIRED" in c.args[0] for c in log.warning.call_args_list)


def test_request_body_keeps_a_paired_search_block_for_block():
    msgs = _long([TEXT, SRV, RES, USE])
    sent = _body(msgs)["messages"]
    kept = [{k: v for k, v in b.items() if k != "cache_control"} for b in sent[1]["content"]]
    assert kept == [TEXT, SRV, RES, USE]
