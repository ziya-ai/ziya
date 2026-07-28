"""Tests for the task-scope tool allowlist: floor, matcher, enforcement.

Three layers are covered:

1. ``app/utils/task_tool_floor.py`` — the pure resolver: prefix-tolerant
   matching, the always-available floor, unrestricted passthrough.
2. ``StreamingToolExecutor._load_and_prepare_tools`` — that the
   allowlist is actually APPLIED where the tool list is built.  The
   regression this guards: task_executor filtered a list and passed it
   as ``tools=``, which the executor discarded, so every task ran with
   the full tool set while its prompt claimed a narrow one.
3. ``_format_tools_section`` — that the prompt describes the effective
   set (request + floor), so it can no longer contradict the payload.
"""

from unittest.mock import MagicMock, patch

import pytest

from app.utils.task_tool_floor import (
    ALWAYS_AVAILABLE_TOOLS,
    effective_tool_names,
    filter_tools_by_scope,
    normalize_tool_name,
    tool_is_allowed,
    unmatched_scope_tools,
)


def _tool(name: str):
    t = MagicMock()
    t.name = name
    return t


class _StubTool:
    """Minimal duck-typed tool for the executor path.

    MagicMock is unusable here: ``_convert_tool_schema`` JSON-dumps the
    resolved schema for its debug log, and a MagicMock attribute is not
    serializable.  A plain object with real strings and a real dict
    schema exercises the same code path honestly.
    """

    is_internal = False

    def __init__(self, name: str):
        self.name = name
        self.description = f"stub {name}"
        self.metadata = {"input_schema": {"type": "object", "properties": {}}}
        self.input_schema = {"type": "object", "properties": {}}


# ── Resolver ─────────────────────────────────────────────────────────

class TestNormalize:
    @pytest.mark.parametrize("raw,expected", [
        ("run_shell_command", "run_shell_command"),
        ("mcp_run_shell_command", "run_shell_command"),
        ("mcp_$mcp_run_shell_command", "run_shell_command"),
        ("", ""),
    ])
    def test_strips_prefixes(self, raw, expected):
        assert normalize_tool_name(raw) == expected


class TestEffectiveNames:
    def test_empty_means_unrestricted(self):
        # An empty set is the "no restriction" signal — NOT "only the
        # floor", which would narrow an unscoped task to five tools.
        assert effective_tool_names(None) == set()
        assert effective_tool_names([]) == set()

    def test_floor_is_unioned_in(self):
        eff = effective_tool_names(["file_read"])
        assert "file_read" in eff
        assert ALWAYS_AVAILABLE_TOOLS <= eff

    def test_floor_includes_artifact_pipeline(self):
        # The executor unconditionally instructs the model to emit
        # artifacts; omitting the tool would make that instruction a lie.
        assert "emit_artifact" in ALWAYS_AVAILABLE_TOOLS
        assert "render_diagram" in ALWAYS_AVAILABLE_TOOLS


class TestToolIsAllowed:
    def test_empty_allowlist_permits_everything(self):
        assert tool_is_allowed("anything", set())

    def test_unprefixed_scope_matches_prefixed_tool(self):
        # A card author writes `run_shell_command`; the registry holds
        # `mcp_run_shell_command`.  A bare `in` test would drop it.
        allowed = effective_tool_names(["run_shell_command"])
        assert tool_is_allowed("mcp_run_shell_command", allowed)

    def test_prefixed_scope_matches_unprefixed_tool(self):
        allowed = effective_tool_names(["mcp_file_read"])
        assert tool_is_allowed("file_read", allowed)

    def test_unrelated_tool_is_denied(self):
        allowed = effective_tool_names(["file_read"])
        assert not tool_is_allowed("mcp_run_shell_command", allowed)


class TestFilterTools:
    def test_unrestricted_returns_input_unchanged(self):
        tools = [_tool("a"), _tool("b")]
        assert filter_tools_by_scope(tools, None) is tools

    def test_narrows_to_request_plus_floor(self):
        tools = [
            _tool("file_read"),
            _tool("mcp_run_shell_command"),
            _tool("emit_artifact"),
            _tool("render_diagram"),
        ]
        kept = {t.name for t in filter_tools_by_scope(tools, ["file_read"])}
        assert kept == {"file_read", "emit_artifact", "render_diagram"}
        assert "mcp_run_shell_command" not in kept

    def test_shell_grant_survives_prefix_mismatch(self):
        tools = [_tool("mcp_run_shell_command"), _tool("mcp_other")]
        kept = {t.name for t in
                filter_tools_by_scope(tools, ["run_shell_command"])}
        assert "mcp_run_shell_command" in kept
        assert "mcp_other" not in kept


class TestUnmatchedReporting:
    def test_typo_is_reported(self):
        tools = [_tool("file_read")]
        assert unmatched_scope_tools(tools, ["file_raed"]) == ["file_raed"]

    def test_prefix_variant_is_not_reported(self):
        tools = [_tool("mcp_run_shell_command")]
        assert unmatched_scope_tools(tools, ["run_shell_command"]) == []

    def test_absent_floor_tool_is_not_blamed_on_author(self):
        # The author didn't request render_diagram; if the diagram
        # category is disabled that's not their error to see.
        tools = [_tool("file_read")]
        assert unmatched_scope_tools(tools, ["file_read"]) == []


# ── Enforcement in the streaming executor ────────────────────────────

class TestLoadAndPrepareToolsEnforcement:
    """The allowlist must bite where the tool list is BUILT."""

    def _executor(self):
        from app.streaming_tool_executor import StreamingToolExecutor
        # Bypass __init__ entirely — it builds providers/boto clients that
        # are irrelevant to tool-list assembly.
        return StreamingToolExecutor.__new__(StreamingToolExecutor)

    @pytest.mark.asyncio
    async def test_allowlist_narrows_registered_tools(self):
        registered = [
            _StubTool("file_read"),
            _StubTool("mcp_run_shell_command"),
            _StubTool("emit_artifact"),
        ]
        mgr = MagicMock()
        mgr.is_initialized = True
        with patch("app.mcp.manager.get_mcp_manager", return_value=mgr), \
             patch("app.mcp.enhanced_tools.create_secure_mcp_tools",
                   return_value=registered):
            all_tools, bedrock_tools, *_ = await self._executor()._load_and_prepare_tools(
                tool_allowlist=["file_read"],
            )
        names = {t.name for t in all_tools}
        assert names == {"file_read", "emit_artifact"}
        # The provider payload must agree — a filtered tool leaking back
        # into bedrock_tools would re-expose it to the model.
        payload_names = {t["name"] for t in bedrock_tools}
        assert not any("run_shell_command" in n for n in payload_names)

    @pytest.mark.asyncio
    async def test_no_allowlist_exposes_everything(self):
        registered = [_StubTool("file_read"), _StubTool("mcp_run_shell_command")]
        mgr = MagicMock()
        mgr.is_initialized = True
        with patch("app.mcp.manager.get_mcp_manager", return_value=mgr), \
             patch("app.mcp.enhanced_tools.create_secure_mcp_tools",
                   return_value=registered):
            all_tools, *_ = await self._executor()._load_and_prepare_tools()
        assert {t.name for t in all_tools} == {
            "file_read", "mcp_run_shell_command",
        }

    @pytest.mark.asyncio
    async def test_floor_survives_a_scope_that_omits_it(self):
        # The regression that produced models reporting "no
        # render_diagram tooling available in this task scope".
        registered = [_StubTool("file_read"), _StubTool("render_diagram"),
                      _StubTool("emit_artifact")]
        mgr = MagicMock()
        mgr.is_initialized = True
        with patch("app.mcp.manager.get_mcp_manager", return_value=mgr), \
             patch("app.mcp.enhanced_tools.create_secure_mcp_tools",
                   return_value=registered):
            all_tools, *_ = await self._executor()._load_and_prepare_tools(
                tool_allowlist=["file_read"],
            )
        names = {t.name for t in all_tools}
        assert "render_diagram" in names
        assert "emit_artifact" in names


# ── Prompt / payload agreement ───────────────────────────────────────

class TestPromptDescribesEffectiveSet:
    def _section(self, tools):
        from app.utils.session_context_prompt import _format_tools_section
        scope = MagicMock()
        scope.tools = tools
        scope.skills = []
        scope.paths = []
        scope.shell_commands = []
        return "\n".join(_format_tools_section(scope))

    def test_no_scope_is_a_noop(self):
        from app.utils.session_context_prompt import _format_tools_section
        assert _format_tools_section(None) == []

    def test_unrestricted_scope_is_a_noop(self):
        assert self._section([]) == ""

    def test_floor_tools_are_listed(self):
        text = self._section(["file_read"])
        assert "file_read" in text
        assert "render_diagram" in text
        assert "emit_artifact" in text

    def test_floor_is_labelled_as_always_available(self):
        assert "Always available regardless of scope" in self._section(["file_read"])

    def test_prompt_matches_what_enforcement_keeps(self):
        # The invariant that failed in production: the prompt's claimed
        # set and the enforced set must be the same set.
        requested = ["file_read", "run_shell_command"]
        registered = [
            _tool("file_read"), _tool("mcp_run_shell_command"),
            _tool("render_diagram"), _tool("emit_artifact"),
            _tool("mcp_unrelated"),
        ]
        enforced = {normalize_tool_name(t.name)
                    for t in filter_tools_by_scope(registered, requested)}
        text = self._section(requested)
        for name in enforced:
            assert name in text, f"{name} enforced but absent from prompt"
        assert "unrelated" not in text
