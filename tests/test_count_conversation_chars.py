"""
Regression tests for StreamingToolExecutor._count_conversation_chars.

This helper computes a best-effort character total of the conversation for
a single logger.debug line.  It was previously an inline generator that did
``for b in msg.get('content', [])`` — which raises
``'NoneType' object is not iterable`` when a message's ``content`` key is
PRESENT but ``None`` (dict.get only returns the default when the key is
absent).  A present-but-None content arises from empty/coalesced assistant
turns and malformed records.  The exception aborted the streaming iteration
even though the block only feeds a debug log.

These tests pin:
  - the present-but-None content case (the reported bug),
  - str content,
  - the three structured block types (text / tool_result / tool_use),
  - non-dict messages and non-dict blocks being skipped,
  - exact equivalence of the totals with the original branch semantics.

The method is a @staticmethod, so it is exercised without constructing a
StreamingToolExecutor (which would require a live provider).
"""

import json

from app.streaming_tool_executor import StreamingToolExecutor

count = StreamingToolExecutor._count_conversation_chars


def test_present_but_none_content_does_not_raise():
    # The reported bug: content key present, value None. Must not raise
    # and must contribute 0.
    conv = [{"role": "assistant", "content": None}]
    assert count(conv) == 0


def test_none_content_mixed_with_real_messages():
    conv = [
        {"role": "user", "content": "hello"},        # 5
        {"role": "assistant", "content": None},        # 0 (the bug case)
        {"role": "user", "content": "world!"},         # 6
    ]
    assert count(conv) == 11


def test_absent_content_key():
    # Key entirely absent — was already safe, but pin it.
    assert count([{"role": "user"}]) == 0


def test_string_content():
    assert count([{"role": "user", "content": "abcde"}]) == 5


def test_text_block():
    conv = [{"role": "assistant", "content": [
        {"type": "text", "text": "hello world"},  # 11
    ]}]
    assert count(conv) == 11


def test_tool_result_string_content():
    conv = [{"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": "t1", "content": "result!"},  # 7
    ]}]
    assert count(conv) == 7


def test_tool_result_non_string_content_contributes_zero():
    # Matches the original branch: tool_result with non-str content -> 0.
    conv = [{"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": "t1",
         "content": [{"type": "text", "text": "ignored"}]},
    ]}]
    assert count(conv) == 0


def test_tool_use_block_counts_serialized_input():
    inp = {"command": "ls -la", "flag": True}
    conv = [{"role": "assistant", "content": [
        {"type": "tool_use", "id": "t1", "name": "run", "input": inp},
    ]}]
    assert count(conv) == len(json.dumps(inp))


def test_unknown_block_type_contributes_zero():
    conv = [{"role": "user", "content": [
        {"type": "image", "source": {"data": "xxxx"}},
    ]}]
    assert count(conv) == 0


def test_non_dict_block_skipped():
    # A stray non-dict block in the content list must be skipped, not crash.
    conv = [{"role": "user", "content": ["bare string", {"type": "text", "text": "ok"}]}]
    assert count(conv) == 2  # only the text block's "ok"


def test_non_dict_message_skipped():
    conv = ["not a dict", {"role": "user", "content": "hi"}]
    assert count(conv) == 2


def test_empty_conversation():
    assert count([]) == 0


def test_mixed_blocks_sum():
    inp = {"x": 1}
    conv = [
        {"role": "user", "content": "ab"},  # 2
        {"role": "assistant", "content": [
            {"type": "text", "text": "cde"},               # 3
            {"type": "tool_use", "id": "1", "input": inp},  # len(json)
        ]},
        {"role": "user", "content": [
            {"type": "tool_result", "content": "fg"},       # 2
        ]},
    ]
    expected = 2 + 3 + len(json.dumps(inp)) + 2
    assert count(conv) == expected


def test_equivalence_with_original_branch_semantics():
    """Cross-check the helper against a direct re-implementation of the
    original inline branch logic (with the None guard applied) on a
    representative conversation, to lock in semantic equivalence."""
    conv = [
        {"role": "user", "content": "plain"},
        {"role": "assistant", "content": None},
        {"role": "assistant", "content": [
            {"type": "text", "text": "thinking out loud"},
            {"type": "tool_use", "id": "t", "input": {"a": [1, 2, 3]}},
            {"type": "tool_result", "content": "tool output here"},
            {"type": "tool_result", "content": ["not", "a", "string"]},
            "stray",
        ]},
        "bad message",
    ]

    def original_with_guard(conversation):
        return sum(
            len(msg.get('content', '')) if isinstance(msg.get('content'), str)
            else sum(
                len(b.get('text', '')) if b.get('type') == 'text'
                else len(b.get('content', '')) if b.get('type') == 'tool_result' and isinstance(b.get('content'), str)
                else len(json.dumps(b.get('input', {}))) if b.get('type') == 'tool_use'
                else 0
                for b in (msg.get('content') or [])
                if isinstance(b, dict)
            )
            for msg in conversation
            if isinstance(msg, dict)
        )

    assert count(conv) == original_with_guard(conv)
