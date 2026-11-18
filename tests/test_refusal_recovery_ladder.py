"""Tests for the refusal-recovery ladder's payload-reduction primitives.

A provider refusal (stop_reason 'refusal') is deterministic for a given
payload but measured to be strongly payload-size dependent: the same
conversation that refuses with ~77k chars of injected file context passes
without it, and a benign system prompt of equal size refuses identically.
The recovery ladder therefore retries with a legitimately REDUCED payload:
rung 1 drops injected file context, rung 2 compacts older assistant turns.
Content is never perturbed or obfuscated; the ladder only ever sends less.

These tests pin rung 2's mutation, _compact_older_assistant_turns, which
runs against the live conversation list mid-stream and therefore must be
surgical: recent turns intact, non-text blocks untouched, accurate count.
"""
import pytest

from app.streaming_tool_executor import StreamingToolExecutor

compact = StreamingToolExecutor._compact_older_assistant_turns

MARKER = "[...earlier response truncated to reduce request size...]"


def _conv(*turns):
    """Build a conversation from (role, content) pairs."""
    return [{"role": r, "content": c} for r, c in turns]


def _big(n=5000):
    return "word " * (n // 5)


class TestCompactOlderAssistantTurns:

    def test_older_large_turn_is_truncated(self):
        conv = _conv(
            ("user", "q1"), ("assistant", _big()),
            ("user", "q2"), ("assistant", "recent a2"),
            ("user", "q3"), ("assistant", "recent a3"),
        )
        n = compact(conv)
        assert n == 1
        assert MARKER in conv[1]["content"]
        assert len(conv[1]["content"]) < 1400  # cap + marker

    def test_keep_last_turns_never_touched(self):
        conv = _conv(
            ("user", "q1"), ("assistant", _big()),
            ("user", "q2"), ("assistant", _big()),
            ("user", "q3"), ("assistant", _big()),
        )
        compact(conv, keep_last=2)
        assert MARKER in conv[1]["content"]          # oldest: compacted
        assert MARKER not in conv[3]["content"]      # last 2: intact
        assert MARKER not in conv[5]["content"]
        assert conv[3]["content"] == _big()
        assert conv[5]["content"] == _big()

    def test_small_turns_left_alone(self):
        conv = _conv(
            ("user", "q1"), ("assistant", "short"),
            ("user", "q2"), ("assistant", "also short"),
            ("user", "q3"), ("assistant", "recent"),
        )
        assert compact(conv) == 0
        assert conv[1]["content"] == "short"

    def test_returns_zero_when_too_few_assistant_turns(self):
        """A conversation with <= keep_last assistant turns has nothing
        older to compact -- the ladder must fall through to the banner
        rather than claim progress it didn't make."""
        conv = _conv(("user", "q1"), ("assistant", _big()),
                     ("user", "q2"), ("assistant", _big()))
        assert compact(conv, keep_last=2) == 0
        assert MARKER not in conv[1]["content"]

    def test_user_turns_never_touched(self):
        big_user = _big()
        conv = _conv(
            ("user", big_user), ("assistant", _big()),
            ("user", "q2"), ("assistant", "a2"),
            ("user", "q3"), ("assistant", "a3"),
        )
        compact(conv)
        assert conv[0]["content"] == big_user

    def test_list_content_text_blocks_compacted(self):
        conv = _conv(
            ("user", "q1"),
            ("assistant", [{"type": "text", "text": _big()},
                           {"type": "tool_use", "id": "t1", "name": "x",
                            "input": {}}]),
            ("user", "q2"), ("assistant", "a2"),
            ("user", "q3"), ("assistant", "a3"),
        )
        n = compact(conv)
        assert n == 1
        blocks = conv[1]["content"]
        assert MARKER in blocks[0]["text"]
        # Non-text blocks must pass through untouched.
        assert blocks[1] == {"type": "tool_use", "id": "t1", "name": "x",
                             "input": {}}

    def test_count_matches_mutations(self):
        conv = _conv(
            ("user", "q1"), ("assistant", _big()),
            ("user", "q2"), ("assistant", _big()),
            ("user", "q3"), ("assistant", "a3"),
            ("user", "q4"), ("assistant", "a4"),
        )
        n = compact(conv, keep_last=2)
        mutated = sum(1 for m in conv
                      if m["role"] == "assistant"
                      and isinstance(m["content"], str)
                      and MARKER in m["content"])
        assert n == mutated == 2

    def test_idempotent_second_pass_is_noop(self):
        """Rung 2 must not shrink turns it already compacted -- a repeat
        refusal falls through to the banner instead of looping."""
        conv = _conv(
            ("user", "q1"), ("assistant", _big()),
            ("user", "q2"), ("assistant", "a2"),
            ("user", "q3"), ("assistant", "a3"),
        )
        assert compact(conv) == 1
        snapshot = [dict(m) for m in conv]
        assert compact(conv) == 0
        assert conv == snapshot

    def test_empty_conversation(self):
        assert compact([]) == 0


class TestStripThinkingBlocks:
    """Both rungs rewrite what the echoed thinking blocks are signed against
    (rung 1 the system prompt, rung 2 the message prefix).  Observed
    2026-11-10 on claude-fable-5-2: rung 1 dropped 222k chars of file
    context and the retry 400'd with "Invalid signature in thinking block
    ... The system prompt differs from the one this block was created
    with", so the reduced payload was never evaluated and the turn ended.
    The ladder must drop the blocks before it retries."""

    @staticmethod
    def strip(conv):
        # Resolved at call time so, before the fix lands, only these tests
        # fail rather than the whole module failing to collect.
        return StreamingToolExecutor._strip_thinking_blocks(conv)

    def _chain(self):
        return _conv(
            ("user", "q"),
            ("assistant", [
                {"type": "thinking", "thinking": "plan", "signature": "S1"},
                {"type": "text", "text": "Looking."},
                {"type": "tool_use", "id": "t1", "name": "x", "input": {}}]),
            ("user", [{"type": "tool_result", "tool_use_id": "t1", "content": "r"}]),
            ("assistant", [
                {"type": "redacted_thinking", "data": "OPAQUE"},
                {"type": "tool_use", "id": "t2", "name": "x", "input": {}}]),
            ("user", [{"type": "tool_result", "tool_use_id": "t2", "content": "r"}]),
        )

    def test_removes_signed_and_redacted_blocks_from_every_assistant_turn(self):
        conv = self._chain()
        assert self.strip(conv) == 2
        types = [[b["type"] for b in m["content"]]
                 for m in conv if m["role"] == "assistant"]
        assert types == [["text", "tool_use"], ["tool_use"]]
        # Everything else is byte-identical.
        assert conv[1]["content"][1] == {"type": "tool_use", "id": "t1",
                                         "name": "x", "input": {}}

    def test_user_turns_and_string_assistant_turns_untouched(self):
        conv = self._chain()
        conv.append({"role": "assistant", "content": "plain reply"})
        snapshot_users = [dict(m) for m in conv if m["role"] == "user"]
        self.strip(conv)
        assert [dict(m) for m in conv if m["role"] == "user"] == snapshot_users
        assert conv[-1]["content"] == "plain reply"

    def test_non_thinking_passback_blocks_survive(self):
        """Tool-search passback blocks are not signed against the system
        prompt and are needed to resolve the tool_use that follows; only
        thinking is dropped."""
        conv = _conv(("assistant", [
            {"type": "thinking", "thinking": "p", "signature": "S"},
            {"type": "server_tool_use", "id": "s1", "name": "tool_search_tool_regex",
             "input": {"pattern": "x"}},
            {"type": "tool_search_tool_result", "tool_use_id": "s1", "content": []},
            {"type": "tool_use", "id": "t1", "name": "x", "input": {}}]))
        assert self.strip(conv) == 1
        assert [b["type"] for b in conv[0]["content"]] == [
            "server_tool_use", "tool_search_tool_result", "tool_use"]

    def test_thinking_only_turn_is_not_left_empty(self):
        """An empty content list is itself a 400; leave a text placeholder."""
        conv = _conv(("assistant", [
            {"type": "thinking", "thinking": "p", "signature": "S"}]))
        assert self.strip(conv) == 1
        assert len(conv[0]["content"]) == 1
        assert conv[0]["content"][0]["type"] == "text"
        assert conv[0]["content"][0]["text"].strip()

    def test_noop_returns_zero_and_leaves_conversation_equal(self):
        conv = _conv(("user", "q"), ("assistant", "a"),
                     ("assistant", [{"type": "text", "text": "t"}]))
        snapshot = [dict(m) for m in conv]
        assert self.strip(conv) == 0
        assert conv == snapshot
        assert self.strip([]) == 0

    def test_tolerates_malformed_entries(self):
        conv = [None, {"role": "assistant"}, {"role": "assistant", "content": None},
                {"role": "assistant", "content": ["not-a-dict",
                 {"type": "thinking", "thinking": "p", "signature": "S"}]}]
        assert self.strip(conv) == 1
        assert conv[3]["content"] == ["not-a-dict"]

    def test_compaction_alone_does_not_strip(self):
        """Pins the division of labour: compaction stays surgical (see
        test_thinking_passback::test_thinking_survives_older_turn_compaction)
        and the ladder calls the stripper separately."""
        conv = self._chain() + _conv(("assistant", "k1"), ("assistant", "k2"))
        compact(conv)
        assert conv[1]["content"][0]["type"] == "thinking"


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-v"]))
