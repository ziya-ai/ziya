"""
Unified Session Context + effective-permissions prompt block.

Used by both the chat (precision_prompt_system) and task
(agents/task_executor) paths so the model receives a single
consistent description of:

  - the project root and current working directory
  - the merged effective write policy (base + task scope)
  - the merged effective readable extras (base + task scope)
  - the tool allowlist (if a task scope narrows it)
  - the shell-command grants (base safety + task scope)
  - the diff fallback for paths NOT in the writable set

Centralising this prevents the kind of mismatch that produced the
d3 task failure: the agent had no way to know it was allowed to
write under the project, so it gave up after a single permission
denial and switched to a workaround renderer.

The helper takes the project root and an optional ``task_scope``
(a ``TaskScope`` model or any duck-typed object exposing
``paths``, ``tools``, ``skills``, ``shell_commands``).  It returns
a string ready to append to the system message.

The function never raises: if any subsystem is unavailable
(write-policy manager not initialised, MCP off, etc.) it skips
that section silently and emits whatever it can.
"""

from __future__ import annotations

import datetime
import os
from typing import Any, List, Optional


def _safe_get_effective_policy() -> dict:
    """Return the merged write policy or an empty dict on any error."""
    try:
        from app.config.write_policy import get_write_policy_manager
        return get_write_policy_manager().get_effective_policy() or {}
    except Exception:
        return {}


def _format_writable_section(
    base_policy: dict,
    task_scope: Optional[Any],
) -> List[str]:
    """Build the 'Writable paths' subsection.

    Combines the base policy's ``safe_write_paths`` and
    ``allowed_write_patterns`` with any task-scope ``paths`` entries
    that have ``write=True``.  Each line is attributed so the model
    can tell what comes from where.
    """
    lines: List[str] = []
    safe_paths = [p for p in base_policy.get("safe_write_paths", []) or []
                  if p not in ("/dev/null",)]
    write_patterns = [p for p in base_policy.get("allowed_write_patterns", []) or [] if p]
    direct_mode = base_policy.get("direct_write_mode", "none")

    # Task-scope writable grants (write=True entries).  May include
    # absolute paths outside the project root — those are additive.
    scope_writes: List[str] = []
    if task_scope is not None:
        for entry in (getattr(task_scope, "paths", None) or []):
            if not getattr(entry, "write", False):
                continue
            p = getattr(entry, "path", None)
            if p:
                scope_writes.append(p)

    if not safe_paths and not write_patterns and not scope_writes and direct_mode == "none":
        return lines

    lines.append("### Writable paths (effective)")

    if safe_paths:
        lines.append(
            "  - Project policy — always writable: "
            + ", ".join(f"`{p}`" for p in safe_paths)
        )
    if write_patterns:
        lines.append(
            "  - Project policy — patterns allowed for direct write: "
            + ", ".join(f"`{p}`" for p in write_patterns)
        )
    if direct_mode == "all_files":
        lines.append("  - Project policy — direct_write_mode=`all_files`: any file in scope may be written directly")
    elif direct_mode == "new_files":
        lines.append("  - Project policy — direct_write_mode=`new_files`: NEW files anywhere in project may be written directly; modifying existing files still requires a diff")

    if scope_writes:
        lines.append(
            "  - Task scope grants (additive): "
            + ", ".join(f"`{p}`" for p in scope_writes)
        )

    lines.append("  - All other paths must be modified via a git diff in the response, NOT via file_write.")
    return lines


def _format_readable_section(task_scope: Optional[Any]) -> List[str]:
    """Build the 'Readable paths (task scope)' subsection.

    Lists every task-scope ``paths`` entry — both in-project and
    out-of-project — so the agent has a single place to check what
    was explicitly placed in scope.  In-project entries are
    redundant with the normal project-read default but are listed
    for clarity (the user explicitly chose them).
    """
    lines: List[str] = []
    if task_scope is None:
        return lines
    entries = getattr(task_scope, "paths", None) or []
    if not entries:
        return lines

    in_project: List[str] = []
    out_of_project: List[str] = []
    for entry in entries:
        p = getattr(entry, "path", None)
        if not p:
            continue
        # Permission flags: read OR write (write implies read).
        if not (getattr(entry, "read", False) or getattr(entry, "write", False)):
            continue
        if os.path.isabs(p):
            out_of_project.append(p)
        else:
            in_project.append(p)

    if not in_project and not out_of_project:
        return lines

    lines.append("### Readable paths (task scope)")
    if in_project:
        lines.append(
            "  - In-project: "
            + ", ".join(f"`{p}`" for p in in_project)
        )
    if out_of_project:
        lines.append(
            "  - Out-of-project (additive): "
            + ", ".join(f"`{p}`" for p in out_of_project)
        )
    return lines


def _format_tools_section(task_scope: Optional[Any]) -> List[str]:
    """Build the 'Allowed tools' subsection.

    Only emitted when the task scope narrows the tool list.  The
    chat path doesn't filter tools, so for chat this is a no-op.
    """
    if task_scope is None:
        return []
    tools = list(getattr(task_scope, "tools", None) or [])
    if not tools:
        return []
    lines = ["### Allowed tools (task scope)"]
    lines.append("  - " + ", ".join(f"`{t}`" for t in sorted(tools)))
    lines.append("  - All other tools are filtered out of this run.")
    return lines


def _format_skills_section(task_scope: Optional[Any]) -> List[str]:
    """Build the 'Allowed skills' subsection.

    Only emitted when the task scope narrows the skill list.  Note
    that this lists ids — the actual skill prompt bodies are loaded
    separately by the executor.
    """
    if task_scope is None:
        return []
    skills = list(getattr(task_scope, "skills", None) or [])
    if not skills:
        return []
    lines = ["### Allowed skills (task scope)"]
    lines.append("  - " + ", ".join(f"`{s}`" for s in sorted(skills)))
    return lines


def _format_shell_section(task_scope: Optional[Any]) -> List[str]:
    """Build the 'Shell command grants' subsection.

    The base shell policy is enforced by ShellWriteChecker and is
    too dense to dump in a system prompt.  We only surface the
    task-scope additive grants here (literal entries and ``re:``
    regex entries are clearly distinguished).
    """
    if task_scope is None:
        return []
    grants = list(getattr(task_scope, "shell_commands", None) or [])
    if not grants:
        return []
    literals: List[str] = []
    regexes: List[str] = []
    for g in grants:
        if g.startswith("re:"):
            regexes.append(g[3:])
        else:
            literals.append(g)
    lines = ["### Shell command grants (task scope, additive)"]
    if literals:
        lines.append(
            "  - Literal first-token grants: "
            + ", ".join(f"`{c}`" for c in literals)
        )
    if regexes:
        lines.append(
            "  - Regex grants (matched against full command line): "
            + ", ".join(f"`{r}`" for r in regexes)
        )
    lines.append(
        "  - These bypass the base shell allowlist and destructive-command "
        "block, but never override `always_blocked` (sudo, editors, etc.) "
        "or redirection blocking."
    )
    return lines


def build_session_context_section(
    project_root: Optional[str] = None,
    task_scope: Optional[Any] = None,
    cwd: Optional[str] = None,
    now: Optional[datetime.datetime] = None,
    conv_start_iso: Optional[str] = None,
) -> str:
    """Assemble the full Session Context + effective-permissions block.

    Parameters
    ----------
    project_root:
        Absolute path to the resolved project root, or ``None``
        when no project context is available.
    task_scope:
        ``TaskScope`` (or duck-typed equivalent) when called from
        the task executor; ``None`` for normal chat.
    cwd:
        Effective working directory.  Defaults to ``project_root``
        when set, else ``os.getcwd()``.
    now:
        Wall-clock time for the ``CurrentDateTime`` line.  Override
        for deterministic tests.
    conv_start_iso:
        Optional pre-formatted conversation-start timestamp.

    Returns
    -------
    Multi-line string starting with a leading ``\\n\\n`` so callers
    can append it directly to an existing system prompt.  Empty
    string if every subsection turns out empty (extremely unlikely
    — at minimum the date/cwd line is always included).
    """
    if now is None:
        now = datetime.datetime.now()
    if cwd is None:
        cwd = project_root or os.getcwd()

    out: List[str] = ["", "", "## Session Context"]
    if project_root:
        out.append(f'<CurrentProjectRoot value="{project_root}" />')
    out.append(f'<CurrentWorkingDirectory value="{cwd}" />')
    out.append(f'<CurrentDateTime value="{now.strftime("%Y-%m-%d %H:%M:%S").strip()}" />')
    if conv_start_iso:
        out.append(f'<ConversationStartTime value="{conv_start_iso}" />')

    base_policy = _safe_get_effective_policy()
    sections: List[List[str]] = [
        _format_writable_section(base_policy, task_scope),
        _format_readable_section(task_scope),
        _format_tools_section(task_scope),
        _format_skills_section(task_scope),
        _format_shell_section(task_scope),
    ]
    for sec in sections:
        if sec:
            out.append("")
            out.extend(sec)

    return "\n".join(out)
