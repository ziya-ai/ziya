"""
Task loader for `ziya task`.

A task is a named prompt with optional permission escalation.
Loading merges three sources (last wins):
  1. Built-in tasks (app/config/builtin_tasks.py)
  2. User global (~/.ziya/tasks.yaml or tasks.json)
  3. Project-local (.ziya/tasks.yaml or tasks.json)

See Docs/CLITasks.md for the `allow` field specification.
"""

import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Dict, Optional

# Commands that can never be escalated, regardless of task permissions
ALWAYS_BLOCKED = {"sudo", "su", "systemctl", "service", "nano", "vim", "vi", "emacs"}

VALID_ALLOW_KEYS = {"commands", "git_operations", "write_patterns"}

def _load_file(path: Path) -> Dict[str, Any]:
    """Load a .yaml/.yml or .json task file. Returns {} on missing/invalid."""
    if not path.exists():
        return {}
    text = path.read_text(encoding="utf-8")
    if not text.strip():
        return {}
    if path.suffix in (".yaml", ".yml"):
        try:
            import yaml
            return yaml.safe_load(text) or {}
        except ImportError:
            print(f"\033[33mWarning: PyYAML not installed, skipping {path}\033[0m",
                  file=sys.stderr)
            return {}
    return json.loads(text)


def _find_task_file(directory: Path) -> Optional[Path]:
    """Find tasks.yaml or tasks.json in a directory."""
    for name in ("tasks.yaml", "tasks.yml", "tasks.json"):
        candidate = directory / name
        if candidate.exists():
            return candidate
    return None


def load_tasks(root: str = None) -> Dict[str, Any]:
    """Load and merge task definitions. Merge order: builtin < global < project."""
    from app.config.builtin_tasks import BUILTIN_TASKS
    import copy

    tasks = copy.deepcopy(BUILTIN_TASKS)

    global_file = _find_task_file(Path.home() / ".ziya")
    if global_file:
        tasks.update(_load_file(global_file))

    project_root = Path(root) if root else Path.cwd()
    local_file = _find_task_file(project_root / ".ziya")
    if local_file:
        tasks.update(_load_file(local_file))

    return tasks


def resolve_task_source_file(name: str, root: str = None) -> Optional[Path]:
    """Return the tasks file that *defines* ``name``, honoring load_tasks order.

    ``load_tasks`` merges builtin < global < project and discards provenance, but
    the scope-approval store must key a CLI task on the realpath of the file that
    actually defined it (project overrides global). This recomputes that with the
    same precedence: the project-local file wins if it defines the task, else the
    global file if it does. Returns None for builtin-only tasks (they carry no
    ``allow`` block, so they never need an approval) or unknown names.
    """
    project_root = Path(root) if root else Path.cwd()
    local_file = _find_task_file(project_root / ".ziya")
    if local_file and name in (_load_file(local_file) or {}):
        return local_file
    global_file = _find_task_file(Path.home() / ".ziya")
    if global_file and name in (_load_file(global_file) or {}):
        return global_file
    return None
def validate_task_allow(task_def: Dict[str, Any]) -> list[str]:
    """Validate the ``allow`` block of a task definition.

    Returns a list of warning/error strings. Empty list means valid.
    """
    allow = task_def.get("allow")
    if allow is None:
        return []

    errors: list[str] = []

    if not isinstance(allow, dict):
        return ["`allow` must be a mapping"]

    unknown_keys = set(allow.keys()) - VALID_ALLOW_KEYS
    if unknown_keys:
        errors.append(f"Unknown allow keys: {', '.join(sorted(unknown_keys))}")

    for field in ("commands", "git_operations", "write_patterns"):
        val = allow.get(field)
        if val is not None and not isinstance(val, list):
            errors.append(f"`allow.{field}` must be a list")

    # Check for always-blocked commands
    for cmd in allow.get("commands", []):
        if cmd in ALWAYS_BLOCKED:
            errors.append(f"Cannot escalate always-blocked command: {cmd}")

    return errors


def apply_task_permissions(task_def: Dict[str, Any]) -> dict[str, str | None]:
    """Escalate shell permissions for the duration of a task.

    Reads the ``allow`` block from *task_def*, merges the requested
    escalations into the current environment variables consumed by the
    shell server, and returns a dict of ``{env_var: previous_value}``
    so the caller can restore state afterwards.

    Must be called **before** MCP servers are started (they read env at
    init time).
    """
    allow = task_def.get("allow")
    if not allow or not isinstance(allow, dict):
        return {}

    saved_env: dict[str, str | None] = {}

    # --- Additional allowed commands ---
    extra_commands = allow.get("commands", [])
    if extra_commands:
        # Filter out always-blocked
        extra_commands = [c for c in extra_commands if c not in ALWAYS_BLOCKED]
        existing = os.environ.get("ALLOW_COMMANDS", "")
        saved_env["ALLOW_COMMANDS"] = existing or None
        merged = set(c.strip() for c in existing.split(",") if c.strip())
        merged.update(extra_commands)
        os.environ["ALLOW_COMMANDS"] = ",".join(sorted(merged))

    # --- Additional safe git operations ---
    extra_git = allow.get("git_operations", [])
    if extra_git:
        existing = os.environ.get("SAFE_GIT_OPERATIONS", "")
        saved_env["SAFE_GIT_OPERATIONS"] = existing or None
        merged = set(op.strip() for op in existing.split(",") if op.strip())
        merged.update(extra_git)
        os.environ["SAFE_GIT_OPERATIONS"] = ",".join(sorted(merged))

    # --- Additional write patterns ---
    extra_patterns = allow.get("write_patterns", [])
    if extra_patterns:
        existing = os.environ.get("ALLOWED_WRITE_PATTERNS", "")
        saved_env["ALLOWED_WRITE_PATTERNS"] = existing or None
        merged = set(p.strip() for p in existing.split(",") if p.strip())
        merged.update(extra_patterns)
        os.environ["ALLOWED_WRITE_PATTERNS"] = ",".join(sorted(merged))

    return saved_env


def restore_permissions(saved_env: dict[str, str | None]) -> None:
    """Undo the env changes made by :func:`apply_task_permissions`."""
    for key, prev in saved_env.items():
        if prev is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = prev


def allow_to_task_scope(allow: Any) -> tuple[list[str], list[dict]]:
    """Project a CLI task ``allow`` block onto the ``_task_scope`` grant shape.

    Returns ``(shell_command_grants, writable_entries)`` for
    ``set_task_shell_commands`` / ``set_task_writable_paths`` respectively:

      • shell_command_grants — each ``commands`` entry as a literal first-token
        grant (so ``git`` grants any ``git`` subcommand), plus one ``re:`` grant
        per ``git_operations`` entry (``re:^git\\s+<op>(\\s|$)``) so a task that
        lists git ops *without* bare ``git`` is still scoped to exactly those.
      • writable_entries — each ``write_patterns`` glob as a ``{"pattern": ...}``
        entry (matched with fnmatch on both the project-relative path and its
        basename, mirroring WritePolicyManager.allowed_write_patterns).

    This is the half that lets a signed CLI-task approval actually reach the
    shell subprocess: the env path (``apply_task_permissions``) is clamped back
    to the floor by the F-004 signature gate, but the ``_task_scope`` envelope is
    consulted additively *after* that clamp and is never subject to it.
    ``always_blocked`` commands are dropped here too (defense in depth — the
    shell server re-enforces that ceiling regardless).
    """
    if not allow or not isinstance(allow, dict):
        return [], []
    cmds: list[str] = []
    for c in (allow.get("commands") or []):
        c = str(c).strip()
        if c and c not in ALWAYS_BLOCKED:
            cmds.append(c)
    for op in (allow.get("git_operations") or []):
        op = str(op).strip()
        if op:
            cmds.append(r"re:^git\s+" + re.escape(op) + r"(\s|$)")
    writable: list[dict] = []
    for pat in (allow.get("write_patterns") or []):
        pat = str(pat).strip()
        if pat:
            writable.append({"pattern": pat})
    return cmds, writable
