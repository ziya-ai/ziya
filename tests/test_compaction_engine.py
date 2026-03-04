"""
Tests for the CompactionEngine (Layer 1).

Covers Phase A (deterministic extraction), Phase B (LLM summary + fallback),
and integration tests for the full compact() pipeline.
"""
import asyncio
import json
import pytest
from unittest.mock import AsyncMock, patch
from app.agents.compaction_engine import CompactionEngine, get_compaction_engine, MIN_COMPACTION_TOKENS
from app.models.delegate import MemoryCrystal, FileChange

# Helper to build fenced tool blocks with real backticks
BT4 = chr(96) * 4  # 4-backtick fence


def _make_file_write_msg(path, content, create_only=False, patch_text=None):
    """Build a message containing a fenced file_write tool block."""
    tool_input = {"path": path, "content": content}
    if create_only:
        tool_input["create_only"] = True
    if patch_text:
        tool_input["patch"] = patch_text
    return {
        "role": "assistant",
        "content": f"Writing file.\n\n{BT4}tool:mcp_file_write\n{json.dumps(tool_input)}\n{BT4}\n",
    }


def _make_shell_msg(command):
    return {
        "role": "assistant",
        "content": f"Running.\n\n{BT4}tool:mcp_run_shell_command\n{json.dumps({'command': command})}\n{BT4}\n",
    }


def _make_native_tool_use_msg(name, tool_input):
    """Build a message using Bedrock's native tool_use content block format."""
    return {
        "role": "assistant",
        "content": [
            {"type": "tool_use", "name": name, "input": tool_input},
        ],
    }


class TestPhaseAFileChanges:
    def setup_method(self):
        self.engine = CompactionEngine()

    def test_extract_create(self):
        msgs = [_make_file_write_msg("auth/provider.py", "class P:\n    pass\n", create_only=True)]
        changes = self.engine._extract_file_changes(msgs)
        assert len(changes) == 1
        assert changes[0].path == "auth/provider.py"
        assert changes[0].action == "created"

    def test_extract_modify_via_patch(self):
        msgs = [_make_file_write_msg("config.py", "X=1\n", patch_text="old")]
        changes = self.engine._extract_file_changes(msgs)
        assert len(changes) == 1
        assert changes[0].action == "modified"
        assert "patched" in changes[0].line_delta

    def test_extract_deduplicates_by_path(self):
        msgs = [
            _make_file_write_msg("a.py", "v1", create_only=True),
            _make_file_write_msg("a.py", "v2"),
        ]
        changes = self.engine._extract_file_changes(msgs)
        assert len(changes) == 1
        assert changes[0].action == "modified"  # second write overrides

    def test_extract_multiple_files(self):
        msgs = [
            _make_file_write_msg("a.py", "x", create_only=True),
            _make_file_write_msg("b.py", "y"),
            _make_shell_msg("pytest"),
        ]
        changes = self.engine._extract_file_changes(msgs)
        assert len(changes) == 2  # shell command not a file change
        paths = {c.path for c in changes}
        assert paths == {"a.py", "b.py"}

    def test_native_tool_use_format(self):
        msgs = [_make_native_tool_use_msg("file_write", {"path": "x.py", "content": "a=1\n", "create_only": True})]
        changes = self.engine._extract_file_changes(msgs)
        assert len(changes) == 1
        assert changes[0].path == "x.py"
        assert changes[0].action == "created"


class TestPhaseAToolStats:
    def setup_method(self):
        self.engine = CompactionEngine()

    def test_counts_by_type(self):
        msgs = [
            _make_file_write_msg("a.py", "x"),
            _make_file_write_msg("b.py", "y"),
            _make_shell_msg("pytest"),
        ]
        stats = self.engine._extract_tool_stats(msgs)
        assert stats.get("file_write", 0) == 2
        assert stats.get("run_shell_command", 0) == 1

    def test_empty_messages(self):
        stats = self.engine._extract_tool_stats([])
        assert stats == {}

    def test_strips_mcp_prefix(self):
        msgs = [_make_shell_msg("ls")]
        stats = self.engine._extract_tool_stats(msgs)
        # Should store without mcp_ prefix
        assert "run_shell_command" in stats
        assert "mcp_run_shell_command" not in stats


class TestPhaseADecisions:
    def setup_method(self):
        self.engine = CompactionEngine()

    def test_finds_decision_markers(self):
        msgs = [
            {"role": "assistant", "content": "I decided to use RS256 for JWT signing because it's safer."},
        ]
        decisions = self.engine._extract_decisions(msgs)
        assert len(decisions) >= 1
        assert any("RS256" in d for d in decisions)

    def test_ignores_human_messages(self):
        msgs = [
            {"role": "human", "content": "I decided to cancel everything."},
        ]
        decisions = self.engine._extract_decisions(msgs)
        assert len(decisions) == 0

    def test_ignores_short_sentences(self):
        msgs = [
            {"role": "assistant", "content": "I chose X."},  # too short (<15 chars)
        ]
        decisions = self.engine._extract_decisions(msgs)
        assert len(decisions) == 0

    def test_caps_at_ten(self):
        # Generate 15 distinct decision sentences
        msgs = [
            {"role": "assistant", "content": ". ".join(
                f"I decided to implement approach number {i} for this component" for i in range(15)
            )},
        ]
        decisions = self.engine._extract_decisions(msgs)
        assert len(decisions) <= 10

    def test_multiple_patterns(self):
        msgs = [
            {"role": "assistant", "content": "Using PKCE instead of implicit flow for security reasons."},
            {"role": "assistant", "content": "We'll use Redis for the token blacklist."},
        ]
        decisions = self.engine._extract_decisions(msgs)
        assert len(decisions) == 2


class TestPhaseAExports:
    def setup_method(self):
        self.engine = CompactionEngine()

    def test_finds_classes_and_functions(self):
        msgs = [_make_file_write_msg(
            "auth/provider.py",
            "class OAuthProvider:\n    pass\n\ndef get_provider():\n    return OAuthProvider()\n",
            create_only=True,
        )]
        changes = self.engine._extract_file_changes(msgs)
        exports = self.engine._extract_exports(msgs, changes)
        assert "OAuthProvider" in exports
        assert "get_provider" in exports
        assert exports["OAuthProvider"] == "auth.provider.OAuthProvider"

    def test_skips_private_symbols(self):
        msgs = [_make_file_write_msg(
            "util.py",
            "def _helper():\n    pass\n\ndef public_fn():\n    pass\n",
        )]
        changes = self.engine._extract_file_changes(msgs)
        exports = self.engine._extract_exports(msgs, changes)
        assert "_helper" not in exports
        assert "public_fn" in exports


class TestPhaseAToolBlockParsing:
    def setup_method(self):
        self.engine = CompactionEngine()

    def test_fenced_format(self):
        msg = {"role": "assistant", "content": f'{BT4}tool:mcp_file_read\n{{"path": "x.py"}}\n{BT4}'}
        blocks = self.engine._extract_tool_blocks(msg)
        assert len(blocks) == 1
        assert blocks[0][0] == "mcp_file_read"
        assert blocks[0][1]["path"] == "x.py"

    def test_native_bedrock_format(self):
        msg = _make_native_tool_use_msg("run_shell_command", {"command": "ls"})
        blocks = self.engine._extract_tool_blocks(msg)
        assert len(blocks) == 1
        assert blocks[0][0] == "run_shell_command"
        assert blocks[0][1]["command"] == "ls"

    def test_non_json_body(self):
        msg = {"role": "assistant", "content": f'{BT4}tool:some_tool\nplain text output\n{BT4}'}
        blocks = self.engine._extract_tool_blocks(msg)
        assert len(blocks) == 1
        assert blocks[0][0] == "some_tool"
        assert blocks[0][1] == {}  # non-JSON → empty dict
        assert blocks[0][2] == "plain text output"

    def test_no_tool_blocks(self):
        msg = {"role": "assistant", "content": "Just regular text."}
        blocks = self.engine._extract_tool_blocks(msg)
        assert len(blocks) == 0

    def test_non_string_content(self):
        msg = {"role": "assistant", "content": 12345}
        blocks = self.engine._extract_tool_blocks(msg)
        assert len(blocks) == 0


class TestPhaseBFallbackSummary:
    def test_content(self):
        changes = [
            FileChange(path="a.py", action="created", line_delta="(new, 50 lines)"),
            FileChange(path="b.py", action="modified", line_delta="+10 -2"),
        ]
        decisions = ["Used RS256 for signing"]
        summary = CompactionEngine._build_fallback_summary("OAuth Setup", changes, decisions)
        assert "OAuth Setup" in summary
        assert "a.py" in summary
        assert "b.py" in summary
        assert "RS256" in summary

    def test_empty_inputs(self):
        summary = CompactionEngine._build_fallback_summary("Task", [], [])
        assert summary == "Completed: Task."

    def test_truncates_at_500(self):
        changes = [FileChange(path=f"file_{i}.py", action="created", line_delta="big") for i in range(50)]
        summary = CompactionEngine._build_fallback_summary("Task", changes, [])
        assert len(summary) <= 500


class TestPhaseBGenerateSummary:
    def setup_method(self):
        self.engine = CompactionEngine()

    @pytest.mark.asyncio
    async def test_falls_back_on_llm_error(self):
        """When LLM call fails, fallback summary is used."""
        # Monkey-patch to simulate LLM failure
        async def _failing_llm(*args, **kwargs):
            raise RuntimeError("LLM unavailable")

        self.engine._call_summary_model = _failing_llm

        changes = [FileChange(path="a.py", action="created", line_delta="new")]
        decisions = ["Chose X"]
        summary = await self.engine._generate_summary(
            [{"role": "assistant", "content": "I did some work."}],
            changes, decisions, "Test Task",
        )
        assert "Test Task" in summary
        assert len(summary) > 10


class TestCompactIntegration:
    def setup_method(self):
        self.engine = CompactionEngine()
        # Disable LLM for predictable tests
        async def _no_llm(*a, **k):
            raise RuntimeError("test: no LLM")
        self.engine._call_summary_model = _no_llm

    @pytest.mark.asyncio
    async def test_below_threshold_returns_none(self):
        result = await self.engine.compact(
            [{"role": "assistant", "content": "short"}],
            "D1", "Task",
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_force_overrides_threshold(self):
        result = await self.engine.compact(
            [{"role": "assistant", "content": "short"}],
            "D1", "Task",
            force=True,
        )
        assert result is not None
        assert result.delegate_id == "D1"

    @pytest.mark.asyncio
    async def test_full_pipeline(self):
        msgs = [
            {"role": "assistant", "content": "I decided to use PKCE for all OAuth flows because it prevents code interception."},
            _make_file_write_msg("auth/provider.py", "class OAuthProvider:\n    def auth(self):\n        pass\n", create_only=True),
            _make_file_write_msg("config.py", "OAUTH=True\n", patch_text="old"),
            _make_shell_msg("python -m pytest tests/"),
            {"role": "assistant", "content": "All tests pass. The OAuth provider is ready."},
        ]
        crystal = await self.engine.compact(msgs, "D1", "OAuth Setup", force=True)

        assert crystal is not None
        assert crystal.delegate_id == "D1"
        assert crystal.task == "OAuth Setup"
        assert len(crystal.files_changed) == 2
        assert any(fc.path == "auth/provider.py" for fc in crystal.files_changed)
        assert any(fc.path == "config.py" for fc in crystal.files_changed)
        assert crystal.tool_stats.get("file_write") == 2
        assert crystal.tool_stats.get("run_shell_command") == 1
        assert len(crystal.decisions) >= 1
        assert any("PKCE" in d for d in crystal.decisions)
        assert "OAuthProvider" in crystal.exports
        assert len(crystal.summary) > 10
        assert crystal.crystal_tokens > 0
        assert crystal.created_at > 0

    @pytest.mark.asyncio
    async def test_crystal_serialization_roundtrip(self):
        msgs = [
            _make_file_write_msg("x.py", "class Foo:\n    pass\n", create_only=True),
            {"role": "assistant", "content": "I chose to implement Foo as a singleton."},
        ]
        crystal = await self.engine.compact(msgs, "D1", "Foo", force=True)
        assert crystal is not None

        d = crystal.model_dump()
        j = json.dumps(d, default=str)
        restored = MemoryCrystal(**json.loads(j))
        assert restored.delegate_id == crystal.delegate_id
        assert restored.summary == crystal.summary
        assert len(restored.files_changed) == len(crystal.files_changed)
        assert restored.exports == crystal.exports


class TestSingleton:
    def test_returns_same_instance(self):
        a = get_compaction_engine()
        b = get_compaction_engine()
        assert a is b


class TestTokenEstimation:
    def test_string_content(self):
        msgs = [{"role": "assistant", "content": "a" * 400}]
        tokens = CompactionEngine._estimate_tokens(msgs)
        assert tokens == 100  # 400 / 4

    def test_list_content(self):
        msgs = [{"role": "assistant", "content": [{"text": "hello world"}]}]
        tokens = CompactionEngine._estimate_tokens(msgs)
        assert tokens > 0

    def test_empty(self):
        assert CompactionEngine._estimate_tokens([]) == 0
