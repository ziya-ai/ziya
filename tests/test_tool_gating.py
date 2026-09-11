"""Session-signal gating of builtin tool categories.

Covers the gate table, signal detection from hard facts, the filter's
respect for explicit task-scope allowlists, and — the part that actually
saves tokens — that ``StreamingToolExecutor._load_and_prepare_tools``
applies it so the provider payload no longer carries the gated tools.
"""

from __future__ import annotations

import asyncio
import os
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from app.utils import tool_gating
from app.utils.tool_gating import (
    SessionSignals, detect_session_signals, filter_tools_by_session,
    gated_out_categories, tool_category_map,
)


def _tool(name):
    return SimpleNamespace(name=name, metadata={})


@pytest.fixture(autouse=True)
def _gating_on(monkeypatch):
    monkeypatch.delenv("ZIYA_TOOL_GATING", raising=False)


# ---------------------------------------------------------------------------
# Category map is grounded in the real registry
# ---------------------------------------------------------------------------

class TestCategoryMap:
    def test_gated_categories_resolve_real_tool_names(self):
        m = tool_category_map()
        assert m.get("emit_artifact") == "task_artifacts"
        assert m.get("list_run_artifacts") == "task_artifacts"
        assert m.get("shadow_list") == "shadow"
        assert m.get("shadow_read") == "shadow"
        assert m.get("pdf_outline") == "pdf_rag"
        assert m.get("pdf_search") == "pdf_rag"
        # Core tools are mapped but never gated.
        assert m.get("file_read") == "fileio"
        assert "fileio" not in tool_gating.CATEGORY_GATES


# ---------------------------------------------------------------------------
# Gates
# ---------------------------------------------------------------------------

class TestGates:
    def test_only_task_artifacts_closed_by_default(self):
        assert gated_out_categories(SessionSignals()) == {"task_artifacts"}

    def test_task_run_opens_the_gate(self):
        assert gated_out_categories(SessionSignals(in_task_run=True)) == set()

    def test_shadow_and_pdf_are_never_gated(self):
        """The tool list heads the provider cache prefix; a category whose
        signal can change mid-conversation (a shadow session started, a PDF
        added) would bust every downstream breakpoint when it flips.  Those
        two gates were withdrawn for that reason and must stay withdrawn —
        their signals are still detected, but no gate consumes them."""
        assert "shadow" not in tool_gating.CATEGORY_GATES
        assert "pdf_rag" not in tool_gating.CATEGORY_GATES
        # Signal state is irrelevant to their exposure.
        for s in (SessionSignals(), SessionSignals(shadow_sessions=True, pdf_relevant=True)):
            assert not ({"shadow", "pdf_rag"} & gated_out_categories(s))

    def test_every_gate_is_conversation_stable(self):
        """Contract for anyone adding a gate: only ``in_task_run`` is
        allowed today.  Extend this set deliberately, with the cache
        argument made, rather than by accident."""
        stable = {"task_artifacts"}
        assert set(tool_gating.CATEGORY_GATES) <= stable


class TestFilter:
    def _tools(self):
        return [_tool(n) for n in ("file_read", "emit_artifact", "shadow_read", "pdf_outline", "mcp_slack_post")]

    def test_drops_only_gated_categories(self):
        kept, dropped = filter_tools_by_session(self._tools(), SessionSignals())
        assert [t.name for t in kept] == ["file_read", "shadow_read", "pdf_outline", "mcp_slack_post"]
        assert dropped == ["emit_artifact"]

    def test_positive_path_keeps_everything(self):
        tools = self._tools()
        kept, dropped = filter_tools_by_session(tools, SessionSignals(in_task_run=True))
        assert kept is tools and dropped == []

    def test_explicit_scope_protects_tool_with_prefix_normalization(self, monkeypatch):
        # Temporarily gate shadow so the keep path has a subject; the
        # production table no longer gates it (see TestGates).
        monkeypatch.setitem(tool_gating.CATEGORY_GATES, "shadow", lambda s: s.shadow_sessions)
        kept, dropped = filter_tools_by_session(
            [_tool("mcp_shadow_read"), _tool("shadow_list")], SessionSignals(),
            keep=["shadow_read"],
        )
        assert [t.name for t in kept] == ["mcp_shadow_read"]
        assert dropped == ["shadow_list"]

    def test_env_kill_switch(self, monkeypatch):
        monkeypatch.setenv("ZIYA_TOOL_GATING", "0")
        tools = self._tools()
        kept, dropped = filter_tools_by_session(tools, SessionSignals())
        assert kept is tools and dropped == []


# ---------------------------------------------------------------------------
# Signal detection from hard facts
# ---------------------------------------------------------------------------

class TestSignals:
    def test_task_run_signal_follows_collector_contextvar(self):
        from app.utils.task_artifacts import start_artifact_collection, finish_artifact_collection
        assert detect_session_signals().in_task_run is False
        token = start_artifact_collection(block_id="b")
        try:
            assert detect_session_signals().in_task_run is True
        finally:
            finish_artifact_collection(token)
        assert detect_session_signals().in_task_run is False

    def test_pdf_signal_from_index_dir(self, tmp_path):
        assert detect_session_signals(project_root=str(tmp_path)).pdf_relevant is False
        idx = tmp_path / ".ziya" / "pdf_index"
        idx.mkdir(parents=True)
        assert detect_session_signals(project_root=str(tmp_path)).pdf_relevant is False  # empty
        (idx / "x.json").write_text("{}")
        assert detect_session_signals(project_root=str(tmp_path)).pdf_relevant is True

    def test_pdf_signal_from_messages_including_block_content(self, tmp_path):
        msgs = [{"role": "user", "content": [{"type": "text", "text": "read Docs/Spec.PDF"}]}]
        assert detect_session_signals(msgs, str(tmp_path)).pdf_relevant is True
        assert detect_session_signals([{"role": "user", "content": "hi"}], str(tmp_path)).pdf_relevant is False

    def test_shadow_signal_absent_dir_does_not_create_it(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HOME", str(tmp_path))
        assert detect_session_signals().shadow_sessions is False
        assert not (tmp_path / ".ziya" / "shadow").exists()

    def test_shadow_signal_uses_registry_not_client(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HOME", str(tmp_path))
        (tmp_path / ".ziya" / "shadow" / "sessions").mkdir(parents=True)
        with patch("app.shadow.registry.list_sessions", return_value=[object()]) as reg, \
             patch("app.shadow.client.list_sessions", side_effect=AssertionError("socket probe")):
            assert detect_session_signals().shadow_sessions is True
            reg.assert_called_once()


# ---------------------------------------------------------------------------
# Seam: the executor applies the gate to the payload it builds
# ---------------------------------------------------------------------------

class TestExecutorSeam:
    def _run(self, signals, tool_allowlist=None):
        from app.streaming_tool_executor import StreamingToolExecutor
        from app.mcp.enhanced_tools import DirectMCPTool
        from app.mcp.tools.fileio import FileReadTool
        from app.mcp.tools.emit_artifact import EmitArtifactTool
        from app.mcp.tools.shadow_tools import ShadowListTool
        fake_tools = [DirectMCPTool(FileReadTool()), DirectMCPTool(EmitArtifactTool()),
                      DirectMCPTool(ShadowListTool())]
        ex = object.__new__(StreamingToolExecutor)
        ex.model_config = {}
        with patch("app.mcp.manager.get_mcp_manager",
                   return_value=SimpleNamespace(is_initialized=True)), \
             patch("app.mcp.enhanced_tools.create_secure_mcp_tools", return_value=list(fake_tools)), \
             patch("app.utils.tool_gating.detect_session_signals", return_value=signals):
            return asyncio.run(ex._load_and_prepare_tools(tool_allowlist=tool_allowlist))

    def test_gated_tools_absent_from_provider_payload(self):
        all_tools, bedrock_tools, builtin_names, *_ = self._run(SessionSignals())
        names = {t.name for t in all_tools}
        assert "file_read" in names
        assert "emit_artifact" not in names
        assert "emit_artifact" not in builtin_names
        # shadow is ungated: present with no live session, in the payload.
        assert "shadow_list" in names
        assert {t["name"] for t in bedrock_tools} == {"file_read", "shadow_list"}

    def test_open_gates_keep_tools(self):
        all_tools, *_ = self._run(SessionSignals(in_task_run=True))
        assert {t.name for t in all_tools} >= {"file_read", "emit_artifact", "shadow_list"}

    def test_scope_allowlist_narrows_but_floor_survives_closed_gate(self):
        """A non-empty allowlist only ever comes from the task executor, so
        the always-available floor (emit_artifact, ...) must survive gating
        under a scope even when this test's signals say no collector is
        open — the executor unconditionally tells the model to emit, and a
        tool it is told to call must be in the payload.  Mirrors
        test_task_tool_floor::test_floor_survives_a_scope_that_omits_it."""
        all_tools, *_ = self._run(SessionSignals(), tool_allowlist=["file_read"])
        names = {t.name for t in all_tools}
        assert "emit_artifact" in names
        # Not on the floor, not requested: removed by the scope allowlist
        # (not by gating — shadow is ungated).
        assert "shadow_list" not in names

    def test_conversation_id_reaches_signal_detection(self):
        """Hysteresis is keyed on conversation_id; if the executor does not
        pass it, every gate is evaluated fresh each turn and a closing
        signal busts the prompt cache.  Assert the seam carries it."""
        from app.streaming_tool_executor import StreamingToolExecutor
        ex = object.__new__(StreamingToolExecutor)
        ex.model_config = {}
        seen = {}

        def fake_detect(messages=None, project_root=None, conversation_id=None):
            seen["cid"] = conversation_id
            return SessionSignals()

        with patch("app.mcp.manager.get_mcp_manager",
                   return_value=SimpleNamespace(is_initialized=True)), \
             patch("app.mcp.enhanced_tools.create_secure_mcp_tools", return_value=[]), \
             patch("app.utils.tool_gating.detect_session_signals", side_effect=fake_detect):
            asyncio.run(ex._load_and_prepare_tools(conversation_id="conv-42"))
        assert seen.get("cid") == "conv-42"

    def test_stream_impl_forwards_allowlist_and_conversation_id(self):
        """The task-scope bug was two correct halves that never met:
        task_executor passed tool_allowlist into stream_with_tools, and
        _load_and_prepare_tools enforced it, but the call between them
        dropped the argument.  Pin the call site's keywords via the AST
        (identifier-anchored, not line-anchored)."""
        import ast, inspect
        import app.streaming_tool_executor as mod
        tree = ast.parse(inspect.getsource(mod))
        impl = next(
            n for n in ast.walk(tree)
            if isinstance(n, ast.AsyncFunctionDef) and n.name == "_stream_with_tools_impl"
        )
        calls = [
            n for n in ast.walk(impl)
            if isinstance(n, ast.Call)
            and isinstance(n.func, ast.Attribute)
            and n.func.attr == "_load_and_prepare_tools"
        ]
        assert calls, "_stream_with_tools_impl must call _load_and_prepare_tools"
        for call in calls:
            kws = {k.arg: k.value for k in call.keywords}
            assert isinstance(kws.get("tool_allowlist"), ast.Name) and \
                kws["tool_allowlist"].id == "tool_allowlist"
            assert isinstance(kws.get("conversation_id"), ast.Name) and \
                kws["conversation_id"].id == "conversation_id"


# ── Hysteresis ───────────────────────────────────────────────────────

class TestStickyGates:
    """Once a gate opens for a conversation it must not close again: the
    tool list heads the cached request prefix, so a closing gate would
    miss every cache breakpoint for no benefit."""

    @pytest.fixture(autouse=True)
    def _clean(self):
        tool_gating.forget_conversation_gates("c1")
        tool_gating.forget_conversation_gates("c2")
        yield
        tool_gating.forget_conversation_gates("c1")
        tool_gating.forget_conversation_gates("c2")

    def _detect(self, cid, **raw):
        with patch.object(tool_gating, "_in_task_run", return_value=raw.get("task", False)), \
             patch.object(tool_gating, "_shadow_sessions_live", return_value=raw.get("shadow", False)), \
             patch.object(tool_gating, "_pdf_relevant", return_value=raw.get("pdf", False)):
            return detect_session_signals(conversation_id=cid)

    def test_gate_stays_open_after_signal_disappears(self):
        assert self._detect("c1", shadow=True).shadow_sessions is True
        # Shadow session exits: raw signal false, reported signal still true.
        later = self._detect("c1")
        assert later.shadow_sessions is True
        assert later.pdf_relevant is False  # only what was seen is sticky

    def test_stickiness_is_per_conversation(self):
        self._detect("c1", pdf=True)
        assert self._detect("c2").pdf_relevant is False

    def test_no_conversation_id_is_not_sticky(self):
        with patch.object(tool_gating, "_in_task_run", return_value=False), \
             patch.object(tool_gating, "_shadow_sessions_live", return_value=True), \
             patch.object(tool_gating, "_pdf_relevant", return_value=False):
            assert detect_session_signals().shadow_sessions is True
        with patch.object(tool_gating, "_in_task_run", return_value=False), \
             patch.object(tool_gating, "_shadow_sessions_live", return_value=False), \
             patch.object(tool_gating, "_pdf_relevant", return_value=False):
            assert detect_session_signals().shadow_sessions is False

    def test_forget_clears_record(self):
        self._detect("c1", pdf=True)
        tool_gating.forget_conversation_gates("c1")
        assert self._detect("c1").pdf_relevant is False

    def test_lru_bound(self, monkeypatch):
        monkeypatch.setattr(tool_gating, "_STICKY_MAX_CONVERSATIONS", 2)
        try:
            self._detect("c1", pdf=True)
            self._detect("c2", pdf=True)
            self._detect("c3", pdf=True)  # evicts c1
            assert "c1" not in tool_gating._sticky_open
            assert {"c2", "c3"} <= set(tool_gating._sticky_open)
        finally:
            tool_gating.forget_conversation_gates("c3")
