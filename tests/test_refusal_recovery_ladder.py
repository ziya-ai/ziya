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


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-v"]))
