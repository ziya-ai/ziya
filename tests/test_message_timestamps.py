"""
Tests for the hidden per-message timestamp feature.

Exercises the REAL code paths:
  1. server.build_messages_for_streaming -> _timestamp preserved through
     processed_chat_history (diff #3) and conv_start_ts derived from the
     first message.
  2. precision_prompt_system.build_messages -> hidden <MessageTime> tag per
     turn + one-time system directive (diff #4).

The hard dependencies (extract_codebase, get_extended_prompt,
precision_system.build_messages) are mocked so the real history loop /
_with_time_tag closure runs; the many optional system-prompt injectors are
wrapped in try/except in the source and degrade gracefully here.
"""
import sys
import os
import datetime
from unittest import mock

# Ensure the project root is on sys.path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest


# ---------------------------------------------------------------------------
# Helpers to drive precision_prompt_system.build_messages with mocked deps.
# ---------------------------------------------------------------------------
class _FakeMsg:
    def __init__(self, type_, content):
        self.type = type_
        self.content = content


class _FakePrompt:
    """Stands in for the LangChain prompt template build_messages formats."""
    def format_messages(self, codebase="", tools="", question=""):
        msgs = [_FakeMsg("system", "BASE_SYSTEM_PROMPT")]
        if question:
            msgs.append(_FakeMsg("human", question))
        return msgs


def _build(chat_history, question="current question"):
    from app.utils.precision_prompt_system import PrecisionPromptSystem
    sys_obj = PrecisionPromptSystem()
    with mock.patch(
        "app.agents.agent.extract_codebase", return_value=""
    ), mock.patch(
        "app.agents.prompts_manager.get_extended_prompt", return_value=_FakePrompt()
    ):
        return sys_obj.build_messages(
            request_path="/api/chat",
            model_info={"model_name": "sonnet4.0", "model_family": "claude",
                        "endpoint": "bedrock", "model_id": "x"},
            files=[],
            question=question,
            chat_history=chat_history,
            conversation_id="test-conv",
        )


def _iso(ms):
    return datetime.datetime.fromtimestamp(ms / 1000).strftime("%Y-%m-%d %H:%M:%S")


# ---------------------------------------------------------------------------
# precision_prompt_system: per-message tag + directive (diff #4)
# ---------------------------------------------------------------------------
def test_string_content_gets_hidden_time_tag():
    ts = 1700000000000  # epoch ms
    history = [{"type": "human", "content": "hello", "_timestamp": ts}]
    messages = _build(history)

    # The system directive explaining the tag must be present exactly once.
    system = messages[0]["content"]
    assert "## Message Timing" in system
    assert system.count("## Message Timing") == 1
    assert "never" in system.lower() and "echo" in system.lower()

    # The history turn must carry the hidden tag with the correct rendered time.
    hist = [m for m in messages if m["role"] == "user" and "hello" in str(m["content"])]
    assert hist, "history user turn not found"
    content = hist[0]["content"]
    assert content == f'<MessageTime value="{_iso(ts)}" />\nhello'


def test_no_timestamp_leaves_content_untouched():
    history = [{"type": "human", "content": "no ts here", "_timestamp": None}]
    messages = _build(history)
    hist = [m for m in messages if m["role"] == "user" and "no ts here" in str(m["content"])]
    assert hist
    assert hist[0]["content"] == "no ts here"  # no tag prefix


def test_multimodal_list_content_tags_first_text_block():
    ts = 1700000500000
    blocks = [
        {"type": "image", "source": {"data": "..."}},
        {"type": "text", "text": "describe this"},
    ]
    history = [{"type": "human", "content": blocks, "_timestamp": ts}]
    messages = _build(history)
    hist = [m for m in messages if m["role"] == "user" and isinstance(m["content"], list)]
    assert hist
    out = hist[0]["content"]
    # Image block untouched, text block prefixed.
    assert out[0]["type"] == "image"
    assert out[1]["text"] == f'<MessageTime value="{_iso(ts)}" />\ndescribe this'


def test_multimodal_list_without_text_block_prepends_tag_block():
    ts = 1700000900000
    blocks = [{"type": "image", "source": {"data": "..."}}]
    history = [{"type": "human", "content": blocks, "_timestamp": ts}]
    messages = _build(history)
    hist = [m for m in messages if m["role"] == "user" and isinstance(m["content"], list)]
    assert hist
    out = hist[0]["content"]
    assert out[0] == {"type": "text", "text": f'<MessageTime value="{_iso(ts)}" />\n'}
    assert out[1]["type"] == "image"


def test_directive_added_only_once_for_many_turns():
    ts = 1700000000000
    history = [
        {"type": "human", "content": "q1", "_timestamp": ts},
        {"type": "ai", "content": "a1", "_timestamp": ts + 1000},
        {"type": "human", "content": "q2", "_timestamp": ts + 5000},
        {"type": "ai", "content": "a2", "_timestamp": ts + 6000},
    ]
    messages = _build(history)
    assert messages[0]["content"].count("## Message Timing") == 1
    # All four turns tagged. Exclude the system message: its directive
    # legitimately contains the literal "<MessageTime ...>" example string.
    tagged = [m for m in messages
              if m["role"] != "system" and "<MessageTime" in str(m["content"])]
    assert len(tagged) == 4


def test_both_human_and_ai_turns_tagged():
    ts = 1700000000000
    history = [
        {"type": "human", "content": "q", "_timestamp": ts},
        {"type": "ai", "content": "a", "_timestamp": ts + 60000},
    ]
    messages = _build(history)
    user = [m for m in messages if m["role"] == "user" and "q" in str(m["content"])][0]
    asst = [m for m in messages if m["role"] == "assistant" and "a" in str(m["content"])][0]
    assert user["content"] == f'<MessageTime value="{_iso(ts)}" />\nq'
    assert asst["content"] == f'<MessageTime value="{_iso(ts + 60000)}" />\na'


def test_bad_timestamp_is_safe():
    # A non-numeric timestamp must not raise; content passes through untouched.
    history = [{"type": "human", "content": "weird", "_timestamp": "not-a-number"}]
    messages = _build(history)
    hist = [m for m in messages if m["role"] == "user" and "weird" in str(m["content"])]
    assert hist
    assert hist[0]["content"] == "weird"


# ---------------------------------------------------------------------------
# server.build_messages_for_streaming: diff #3 (_timestamp preservation)
# Drives the REAL function, capturing what it forwards to precision_system.
# ---------------------------------------------------------------------------
def _capture_processed_history(chat_history):
    import app.server as server
    captured = {}

    def _fake_build_messages(**kwargs):
        captured["chat_history"] = kwargs.get("chat_history")
        captured["conv_start_ts"] = kwargs.get("conv_start_ts")
        return [{"role": "system", "content": "x"}]

    with mock.patch(
        "app.utils.precision_prompt_system.precision_system.build_messages",
        side_effect=_fake_build_messages,
    ), mock.patch(
        "app.agents.prompts_manager.get_model_info_from_config",
        return_value={"model_name": "sonnet4.0", "model_family": "claude",
                      "endpoint": "bedrock", "model_id": "x"},
    ), mock.patch(
        "app.mcp.response_validator.sanitize_text", side_effect=lambda t: t,
    ):
        server.build_messages_for_streaming(
            question="now",
            chat_history=chat_history,
            files=[],
            conversation_id="c1",
        )
    return captured


def test_streaming_preserves_timestamp_in_dict_branch():
    ts = 1700000000000
    history = [
        {"type": "human", "content": "q1", "_timestamp": ts},
        {"type": "ai", "content": "a1", "_timestamp": ts + 90000},
    ]
    captured = _capture_processed_history(history)
    processed = captured["chat_history"]
    assert all("_timestamp" in m for m in processed)
    assert [m["_timestamp"] for m in processed] == [ts, ts + 90000]


def test_streaming_derives_conv_start_ts_from_first_message():
    ts = 1700000000000
    history = [
        {"type": "human", "content": "q1", "_timestamp": ts},
        {"type": "ai", "content": "a1", "_timestamp": ts + 90000},
    ]
    captured = _capture_processed_history(history)
    assert captured["conv_start_ts"] == ts


def test_streaming_handles_missing_timestamp():
    history = [{"type": "human", "content": "q1"}]  # no _timestamp key
    captured = _capture_processed_history(history)
    processed = captured["chat_history"]
    assert processed[0]["_timestamp"] is None
    assert captured["conv_start_ts"] is None


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
