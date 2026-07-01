"""
Regression: the text-fence "fake tool" dispatch path must attach the _task_scope
envelope (ASR F-001).

There are two shell-dispatch paths in the streaming executor:
  • execute_single_tool (structured tool-use) — builds the _task_scope envelope.
  • _execute_fake_tool (text-fence intercept)  — historically called call_tool
    bare, so a CLI-task / Task-Card escalation carried in the task contextvars
    never reached the shell server and the command was denied at the floor even
    though it was approved.  This pins that the fake path now builds the same
    envelope from the contextvars.
"""

import asyncio
import pytest

from app.context import (
    set_task_shell_commands, reset_task_shell_commands,
    set_task_writable_paths, reset_task_writable_paths,
)
from app.streaming_tool_executor import StreamingToolExecutor


class _CaptureManager:
    """mcp_manager stand-in. The real MCPManager.call_tool builds the
    _task_scope envelope centrally from the active task contextvars when the
    caller passes none, so this stand-in replicates that inline fallback to
    validate end-to-end delivery: the fake-tool path passes bare args, and the
    manager supplies the grant — no dispatch path needs to construct it."""
    def __init__(self):
        self.captured_args = None

    async def call_tool(self, tool_name, arguments, server_name=None):
        args = dict(arguments) if isinstance(arguments, dict) else arguments
        task_scope = args.get("_task_scope") if isinstance(args, dict) else None
        if task_scope is None:
            from app.context import (
                get_task_writable_paths, get_task_readable_paths,
                get_task_shell_commands, get_project_root,
            )
            _twp = get_task_writable_paths()
            _trp = get_task_readable_paths()
            _tsc = get_task_shell_commands()
            if _twp or _trp or _tsc:
                args["_task_scope"] = {
                    "writable": _twp or [],
                    "readable": _trp or [],
                    "shell_commands": list(_tsc) if _tsc else [],
                    "project_root": get_project_root() or "",
                }
        self.captured_args = args
        return {"content": [{"text": "ok"}]}


def _executor():
    # Construct without touching AWS/model init.
    return StreamingToolExecutor.__new__(StreamingToolExecutor)


@pytest.mark.asyncio
async def test_fake_tool_attaches_shell_command_grant():
    mgr = _CaptureManager()
    ex = _executor()
    tok = set_task_shell_commands(["gh"])
    try:
        await ex._execute_fake_tool(
            "run_shell_command", "gh --version", "", [], mgr, tool_id="fake_0")
    finally:
        reset_task_shell_commands(tok)
    assert mgr.captured_args is not None
    scope = mgr.captured_args.get("_task_scope")
    assert scope is not None, "fake-tool path did not attach _task_scope"
    assert scope["shell_commands"] == ["gh"]


@pytest.mark.asyncio
async def test_fake_tool_attaches_writable_grant():
    mgr = _CaptureManager()
    ex = _executor()
    tok = set_task_writable_paths([{"pattern": "*.toml"}])
    try:
        await ex._execute_fake_tool(
            "run_shell_command", "echo hi", "", [], mgr, tool_id="fake_0")
    finally:
        reset_task_writable_paths(tok)
    scope = mgr.captured_args.get("_task_scope")
    assert scope is not None
    assert {"pattern": "*.toml"} in scope["writable"]


@pytest.mark.asyncio
async def test_fake_tool_no_scope_when_no_grant():
    """With no active task scope, no _task_scope key is attached (floor)."""
    mgr = _CaptureManager()
    ex = _executor()
    await ex._execute_fake_tool(
        "run_shell_command", "git status", "", [], mgr, tool_id="fake_0")
    assert mgr.captured_args is not None
    assert "_task_scope" not in mgr.captured_args


@pytest.mark.asyncio
async def test_fake_tool_envelope_survives_nested_tasks():
    """The grant is set synchronously before asyncio.run (as cmd_task does);
    confirm it survives into a nested create_task, matching the live stack."""
    mgr = _CaptureManager()
    ex = _executor()
    tok = set_task_shell_commands(["gh"])
    try:
        async def inner():
            await ex._execute_fake_tool(
                "run_shell_command", "gh --version", "", [], mgr, tool_id="x")
        await asyncio.create_task(inner())
    finally:
        reset_task_shell_commands(tok)
    assert mgr.captured_args.get("_task_scope", {}).get("shell_commands") == ["gh"]
