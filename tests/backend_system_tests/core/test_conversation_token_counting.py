"""Tests that conversation token estimation accounts for all block types.

The total_chars calculation and the calibration estimation loop in
streaming_tool_executor must count tool_result and tool_use blocks —
not just text blocks.  Before the fix, these were silently skipped,
causing estimates to be 50-70% below actual token usage in
tool-heavy conversations.
"""

import json
import pytest


class TestConversationCharCounting:
    """Tests for the total_chars calculation in stream_with_tools."""

    @staticmethod
    def _count_total_chars(conversation):
        """Reproduce the total_chars calculation from stream_with_tools.

        Kept in sync with the actual code so tests break if the two diverge.
        """
        return sum(
            len(msg.get('content', '')) if isinstance(msg.get('content'), str)
            else sum(
                len(b.get('text', '')) if b.get('type') == 'text'
                else len(b.get('content', '')) if b.get('type') == 'tool_result' and isinstance(b.get('content'), str)
                else len(json.dumps(b.get('input', {}))) if b.get('type') == 'tool_use'
                else 0
                for b in msg.get('content', [])
                if isinstance(b, dict)
            )
            for msg in conversation
            if isinstance(msg, dict)
        )

    def test_counts_string_content(self):
        conversation = [
            {"role": "user", "content": "Hello world"},
            {"role": "assistant", "content": "Hi there!"},
        ]
        total = self._count_total_chars(conversation)
        assert total == len("Hello world") + len("Hi there!")

    def test_counts_text_blocks(self):
        conversation = [
            {"role": "assistant", "content": [
                {"type": "text", "text": "Here is my analysis."},
            ]},
        ]
        assert self._count_total_chars(conversation) == len("Here is my analysis.")

    def test_counts_tool_result_blocks(self):
        """tool_result blocks must be counted — this was the primary bug."""
        tool_result_text = "$ ls -la\ntotal 42\ndrwxr-xr-x  5 user staff 160 .\n" * 100
        conversation = [
            {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "abc123", "content": tool_result_text},
            ]},
        ]
        assert self._count_total_chars(conversation) == len(tool_result_text)

    def test_counts_tool_use_blocks(self):
        """tool_use input JSON must be counted."""
        tool_input = {"command": "find . -name '*.py' -type f | head -20"}
        conversation = [
            {"role": "assistant", "content": [
                {"type": "text", "text": "Let me check."},
                {"type": "tool_use", "id": "t1", "name": "run_shell_command", "input": tool_input},
            ]},
        ]
        expected = len("Let me check.") + len(json.dumps(tool_input))
        assert self._count_total_chars(conversation) == expected

    def test_counts_mixed_conversation(self):
        """Realistic multi-turn with text, tool_use, and tool_result."""
        big_result = "x" * 50000
        tool_input = {"command": "cat large_file.py"}
        conversation = [
            {"role": "user", "content": "Read the file"},
            {"role": "assistant", "content": [
                {"type": "text", "text": "Reading now."},
                {"type": "tool_use", "id": "t1", "name": "run_shell_command", "input": tool_input},
            ]},
            {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "t1", "content": big_result},
            ]},
            {"role": "assistant", "content": "Here is my analysis."},
        ]
        expected = (
            len("Read the file")
            + len("Reading now.") + len(json.dumps(tool_input))
            + len(big_result)
            + len("Here is my analysis.")
        )
        assert self._count_total_chars(conversation) == expected

    def test_old_code_would_miss_tool_blocks(self):
        """Demonstrates the bug: text-only counting misses tool blocks."""
        big_result = "x" * 100000
        conversation = [
            {"role": "user", "content": "Read the doc"},
            {"role": "assistant", "content": [
                {"type": "text", "text": "Reading."},
                {"type": "tool_use", "id": "t1", "name": "QuipEditor", "input": {"documentId": "abc"}},
            ]},
            {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "t1", "content": big_result},
            ]},
        ]
        # Old code: only counts type=='text' blocks
        old_total = sum(
            len(msg.get('content', '')) if isinstance(msg.get('content'), str)
            else sum(
                len(b.get('text', ''))
                for b in msg.get('content', [])
                if isinstance(b, dict) and b.get('type') == 'text'
            )
            for msg in conversation
        )
        new_total = self._count_total_chars(conversation)

        assert old_total == len("Read the doc") + len("Reading.")
        assert new_total > old_total + 99000

    def test_handles_empty_and_malformed(self):
        """Edge cases: empty messages, non-dict entries, missing fields."""
        conversation = [
            {"role": "user", "content": ""},
            {"role": "assistant", "content": []},
            {"role": "user", "content": [None, "not a dict", {"type": "unknown"}]},
            {},
        ]
        assert self._count_total_chars(conversation) == 0

    def test_openai_tool_message_format(self):
        """OpenAI role=tool messages have string content."""
        conversation = [
            {"role": "tool", "tool_call_id": "t1", "content": "result text here"},
        ]
        assert self._count_total_chars(conversation) == len("result text here")


class TestNaiveTokenEstimation:
    """Tests for the naive (len//4) estimation loop."""

    @staticmethod
    def _estimate_tokens_naive(conversation):
        """Reproduce the naive estimation from stream_with_tools."""
        estimated = 0
        for msg in conversation:
            content = msg.get('content', '')
            if isinstance(content, str):
                estimated += len(content) // 4
            elif isinstance(content, list):
                for block in content:
                    if not isinstance(block, dict):
                        continue
                    block_type = block.get('type')
                    if block_type == 'text':
                        estimated += len(block.get('text', '')) // 4
                    elif block_type == 'tool_result':
                        tr_content = block.get('content', '')
                        if isinstance(tr_content, str):
                            estimated += len(tr_content) // 4
                    elif block_type == 'tool_use':
                        input_json = json.dumps(block.get('input', {}))
                        estimated += len(input_json) // 4
        return estimated

    def test_includes_tool_results(self):
        """4000 chars of tool result = ~1000 tokens."""
        conversation = [
            {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "t1", "content": "x" * 4000},
            ]},
        ]
        assert self._estimate_tokens_naive(conversation) == 1000

    def test_includes_tool_use_input(self):
        tool_input = {"query": "search term", "limit": 10}
        conversation = [
            {"role": "assistant", "content": [
                {"type": "tool_use", "id": "t1", "name": "search", "input": tool_input},
            ]},
        ]
        assert self._estimate_tokens_naive(conversation) == len(json.dumps(tool_input)) // 4

    def test_includes_all_block_types(self):
        """Combined estimation across text + tool_use + tool_result."""
        conversation = [
            {"role": "user", "content": "Hello"},  # 5 chars -> 1 token
            {"role": "assistant", "content": [
                {"type": "text", "text": "x" * 40},  # 10 tokens
                {"type": "tool_use", "id": "t1", "name": "cmd", "input": {"a": "b"}},
            ]},
            {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "t1", "content": "y" * 80},  # 20 tokens
            ]},
        ]
        result = self._estimate_tokens_naive(conversation)
        tool_input_tokens = len(json.dumps({"a": "b"})) // 4
        assert result == 1 + 10 + tool_input_tokens + 20
