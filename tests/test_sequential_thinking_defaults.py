"""
Tests for StreamingToolExecutor._normalize_sequential_thinking_args.

GLM (and other OpenAI-compatible models) routinely call the
sequential-thinking MCP tool with only `thought` + `nextThoughtNeeded`,
omitting the schema-required `thoughtNumber`/`totalThoughts`. Rather than
bounce the call through schema validation (burning a provider round-trip
per thinking step to restate the same thought), the executor auto-fills
those bookkeeping numbers. These tests pin that behavior and prove the
schema gate then passes.

The executor is built via object.__new__ to skip __init__ (which would
construct a real provider + client); the normalizer reads only an
instance counter via getattr.
"""

from app.streaming_tool_executor import (
    StreamingToolExecutor,
    validate_tool_args_against_schema,
)


def _ex():
    return object.__new__(StreamingToolExecutor)


_SCHEMA = {
    "properties": {
        "thought": {"type": "string"},
        "nextThoughtNeeded": {"type": "boolean"},
        "thoughtNumber": {"type": "integer"},
        "totalThoughts": {"type": "integer"},
    },
    "required": ["thought", "nextThoughtNeeded", "thoughtNumber", "totalThoughts"],
}


class TestSequentialThinkingDefaults:
    def test_fills_missing_numbers(self):
        ex = _ex()
        out = ex._normalize_sequential_thinking_args(
            "sequentialthinking", {"thought": "x", "nextThoughtNeeded": True})
        assert out["thoughtNumber"] == 1
        assert out["totalThoughts"] >= 1

    def test_monotonic_counter_across_calls(self):
        ex = _ex()
        a = ex._normalize_sequential_thinking_args("sequentialthinking", {"thought": "1"})
        b = ex._normalize_sequential_thinking_args("sequentialthinking", {"thought": "2"})
        c = ex._normalize_sequential_thinking_args("sequentialthinking", {"thought": "3"})
        assert [a["thoughtNumber"], b["thoughtNumber"], c["thoughtNumber"]] == [1, 2, 3]

    def test_preserves_model_supplied_numbers(self):
        ex = _ex()
        out = ex._normalize_sequential_thinking_args(
            "sequentialthinking",
            {"thought": "x", "thoughtNumber": 5, "totalThoughts": 10,
             "nextThoughtNeeded": True})
        assert out["thoughtNumber"] == 5
        assert out["totalThoughts"] == 10

    def test_fills_total_when_only_number_given(self):
        ex = _ex()
        out = ex._normalize_sequential_thinking_args(
            "sequentialthinking", {"thought": "x", "thoughtNumber": 7})
        assert out["thoughtNumber"] == 7
        # totalThoughts must never trail the provided thoughtNumber.
        assert out["totalThoughts"] >= 7

    def test_defaults_next_thought_needed_when_missing(self):
        ex = _ex()
        out = ex._normalize_sequential_thinking_args(
            "sequentialthinking", {"thought": "x"})
        assert out["nextThoughtNeeded"] is True

    def test_both_present_is_noop(self):
        ex = _ex()
        original = {"thought": "x", "thoughtNumber": 2, "totalThoughts": 4,
                    "nextThoughtNeeded": False}
        out = ex._normalize_sequential_thinking_args("sequentialthinking", original)
        # Untouched — including nextThoughtNeeded=False (not overridden).
        assert out == {"thought": "x", "thoughtNumber": 2, "totalThoughts": 4,
                       "nextThoughtNeeded": False}
        # Counter not advanced when no fill was needed.
        assert getattr(ex, "_seq_thought_counter", 0) == 0

    def test_other_tools_untouched(self):
        ex = _ex()
        out = ex._normalize_sequential_thinking_args("run_shell_command", {"command": "ls"})
        assert out == {"command": "ls"}

    def test_non_dict_passthrough(self):
        ex = _ex()
        assert ex._normalize_sequential_thinking_args("sequentialthinking", None) is None

    def test_validation_fails_before_then_passes_after(self):
        """The end-to-end contract: the schema gate rejects the bare call,
        and accepts it once normalized — so no turn is wasted."""
        ex = _ex()
        bare = {"thought": "reasoning step", "nextThoughtNeeded": True}
        # Before: missing required numbers → validation error.
        assert validate_tool_args_against_schema(
            "mcp_sequentialthinking", dict(bare), _SCHEMA) is not None
        # After: normalized call validates clean.
        norm = ex._normalize_sequential_thinking_args("sequentialthinking", dict(bare))
        assert validate_tool_args_against_schema(
            "mcp_sequentialthinking", norm, _SCHEMA) is None
