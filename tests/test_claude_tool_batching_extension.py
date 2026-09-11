"""Tests for the tool-batching guidance in the global MCP prompt extension.

The executor (streaming_tool_executor) has accepted any number of tool_use
blocks per turn for a long time, for every provider; the only thing that
pushed models toward one-call-per-turn was the sentinel-era "STOP IMMEDIATELY
/ WAIT" block in the Claude family extension.  The replacement rules describe
the executor's contract, not a Claude quirk, so they live in the global
``mcp_usage_guidelines`` extension and reach every family with tools loaded.

These tests pin:

- the block renders in the GLOBAL guidelines for a non-Claude model,
- the pre-batch introduction is retained (load-bearing for UX: the user must
  know what is about to happen) and scoped to the whole batch,
- independent calls are to be batched in one turn; serialize only on a
  true argument-depends-on-result dependency,
- no text may follow the tool blocks,
- a failed call does not invalidate its siblings,
- the Claude family extension no longer carries its own copy (no
  duplication, no drift between the two layers), and
- the sentinel-era stop/wait rules are gone everywhere.
"""

from unittest.mock import Mock, patch

from app.extensions.prompt_extensions.claude_extensions import claude_family_extension


def _mock_manager(n_external: int = 2):
    manager = Mock()
    manager.is_initialized = True
    manager.server_configs = {"ext_server": {"command": ["echo"], "enabled": True}}
    manager.clients = {}
    tools = []
    for i in range(n_external):
        t = Mock()
        t.name = f"ext_tool_{i}"
        t.description = f"External tool {i}"
        t.inputSchema = {"type": "object", "properties": {}}
        t._server_name = "ext_server"
        tools.append(t)
    manager.get_all_tools.return_value = tools
    return manager


def _render_global(monkeypatch, endpoint="openai", model_name="gpt-5") -> str:
    """Render the global MCP guidelines for a NON-Claude model.

    The model choice is the point: the batching rules must reach every
    family, so the assertion seam is a family that never saw them before.
    """
    from app.extensions.prompt_extensions import mcp_prompt_extensions as ext

    monkeypatch.setenv("ZIYA_ENABLE_MCP", "true")
    caps = {"native_function_calling": True}
    with patch("app.mcp.manager.get_mcp_manager", return_value=_mock_manager()), \
         patch("app.config.models_config.get_model_capabilities", return_value=caps):
        return ext.mcp_usage_guidelines(
            "BASE PROMPT", {"endpoint": endpoint, "model_name": model_name}
        )


def _section(out: str) -> str:
    """The TOOL EXECUTION AND CONTINUATION block, up to the Usage Rules."""
    start = out.index("TOOL EXECUTION AND CONTINUATION:")
    end = out.index("**Usage Rules:**", start)
    return out[start:end]


class TestToolBatchingGuidanceIsGlobal:
    def test_block_present_for_non_claude_model(self, monkeypatch):
        out = _render_global(monkeypatch)
        assert "TOOL EXECUTION AND CONTINUATION:" in out
        # Placed ahead of the Usage Rules it governs.
        assert out.index("TOOL EXECUTION AND CONTINUATION:") < out.index("**Usage Rules:**")

    def test_introduction_retained_and_scoped_to_batch(self, monkeypatch):
        sec = _section(_render_global(monkeypatch))
        assert "Introduce what you're about to do" in sec
        assert "covers the whole batch" in sec
        # Introduction is the first numbered rule, ahead of batching.
        assert sec.index("Introduce what you're about to do") < sec.index("**BATCH**")

    def test_batch_and_serialize_rules_present(self, monkeypatch):
        sec = _section(_render_global(monkeypatch))
        assert "**BATCH**" in sec and "SAME turn" in sec
        assert "**SERIALIZE**" in sec
        assert "depend on a result you do not yet have" in sec

    def test_pre_emit_prohibition_present(self, monkeypatch):
        # The original 2024 failure mode, named as a prohibition.
        sec = _section(_render_global(monkeypatch))
        assert "pre-emit a call that assumes an earlier call in the same batch succeeded" in sec

    def test_no_text_after_tool_calls_retained(self, monkeypatch):
        sec = _section(_render_global(monkeypatch))
        assert "write any text after the tool calls" in sec
        assert "guess what the tool output will be" in sec

    def test_batch_survives_single_failure(self, monkeypatch):
        sec = _section(_render_global(monkeypatch))
        assert "failed call does not invalidate its siblings" in sec
        assert "re-issue only what failed" in sec


class TestClaudeExtensionNoLongerDuplicates:
    def test_block_removed_from_claude_family_extension(self):
        out = claude_family_extension("base prompt", {"config": {"enabled": True}})
        assert "TOOL EXECUTION AND CONTINUATION:" not in out
        assert "**BATCH**" not in out
        # Claude-specific sentinel cleanup stays where it was.
        assert "Use ONLY native tool calling" in out

    def test_sentinel_era_stop_wait_rules_removed_everywhere(self, monkeypatch):
        claude = claude_family_extension("base prompt", {"config": {"enabled": True}})
        glob = _render_global(monkeypatch)
        for out in (claude, glob):
            assert "STOP IMMEDIATELY after </TOOL_SENTINEL>" not in out
            assert "tool result to be provided" not in out
