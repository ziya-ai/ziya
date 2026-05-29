"""Tests for the new scope features:

  - ``TaskScope.cwd`` resolution (valid, missing, escaping)
  - ``TaskScope.paths`` write-flag → task-scoped writable allowlist
  - The ``_task_writable_paths`` contextvar plumbing in ``app.context``
  - The additive write check in ``app.mcp.tools.fileio._check_task_scope_write``

The executor is exercised end-to-end with a fake StreamingToolExecutor
(same approach as ``test_task_executor_scope.py``).
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from app.agents import task_executor
from app.models.task_card import Block, TaskScope, ScopeEntry


# ── Helpers (parallel to test_task_executor_scope.py) ────────────────

def _task(instructions: str = "do it", **scope_kwargs) -> Block:
    scope = TaskScope(**scope_kwargs) if scope_kwargs else None
    return Block(
        block_type="task", id="task-1", name="T",
        instructions=instructions, scope=scope,
    )


class _FakeExecutor:
    """Captures the messages and project_root passed to the model and
    yields a single text chunk plus stream_end so the executor returns."""
    captured_messages = None
    captured_project_root = None
    # Hook for tests that need to assert state *during* the stream.
    on_stream = None

    def __init__(self, *args, **kwargs):
        pass

    async def stream_with_tools(self, messages, tools=None, project_root=None, **_):
        type(self).captured_messages = messages
        type(self).captured_project_root = project_root
        if type(self).on_stream is not None:
            type(self).on_stream(messages=messages, project_root=project_root)
        yield {"type": "text", "content": "ok"}
        yield {"type": "stream_end"}


@pytest.fixture
def fake_executor():
    _FakeExecutor.captured_messages = None
    _FakeExecutor.captured_project_root = None
    _FakeExecutor.on_stream = None
    with patch("app.streaming_tool_executor.StreamingToolExecutor", _FakeExecutor), \
         patch("app.agents.models.ModelManager.get_state",
               return_value={"aws_region": "us-east-1", "aws_profile": "x",
                             "current_model": "fake"}), \
         patch("app.mcp.enhanced_tools.create_secure_mcp_tools", return_value=[]):
        yield _FakeExecutor


def _system_text(executor_cls) -> str:
    msgs = executor_cls.captured_messages or []
    sys_msg = next(m for m in msgs if m.__class__.__name__ == "SystemMessage")
    return sys_msg.content


# ── cwd resolution ───────────────────────────────────────────────────

class TestCwd:
    @pytest.mark.asyncio
    async def test_default_uses_project_root(self, fake_executor, tmp_path):
        block = _task()
        await task_executor.execute_task_block(
            block, project_root=str(tmp_path),
        )
        assert fake_executor.captured_project_root == str(tmp_path)

    @pytest.mark.asyncio
    async def test_valid_subdirectory(self, fake_executor, tmp_path):
        sub = tmp_path / "sub"
        sub.mkdir()
        block = _task(cwd="sub")
        artifact = await task_executor.execute_task_block(
            block, project_root=str(tmp_path),
        )
        assert fake_executor.captured_project_root == str(sub.resolve())
        assert not any("cwd" in d for d in artifact.decisions)

    @pytest.mark.asyncio
    async def test_missing_cwd_falls_back(self, fake_executor, tmp_path):
        block = _task(cwd="does-not-exist")
        artifact = await task_executor.execute_task_block(
            block, project_root=str(tmp_path),
        )
        assert fake_executor.captured_project_root == str(tmp_path)
        assert any("not found" in d for d in artifact.decisions)

    @pytest.mark.asyncio
    async def test_cwd_is_a_file_falls_back(self, fake_executor, tmp_path):
        (tmp_path / "afile.txt").write_text("x")
        block = _task(cwd="afile.txt")
        artifact = await task_executor.execute_task_block(
            block, project_root=str(tmp_path),
        )
        assert fake_executor.captured_project_root == str(tmp_path)
        assert any("not a directory" in d or "not found" in d
                   for d in artifact.decisions)

    @pytest.mark.asyncio
    async def test_escape_via_dotdot_rejected(self, fake_executor, tmp_path):
        (tmp_path.parent / "outside").mkdir(exist_ok=True)
        block = _task(cwd="../outside")
        artifact = await task_executor.execute_task_block(
            block, project_root=str(tmp_path),
        )
        assert fake_executor.captured_project_root == str(tmp_path)
        assert any("escapes project root" in d for d in artifact.decisions)

    @pytest.mark.asyncio
    async def test_no_project_root_skips_cwd_resolution(self, fake_executor):
        block = _task(cwd="anywhere")
        artifact = await task_executor.execute_task_block(
            block, project_root=None,
        )
        assert not any("cwd" in d for d in artifact.decisions)


# ── ScopeEntry context-preload semantics ─────────────────────────────

class TestPathsContextFlag:
    @pytest.mark.asyncio
    async def test_context_true_preloads(self, fake_executor, tmp_path):
        (tmp_path / "ctx.py").write_text("CTX_MARKER\n")
        block = _task(paths=[
            ScopeEntry(path="ctx.py", read=True, context=True),
        ])
        await task_executor.execute_task_block(
            block, project_root=str(tmp_path),
        )
        assert "CTX_MARKER" in _system_text(fake_executor)

    @pytest.mark.asyncio
    async def test_context_false_not_preloaded(self, fake_executor, tmp_path):
        (tmp_path / "readme.py").write_text("READ_ME_MARKER\n")
        block = _task(paths=[
            ScopeEntry(path="readme.py", read=True, context=False),
        ])
        await task_executor.execute_task_block(
            block, project_root=str(tmp_path),
        )
        assert "READ_ME_MARKER" not in _system_text(fake_executor)

    @pytest.mark.asyncio
    async def test_directory_with_context_skipped(self, fake_executor, tmp_path):
        sub = tmp_path / "subdir"
        sub.mkdir()
        (sub / "inside.py").write_text("INSIDE_MARKER\n")
        block = _task(paths=[
            ScopeEntry(path="subdir", is_dir=True, read=True, context=True),
        ])
        await task_executor.execute_task_block(
            block, project_root=str(tmp_path),
        )
        assert "INSIDE_MARKER" not in _system_text(fake_executor)


# ── Writable-path contextvar (app.context) ───────────────────────────

class TestWritablePathsContextVar:
    def test_default_is_none(self):
        from app.context import get_task_writable_paths
        assert get_task_writable_paths() is None

    def test_set_and_reset(self):
        from app.context import (
            get_task_writable_paths,
            set_task_writable_paths,
            reset_task_writable_paths,
        )
        token = set_task_writable_paths([{"path": "out", "is_dir": True}])
        try:
            assert get_task_writable_paths() == [{"path": "out", "is_dir": True}]
        finally:
            reset_task_writable_paths(token)
        assert get_task_writable_paths() is None


# ── Additive write check (app.mcp.tools.fileio) ──────────────────────

class TestCheckTaskScopeWrite:
    """The helper is the integration point for write enforcement.

    Returns True iff an active task scope grants write to the path,
    regardless of the base WritePolicy.
    """

    def test_no_grant_returns_false(self, tmp_path):
        from app.mcp.tools.fileio import _check_task_scope_write
        assert _check_task_scope_write("foo.txt", str(tmp_path)) is False

    def test_no_project_root_returns_false(self):
        from app.context import set_task_writable_paths, reset_task_writable_paths
        from app.mcp.tools.fileio import _check_task_scope_write
        token = set_task_writable_paths([{"path": "foo.txt", "is_dir": False}])
        try:
            assert _check_task_scope_write("foo.txt", "") is False
        finally:
            reset_task_writable_paths(token)

    def test_file_grant_exact_match(self, tmp_path):
        from app.context import set_task_writable_paths, reset_task_writable_paths
        from app.mcp.tools.fileio import _check_task_scope_write
        token = set_task_writable_paths([{"path": "out/log.txt", "is_dir": False}])
        try:
            assert _check_task_scope_write("out/log.txt", str(tmp_path)) is True
            assert _check_task_scope_write("out/other.txt", str(tmp_path)) is False
        finally:
            reset_task_writable_paths(token)

    def test_dir_grant_covers_subtree(self, tmp_path):
        from app.context import set_task_writable_paths, reset_task_writable_paths
        from app.mcp.tools.fileio import _check_task_scope_write
        token = set_task_writable_paths([{"path": "out", "is_dir": True}])
        try:
            assert _check_task_scope_write("out/log.txt", str(tmp_path)) is True
            assert _check_task_scope_write("out/sub/deep.txt", str(tmp_path)) is True
            assert _check_task_scope_write("other.txt", str(tmp_path)) is False
            # Regression guard: ``output/`` must not match ``out`` prefix.
            assert _check_task_scope_write("output/log.txt", str(tmp_path)) is False
        finally:
            reset_task_writable_paths(token)

    @pytest.mark.asyncio
    async def test_executor_activates_grant_during_task(
        self, fake_executor, tmp_path,
    ):
        """End-to-end: while the task is running, the helper returns
        True for paths inside the granted directory and False for
        unrelated paths.  After the task finishes, the contextvar is
        cleared."""
        from app.mcp.tools.fileio import _check_task_scope_write
        from app.context import get_task_writable_paths

        captured = {}

        def _grant_hook(messages, project_root):
            captured["inside_grant"] = _check_task_scope_write(
                "out/log.txt", project_root,
            )
            captured["outside_grant"] = _check_task_scope_write(
                "elsewhere/log.txt", project_root,
            )

        fake_executor.on_stream = staticmethod(_grant_hook)

        block = _task(paths=[
            ScopeEntry(path="out", is_dir=True, read=True, write=True),
        ])
        await task_executor.execute_task_block(block, project_root=str(tmp_path))

        assert captured["inside_grant"] is True
        assert captured["outside_grant"] is False
        assert get_task_writable_paths() is None

    @pytest.mark.asyncio
    async def test_no_grant_when_no_write_flag(
        self, fake_executor, tmp_path,
    ):
        """Paths with read/context but no write must not activate any
        write grant during execution."""
        from app.mcp.tools.fileio import _check_task_scope_write
        from app.context import get_task_writable_paths

        captured = {}

        def _no_grant_hook(messages, project_root):
            captured["grant_check"] = _check_task_scope_write(
                "out/log.txt", project_root,
            )
            captured["active"] = get_task_writable_paths()

        fake_executor.on_stream = staticmethod(_no_grant_hook)

        block = _task(paths=[
            ScopeEntry(path="out", is_dir=True, read=True, context=False),
        ])
        await task_executor.execute_task_block(block, project_root=str(tmp_path))

        assert captured["grant_check"] is False
        assert captured["active"] is None


# ── Readable-path grant (out-of-project read access) ─────────────────

class TestReadablePathsContextVar:
    def test_readable_default_is_none(self):
        from app.context import get_task_readable_paths
        assert get_task_readable_paths() is None

    def test_readable_set_and_reset(self):
        from app.context import (
            get_task_readable_paths,
            set_task_readable_paths,
            reset_task_readable_paths,
        )
        token = set_task_readable_paths([{"path": "/etc/hosts", "is_dir": False}])
        try:
            assert get_task_readable_paths() == [
                {"path": "/etc/hosts", "is_dir": False}
            ]
        finally:
            reset_task_readable_paths(token)
        assert get_task_readable_paths() is None


class TestReadablePrefixHelper:
    """``_get_task_readable_prefixes`` strips relative entries and
    expands ``~`` so the returned list can be passed straight to
    ``_resolve_and_validate(allowed_absolute_prefixes=...)``."""

    def test_prefix_no_grant_returns_empty(self):
        from app.mcp.tools.fileio import _get_task_readable_prefixes
        assert _get_task_readable_prefixes() == []

    def test_prefix_relative_entries_skipped(self):
        from app.context import set_task_readable_paths, reset_task_readable_paths
        from app.mcp.tools.fileio import _get_task_readable_prefixes
        # Relative paths are inside-project by construction; they
        # don't need to appear in the absolute allowlist.
        token = set_task_readable_paths([
            {"path": "src/foo.py", "is_dir": False},
            {"path": "/etc/hosts", "is_dir": False},
        ])
        try:
            assert _get_task_readable_prefixes() == ["/etc/hosts"]
        finally:
            reset_task_readable_paths(token)

    def test_prefix_tilde_expanded(self):
        import os
        from app.context import set_task_readable_paths, reset_task_readable_paths
        from app.mcp.tools.fileio import _get_task_readable_prefixes
        token = set_task_readable_paths([{"path": "~/foo", "is_dir": False}])
        try:
            assert _get_task_readable_prefixes() == [os.path.expanduser("~/foo")]
        finally:
            reset_task_readable_paths(token)


class TestFileReadOutOfProjectGrant:
    """End-to-end: ``file_read`` of an out-of-project absolute path
    succeeds iff the active task scope grants read access."""

    async def _read(self, path: str, project_root: str):
        from app.mcp.tools.fileio import FileReadTool
        tool = FileReadTool()
        return await tool.execute(path=path, _workspace_path=project_root)

    @pytest.mark.asyncio
    async def test_read_no_grant_blocks_out_of_project(self, tmp_path):
        outside = tmp_path.parent / "outside.txt"
        outside.write_text("OUTSIDE\n")
        result = await self._read(str(outside), str(tmp_path))
        assert isinstance(result, dict) and result.get("error") is True
        assert "OUTSIDE" not in (result.get("content") or "")

    @pytest.mark.asyncio
    async def test_read_file_grant_allows_exact_path(self, tmp_path):
        from app.context import set_task_readable_paths, reset_task_readable_paths
        outside = tmp_path.parent / "granted.txt"
        outside.write_text("GRANTED\n")
        token = set_task_readable_paths([
            {"path": str(outside), "is_dir": False}
        ])
        try:
            result = await self._read(str(outside), str(tmp_path))
        finally:
            reset_task_readable_paths(token)
        assert result.get("error") is not True, result
        assert "GRANTED" in (result.get("content") or "")

    @pytest.mark.asyncio
    async def test_read_dir_grant_allows_subtree(self, tmp_path):
        from app.context import set_task_readable_paths, reset_task_readable_paths
        outside_dir = tmp_path.parent / "granted-dir"
        outside_dir.mkdir(exist_ok=True)
        target = outside_dir / "deep" / "nested.txt"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("DEEP\n")
        token = set_task_readable_paths([
            {"path": str(outside_dir), "is_dir": True}
        ])
        try:
            result = await self._read(str(target), str(tmp_path))
        finally:
            reset_task_readable_paths(token)
        assert result.get("error") is not True, result
        assert "DEEP" in (result.get("content") or "")

    @pytest.mark.asyncio
    async def test_read_in_project_unaffected_by_grant(self, tmp_path):
        (tmp_path / "inside.txt").write_text("INSIDE\n")
        result = await self._read("inside.txt", str(tmp_path))
        assert result.get("error") is not True, result
        assert "INSIDE" in (result.get("content") or "")


class TestExecutorActivatesReadGrant:
    """The executor builds the read grant from ``paths`` entries with
    ``read=True`` *or* ``write=True`` (write implies read)."""

    @pytest.mark.asyncio
    async def test_read_flag_activates_grant(self, fake_executor, tmp_path):
        from app.context import get_task_readable_paths
        captured = {}
        def _read_hook(messages, project_root):
            captured["entries"] = get_task_readable_paths()
        fake_executor.on_stream = staticmethod(_read_hook)
        block = _task(paths=[
            ScopeEntry(path="/etc/hosts", is_dir=False, read=True),
        ])
        await task_executor.execute_task_block(
            block, project_root=str(tmp_path),
        )
        assert captured["entries"] == [{"path": "/etc/hosts", "is_dir": False}]
        assert get_task_readable_paths() is None

    @pytest.mark.asyncio
    async def test_write_implies_read(self, fake_executor, tmp_path):
        """A write-only grant should still appear in the read list so
        the model can read back what it just wrote."""
        from app.context import get_task_readable_paths
        captured = {}
        def _write_implies_read_hook(messages, project_root):
            captured["entries"] = get_task_readable_paths()
        fake_executor.on_stream = staticmethod(_write_implies_read_hook)
        block = _task(paths=[
            ScopeEntry(path="out", is_dir=True, read=False, write=True),
        ])
        await task_executor.execute_task_block(
            block, project_root=str(tmp_path),
        )
        assert captured["entries"] == [{"path": "out", "is_dir": True}]

    @pytest.mark.asyncio
    async def test_no_read_no_write_no_grant(self, fake_executor, tmp_path):
        """A path with read=False and write=False (e.g. a context-only
        preload entry) should not appear in either grant."""
        from app.context import get_task_readable_paths, get_task_writable_paths
        captured = {}
        def _empty_grant_hook(messages, project_root):
            captured["readable"] = get_task_readable_paths()
            captured["writable"] = get_task_writable_paths()
        fake_executor.on_stream = staticmethod(_empty_grant_hook)
        (tmp_path / "ctx.py").write_text("x")
        block = _task(paths=[
            ScopeEntry(path="ctx.py", read=False, write=False, context=True),
        ])
        await task_executor.execute_task_block(
            block, project_root=str(tmp_path),
        )
        assert captured["readable"] is None
        assert captured["writable"] is None
