"""
Request-scoped project context.

Provides per-request isolation of project_root so concurrent requests from
different browser tabs (different projects) don't race on a shared global.

Uses Python's contextvars, which are natively async-safe: each FastAPI
request handler (and its entire call tree, including StreamingResponse
generators) gets its own copy.
"""

import contextvars
import os
from typing import List, Optional

# Per-request project root — set by middleware, read everywhere.
_request_project_root: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    'request_project_root', default=None
)

# Per-request conversation ID — set when a streaming turn begins, read
# by code paths that retrieve memories without going through MCP tool
# kwargs (notably the system-prompt builder).
_request_conversation_id: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    'request_conversation_id', default=None
)


def set_project_root(path: str) -> None:
    """Set the project root for the current request context."""
    _request_project_root.set(path)

# Per-task scoped writable paths.  When set (by ``task_executor`` while
# a Task block is running) this is a list of ``{path, is_dir}`` dicts
# the task is permitted to write to *in addition* to the base
# ``WritePolicy`` allowlist.  When None, no task-level grant is active
# and only the base policy applies.  Paths are interpreted relative to
# the effective task project_root.
_task_writable_paths: contextvars.ContextVar[Optional[List[dict]]] = contextvars.ContextVar(
    'task_writable_paths', default=None
)

# Per-task scoped readable paths.  Mirror of ``_task_writable_paths``
# but for read access.  Inside-project reads remain unrestricted
# regardless of this list — its purpose is to *grant* read access to
# specific paths (file or directory) that fall outside the project
# root, e.g. ``~/.config/foo`` or ``/etc/hosts``.  When None, no
# task-level read grant is active and only the project root is
# readable.
_task_readable_paths: contextvars.ContextVar[Optional[List[dict]]] = contextvars.ContextVar(
    'task_readable_paths', default=None
)

# Per-task shell command grants.  When set (by ``task_executor`` while
# a Task block is running) this is a list of strings: each entry is
# either a literal first-token allowlist (e.g. "pytest" grants any
# pytest invocation) or, with a "re:" prefix, a regex against the
# full command line.  Consulted by ``ShellWriteChecker`` *only* when
# the base shell policy would block the command, so an empty/None
# value preserves pre-Slice-B semantics.  Cannot bypass
# ``always_blocked`` (sudo/vi/etc.) or redirection blocking.
_task_shell_commands: contextvars.ContextVar[Optional[List[str]]] = contextvars.ContextVar(
    'task_shell_commands', default=None
)


# Per-task iteration context.  Set by ``block_executor`` while a body
# runs inside a Repeat / Until iteration so streaming events emitted
# by the nested task body (``task_text_delta``, ``task_tool_call``)
# can be tagged with the *parent* iteration's block_id and index.
# Without this, the task body's own block_id is the only iteration
# hint on a delta, which is ambiguous when the task is the body of a
# repeat (the repeat owns the iteration boundary, the task does not).
# Carries ``{"block_id": str, "index": int}`` or None.
_task_iteration_context: contextvars.ContextVar[Optional[dict]] = contextvars.ContextVar(
    'task_iteration_context', default=None
)



def get_project_root() -> str:
    """
    Get the project root for the current request.

    Resolution order:
      1. Per-request ContextVar (set by middleware from X-Project-Root header)
      2. ZIYA_USER_CODEBASE_DIR env var (startup / CLI bootstrap)
      3. os.getcwd() (last resort)
    """
    root = _request_project_root.get()
    if root:
        return root
    return os.environ.get("ZIYA_USER_CODEBASE_DIR") or os.getcwd()


def get_project_root_or_none() -> Optional[str]:
    """Get the request-scoped project root, or None if not in a request context."""
    return _request_project_root.get()


def set_conversation_id(conversation_id: str) -> None:
    """Set the conversation ID for the current request context.

    Called by the streaming entry point so deeper code paths
    (memory retrieval, prompt builder) can attribute work to the
    correct conversation without threading it through every call.
    """


# ── Task scope ───────────────────────────────────────────────

def set_task_writable_paths(paths: Optional[List[dict]]) -> contextvars.Token:
    """Set the task-scoped writable allowlist; returns a token for reset.

    Each entry is ``{"path": str, "is_dir": bool}``.  Paths are
    interpreted relative to the effective project root for the task.
    Pass ``None`` to clear (the default).
    """
    return _task_writable_paths.set(paths)


def reset_task_writable_paths(token: contextvars.Token) -> None:
    """Restore the previous task writable list using ``token``."""
    _task_writable_paths.reset(token)


def get_task_writable_paths() -> Optional[List[dict]]:
    """Return the active task writable allowlist, or None if not set."""
    return _task_writable_paths.get()


def set_task_readable_paths(paths: Optional[List[dict]]) -> contextvars.Token:
    """Set the task-scoped readable allowlist; returns a token for reset.

    Each entry is ``{"path": str, "is_dir": bool}``.  Paths are
    interpreted as absolute filesystem paths (after ``~`` expansion).
    Pass ``None`` to clear (the default).

    Inside-project reads are always allowed regardless of this list;
    the list only *adds* permission for out-of-project paths.
    """
    return _task_readable_paths.set(paths)


def reset_task_readable_paths(token: contextvars.Token) -> None:
    """Restore the previous task readable list using ``token``."""
    _task_readable_paths.reset(token)


def get_task_readable_paths() -> Optional[List[dict]]:
    """Return the active task readable allowlist, or None if not set."""
    return _task_readable_paths.get()


def set_task_shell_commands(commands: Optional[List[str]]) -> contextvars.Token:
    """Set the task-scoped shell command allowlist; returns a token for reset.

    Each entry is either a literal first-token match (e.g. ``"pytest"``)
    or, with a ``"re:"`` prefix, a regex against the full command line
    (e.g. ``"re:^make\\s+test(:\\w+)?$"``).  Pass ``None`` to clear.

    Grants are additive over the base shell policy: they bypass the
    global allowlist and the destructive-command list, but never
    override ``always_blocked`` (sudo, vi, etc.) or redirection
    blocking.
    """
    return _task_shell_commands.set(commands)


def reset_task_shell_commands(token: contextvars.Token) -> None:
    """Restore the previous task shell-command list using ``token``."""
    _task_shell_commands.reset(token)


def get_task_shell_commands() -> Optional[List[str]]:
    """Return the active task shell-command allowlist, or None if not set."""
    return _task_shell_commands.get()


def set_task_iteration_context(
    block_id: Optional[str], index: Optional[int],
) -> contextvars.Token:
    """Set the active iteration context for the current task scope.

    ``block_id`` is the *iteration owner* — i.e. the Repeat/Until
    block emitting the boundary events — not the inner task block.
    Pass ``None`` for both to clear.
    """
    value = (
        {"block_id": block_id, "index": int(index)}
        if block_id is not None and index is not None
        else None
    )
    return _task_iteration_context.set(value)


def reset_task_iteration_context(token: contextvars.Token) -> None:
    """Restore the previous iteration context using ``token``."""
    _task_iteration_context.reset(token)


def get_task_iteration_context() -> Optional[dict]:
    """Return ``{'block_id', 'index'}`` if inside an iteration, else None."""
    return _task_iteration_context.get()
    _request_conversation_id.set(conversation_id)


def get_conversation_id_or_none() -> Optional[str]:
    """Get the request-scoped conversation ID, or None if not set."""
    return _request_conversation_id.get()
