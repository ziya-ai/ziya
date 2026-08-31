"""Tests for scope handling in app.agents.task_executor.

Covers:
  - Skill loading: prompts injected, missing skills warn but don't abort
  - File preloading: contents injected, size caps enforced, path escape
    rejected, non-existent files warn
  - Tool filter: warnings for requested-but-unavailable tools
  - Warning plumbing into Artifact.decisions

execute_task_block is exercised end-to-end by patching the
StreamingToolExecutor so we can inspect the exact messages handed
to the model without making real API calls.
"""

import json
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from app.agents import task_executor
from app.models.task_card import Block, TaskScope, ScopeEntry, Artifact


# ── Helpers ──────────────────────────────────────────────────────────

def _ctx_files(*paths: str):
    """Build a list of context-preload ScopeEntries for the given files."""
    return [ScopeEntry(path=p, is_dir=False, read=True, context=True) for p in paths]


def _task(instructions: str = "do it", **scope_kwargs) -> Block:
    scope = TaskScope(**scope_kwargs) if scope_kwargs else None
    return Block(
        block_type="task",
        id="task-1",
        name="T",
        instructions=instructions,
        scope=scope,
    )


class _FakeExecutor:
    """Replacement for StreamingToolExecutor that captures messages
    and yields a single text chunk + stream_end."""

    captured_messages = None
    captured_tools = None
    captured_kwargs = None

    def __init__(self, *args, **kwargs):
        pass

    async def stream_with_tools(self, messages, tools=None, **kwargs):
        # Record what the executor was called with so tests can inspect.
        type(self).captured_messages = messages
        type(self).captured_tools = tools
        type(self).captured_kwargs = kwargs
        yield {"type": "text", "content": "done"}
        yield {"type": "stream_end"}


@pytest.fixture
def fake_executor():
    # Reset capture state between tests.
    _FakeExecutor.captured_messages = None
    _FakeExecutor.captured_tools = None
    _FakeExecutor.captured_kwargs = None
    with patch("app.streaming_tool_executor.StreamingToolExecutor", _FakeExecutor), \
         patch("app.agents.models.ModelManager.get_state",
               return_value={"aws_region": "us-east-1", "aws_profile": "x",
                             "current_model": "fake"}), \
         patch("app.mcp.enhanced_tools.create_secure_mcp_tools", return_value=[]):
        yield _FakeExecutor


def _system_text(executor_cls) -> str:
    msgs = executor_cls.captured_messages or []
    assert msgs, "executor not called"
    return msgs[0].content  # SystemMessage is first


# ── Structural validation ────────────────────────────────────────────

class TestValidation:
    @pytest.mark.asyncio
    async def test_non_task_block_rejected(self):
        block = Block(block_type="repeat", id="r", name="R",
                      repeat_mode="count", repeat_count=1)
        with pytest.raises(task_executor.TaskExecutorError):
            await task_executor.execute_task_block(block)

    @pytest.mark.asyncio
    async def test_empty_instructions_rejected(self):
        block = Block(block_type="task", id="t", name="T", instructions="  ")
        with pytest.raises(task_executor.TaskExecutorError):
            await task_executor.execute_task_block(block)

    @pytest.mark.asyncio
    async def test_run_id_passed_as_conversation_id(self, fake_executor):
        # Regression test: execute_task_block must forward run_id to
        # stream_with_tools as conversation_id, otherwise
        # app.context.set_conversation_id() is never called during a
        # Task Card run and model-driven context tools
        # (context_add_file/context_remove_file/context_list_files)
        # fail with "no conversation_id is set in the current request
        # context".
        block = _task("do it")
        await task_executor.execute_task_block(block, run_id="run-xyz")
        assert fake_executor.captured_kwargs is not None
        assert fake_executor.captured_kwargs.get("conversation_id") == "run-xyz"

    @pytest.mark.asyncio
    async def test_no_run_id_omits_conversation_id(self, fake_executor):
        # When no run_id is supplied (e.g. a caller that predates this
        # fix, or a non-Task-Card invocation), conversation_id should
        # simply be None rather than an empty string or missing kwarg
        # entirely — stream_with_tools treats falsy conversation_id as
        # "no conversation context" and skips set_conversation_id().
        block = _task("do it")
        await task_executor.execute_task_block(block)
        assert fake_executor.captured_kwargs is not None
        assert fake_executor.captured_kwargs.get("conversation_id") is None


# ── Skills ───────────────────────────────────────────────────────────

class TestSkills:
    @pytest.mark.asyncio
    async def test_no_project_id_emits_warning(self, fake_executor):
        block = _task(skills=["some-skill"])
        artifact = await task_executor.execute_task_block(block, project_id=None)
        assert any("no project_id" in d for d in artifact.decisions)

    @pytest.mark.asyncio
    async def test_skill_prompt_injected(self, fake_executor, tmp_path, monkeypatch):
        # Build a real skill on disk so SkillStorage resolves it.
        from app.storage.skills import SkillStorage
        from app.services.token_service import TokenService
        monkeypatch.setattr("app.utils.paths.get_project_dir",
                            lambda pid: tmp_path)
        storage = SkillStorage(tmp_path, TokenService())
        # Insert a custom skill directly.
        skill_dir = tmp_path / "skills"
        skill_dir.mkdir(parents=True, exist_ok=True)
        skill_data = {
            "id": "my-skill", "name": "My Skill",
            "description": "d", "prompt": "ALWAYS DO X",
            "color": "#000", "tokenCount": 0,
            "isBuiltIn": False, "createdAt": 0, "lastUsedAt": 0,
        }
        (skill_dir / "my-skill.json").write_text(json.dumps(skill_data))

        block = _task(skills=["my-skill"])
        artifact = await task_executor.execute_task_block(
            block, project_id="proj-1",
        )
        sys_text = _system_text(fake_executor)
        assert "[Active Skill: My Skill]" in sys_text
        assert "ALWAYS DO X" in sys_text
        # No warnings for a successfully-loaded skill.
        assert not any("my-skill" in d for d in artifact.decisions)

    @pytest.mark.asyncio
    async def test_missing_skill_warns(self, fake_executor, tmp_path, monkeypatch):
        monkeypatch.setattr("app.utils.paths.get_project_dir",
                            lambda pid: tmp_path)
        block = _task(skills=["does-not-exist"])
        artifact = await task_executor.execute_task_block(
            block, project_id="proj-1",
        )
        assert any("does-not-exist" in d and "not found" in d
                   for d in artifact.decisions)


# ── File preload ─────────────────────────────────────────────────────

class TestFilePreload:
    @pytest.mark.asyncio
    async def test_no_project_root_warns(self, fake_executor):
        block = _task(paths=_ctx_files("a.py"))
        artifact = await task_executor.execute_task_block(block, project_root=None)
        assert any("no project_root" in d for d in artifact.decisions)

    @pytest.mark.asyncio
    async def test_existing_file_injected(self, fake_executor, tmp_path):
        (tmp_path / "hello.py").write_text("print('hi')\n")
        block = _task(paths=_ctx_files("hello.py"))
        await task_executor.execute_task_block(
            block, project_root=str(tmp_path),
        )
        sys_text = _system_text(fake_executor)
        assert "### hello.py" in sys_text
        assert "print('hi')" in sys_text

    @pytest.mark.asyncio
    async def test_missing_file_warns_but_continues(self, fake_executor, tmp_path):
        (tmp_path / "exists.py").write_text("yes\n")
        block = _task(paths=_ctx_files("missing.py", "exists.py"))
        artifact = await task_executor.execute_task_block(
            block, project_root=str(tmp_path),
        )
        assert any("missing.py" in d for d in artifact.decisions)
        sys_text = _system_text(fake_executor)
        assert "### exists.py" in sys_text

    @pytest.mark.asyncio
    async def test_path_escape_rejected(self, fake_executor, tmp_path):
        outside = tmp_path.parent / "secret.txt"
        outside.write_text("SECRET")
        block = _task(paths=_ctx_files("../secret.txt"))
        artifact = await task_executor.execute_task_block(
            block, project_root=str(tmp_path),
        )
        sys_text = _system_text(fake_executor)
        assert "SECRET" not in sys_text
        assert any("escapes" in d for d in artifact.decisions)

    @pytest.mark.asyncio
    async def test_per_file_size_cap(self, fake_executor, tmp_path, monkeypatch):
        # Shrink the cap so the test stays fast.
        monkeypatch.setattr(task_executor, "_MAX_FILE_BYTES", 100)
        monkeypatch.setattr(task_executor, "_MAX_TOTAL_FILE_BYTES", 1_000_000)
        big = "x" * 500
        (tmp_path / "big.txt").write_text(big)
        block = _task(paths=_ctx_files("big.txt"))
        artifact = await task_executor.execute_task_block(
            block, project_root=str(tmp_path),
        )
        sys_text = _system_text(fake_executor)
        # Truncated to cap; still present.
        assert "x" * 100 in sys_text
        assert "x" * 500 not in sys_text
        assert any("cap" in d and "big.txt" in d for d in artifact.decisions)

    @pytest.mark.asyncio
    async def test_total_size_cap_stops_later_files(
        self, fake_executor, tmp_path, monkeypatch,
    ):
        monkeypatch.setattr(task_executor, "_MAX_FILE_BYTES", 1_000_000)
        monkeypatch.setattr(task_executor, "_MAX_TOTAL_FILE_BYTES", 200)
        (tmp_path / "a.txt").write_text("a" * 150)
        (tmp_path / "b.txt").write_text("b" * 150)
        block = _task(paths=_ctx_files("a.txt", "b.txt"))
        artifact = await task_executor.execute_task_block(
            block, project_root=str(tmp_path),
        )
        sys_text = _system_text(fake_executor)
        assert "a" * 150 in sys_text
        assert "b" * 150 not in sys_text
        assert any("total preload cap" in d and "b.txt" in d
                   for d in artifact.decisions)


# ── Tool filter ──────────────────────────────────────────────────────

class TestToolFilter:
    @pytest.mark.asyncio
    async def test_unknown_tool_warns(self, fake_executor):
        # create_secure_mcp_tools returns [] in the fixture, so any
        # requested tool is "unavailable".
        block = _task(tools=["mcp_nonexistent"])
        artifact = await task_executor.execute_task_block(block)
        assert any("mcp_nonexistent" in d for d in artifact.decisions)

    @pytest.mark.asyncio
    async def test_empty_scope_exposes_all(self, fake_executor):
        fake_tool = MagicMock()
        fake_tool.name = "mcp_anything"
        with patch("app.mcp.enhanced_tools.create_secure_mcp_tools",
                   return_value=[fake_tool]):
            block = _task()  # no scope
            await task_executor.execute_task_block(block)
        assert fake_executor.captured_tools == [fake_tool]


# ── Conversation isolation ───────────────────────────────────────────

class TestConversationIsolation:
    """A task-card run must start from a FRESH context, never the parent
    conversation's transcript.

    The isolation guarantee lives entirely in execute_task_block's
    message construction: it builds exactly [SystemMessage, HumanMessage]
    from the block's own scope, and stream_with_tools uses its
    ``conversation_id`` argument only for usage tracking / tool context,
    never to load stored history.  These tests pin that seam: if anyone
    ever prepends parent-chat messages (or threads a chat id through as
    the conversation), the structure assertions here break.
    """

    @pytest.mark.asyncio
    async def test_messages_are_exactly_system_plus_instructions(
        self, fake_executor,
    ):
        from langchain_core.messages import SystemMessage, HumanMessage
        block = _task("summarize the design doc")
        await task_executor.execute_task_block(block)
        msgs = fake_executor.captured_messages
        assert msgs is not None, "executor not called"
        # Exactly two messages: no parent transcript, no prior AI turns.
        assert len(msgs) == 2, (
            f"expected a fresh 2-message context, got {len(msgs)}: "
            f"{[type(m).__name__ for m in msgs]}"
        )
        assert isinstance(msgs[0], SystemMessage)
        assert isinstance(msgs[1], HumanMessage)
        # The human turn is the block's instructions verbatim — not a
        # chat message inherited from the launching conversation.
        assert msgs[1].content == "summarize the design doc"

    @pytest.mark.asyncio
    async def test_system_prompt_declares_sandbox(self, fake_executor):
        block = _task("do it")
        await task_executor.execute_task_block(block)
        sys_text = _system_text(fake_executor)
        assert "isolated task" in sys_text
        assert "sandbox" in sys_text

    @pytest.mark.asyncio
    async def test_conversation_id_is_run_id_not_a_chat_id(
        self, fake_executor,
    ):
        # The run's conversation identity is the (fresh, unique) run_id.
        # Even though bindings record a source_conversation_id on the
        # TaskRun for tile linkage, that id must never become the
        # conversation the model streams under — a fresh run_id means
        # there is no stored history for stream_with_tools' consumers
        # to associate with this stream.
        block = _task("do it")
        await task_executor.execute_task_block(block, run_id="run-fresh-1")
        assert fake_executor.captured_kwargs.get("conversation_id") == "run-fresh-1"

    @pytest.mark.asyncio
    async def test_launch_signature_has_no_history_channel(self):
        # Negative structural check: execute_task_block accepts no
        # parameter through which a parent transcript could arrive.
        # If someone adds one (e.g. ``history=`` / ``messages=`` /
        # ``parent_conversation=``), this test forces them to revisit
        # the isolation contract deliberately.
        import inspect
        params = set(
            inspect.signature(task_executor.execute_task_block).parameters
        )
        forbidden = {"history", "messages", "conversation",
                     "parent_conversation", "chat_messages"}
        assert not (params & forbidden), (
            f"execute_task_block grew a history-bearing parameter: "
            f"{params & forbidden}"
        )
