"""
The thinking-channel fabrication prohibition in the MCP guidelines.

Why this test exists
--------------------
Two runtime defenses catch fabricated tool output, and BOTH are structurally
blind to the reasoning/thinking channel:

  - ``StreamingToolExecutor._sanitize_assistant_text`` scans ``assistant_text``,
    and thinking deliberately never enters that variable (see the executor's
    ``thinking_delta`` branch — keeping it out is what stopped the model
    re-reading its own chain-of-thought as billed input prose).
  - The frontend HALLUCINATION FAILSAFE in ``chatApi.ts`` is gated on
    ``contentToAdd``; the ``type === 'thinking'`` branch returns before ever
    reaching it.

So a tool result invented *inside* a thinking block passes both. It then
poisons the conclusions of that iteration, and when the real call is finally
made and returns something different, the model attributes the mismatch to a
broken/caching tool layer rather than to its own invention.

No runtime check can catch this — the fabrication is indistinguishable from
legitimate reasoning at the token level. The prompt is the ONLY control point,
which is what makes the clause load-bearing rather than advisory, and worth a
test that fails if it is ever dropped.
"""

import pytest
from unittest.mock import MagicMock, patch


# Substrings asserted individually rather than as one exact block, so
# rewording the prose does not fail the test while DELETING a load-bearing
# instruction does. Each entry is a distinct claim the model must receive.
REQUIRED_CLAUSES = [
    # The mechanical fact. Without this the rest reads as style advice.
    "CANNOT be made from inside a reasoning/thinking block",
    # The prohibition itself.
    "describe a tool as running, retrying, or having returned something",
    # The specific misattribution observed live ("the shell tool keeps
    # reproducing cached output" / "the tool layer seems to be stuck").
    "conclude the tool layer is broken, cached, looping",
    # The corrective action. A prohibition with no alternative is ignorable.
    "STOP reasoning and emit the tool call",
]


def _mock_tool(name):
    t = MagicMock()
    t.name = name
    return t


def _run_guidelines(tool_names, native_function_calling=True):
    """Invoke mcp_usage_guidelines with MCP enabled and the given tools."""
    from app.extensions.prompt_extensions import mcp_prompt_extensions as mod

    mgr = MagicMock()
    mgr.is_initialized = True
    mgr.get_all_tools.return_value = [_mock_tool(n) for n in tool_names]

    def fake_env(key, *a, **kw):
        # MCP must read as enabled or the function short-circuits. Model name
        # must not be gemini-2.5-pro, which is skipped for prompt-size reasons.
        return {
            "ZIYA_ENABLE_MCP": "1",
            "ZIYA_ENDPOINT": "bedrock",
            "ZIYA_MODEL": "sonnet4.6",
        }.get(key, "")

    with patch.object(mod, "ziya_env", side_effect=fake_env), \
         patch("app.mcp.manager.get_mcp_manager", return_value=mgr), \
         patch("app.config.models_config.get_model_capabilities",
               return_value={"native_function_calling": native_function_calling}):
        return mod.mcp_usage_guidelines(
            "BASE PROMPT",
            {"endpoint": "bedrock", "model_name": "sonnet4.6",
             "model_id": "anthropic.claude-sonnet-4-6"},
        )


class TestThinkingChannelProhibitionPresent:
    """The clause must reach the model whenever tools exist."""

    @pytest.mark.parametrize("clause", REQUIRED_CLAUSES)
    def test_clause_present(self, clause):
        out = _run_guidelines(["run_shell_command", "file_read"])
        assert clause in out, f"missing load-bearing clause: {clause!r}"

    @pytest.mark.parametrize("native", [True, False])
    def test_survives_both_function_calling_modes(self, native):
        # The XML-examples block above this text is conditional on
        # native_function_calling. The prohibition must NOT be, or half of
        # all models lose it.
        out = _run_guidelines(["run_shell_command"], native_function_calling=native)
        for clause in REQUIRED_CLAUSES:
            assert clause in out, (
                f"clause {clause!r} lost when native_function_calling={native}"
            )

    def test_present_without_shell_tool(self):
        # The shell WRITE POLICY section is gated on a shell tool being
        # present; this prohibition is about fabrication generally and must
        # not be accidentally coupled to that gate.
        out = _run_guidelines(["file_read", "WorkspaceSearch"])
        for clause in REQUIRED_CLAUSES:
            assert clause in out


class TestProhibitionScopeIsExplicitlyWiderThanAnswerText:
    """
    The pre-existing prohibition is scoped to markdown code blocks in the
    ANSWER ("NEVER write a shell command in a markdown code block and then
    write fabricated output below it"). A model reading only that can honor
    it perfectly while fabricating freely in prose inside a thinking block.
    The new clause must state the wider scope, not merely repeat the old one.
    """

    def test_states_it_covers_reasoning_not_only_answer(self):
        out = _run_guidelines(["run_shell_command"])
        assert "APPLIES TO YOUR REASONING, NOT JUST YOUR ANSWER" in out

    def test_original_answer_scoped_prohibition_still_present(self):
        # Additive, not a replacement — the markdown-fence case is real and
        # separately observed.
        out = _run_guidelines(["run_shell_command"])
        assert "indistinguishable from lying" in out

    def test_explains_why_invented_results_outlive_the_reasoning(self):
        # The asymmetry is the whole reason this is expensive: thinking is
        # NOT round-tripped (bedrock.py drops signature_delta;
        # build_assistant_message emits text + tool_use only), but a
        # conclusion drawn from an invented result IS carried forward in
        # assistant_text. Without this sentence the instruction looks like
        # mere tidiness.
        out = _run_guidelines(["run_shell_command"])
        assert "your reasoning is not carried into" in out


class TestNoGuidelinesWithoutTools:
    """Guard the early-return paths, so the assertions above mean something."""

    def test_absent_when_no_tools_available(self):
        out = _run_guidelines([])
        assert out == "BASE PROMPT"
        for clause in REQUIRED_CLAUSES:
            assert clause not in out

    def test_absent_when_mcp_disabled(self):
        from app.extensions.prompt_extensions import mcp_prompt_extensions as mod
        with patch.object(mod, "ziya_env", return_value=""), \
             patch("app.config.models_config.get_model_capabilities",
                   return_value={"native_function_calling": True}):
            out = mod.mcp_usage_guidelines("BASE PROMPT", {})
        assert out == "BASE PROMPT"
