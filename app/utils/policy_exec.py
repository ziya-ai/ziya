"""Run one configured shell command under the shell tool's own policy.

Shared by the Until block's command gate (``app/agents/until_gate.py``) and
lifecycle hooks (``app/utils/lifecycle_hooks.py``): both execute a command
an author wrote into a card or a config file rather than one the model
asked for, and both must be adjudicated exactly as ``run_shell_command``
would be.  The verdict comes from ``app.jobs.policy.check`` -- the same
in-process ``ShellServer`` the job runner consults, over the same
environment the MCP manager would give the shell subprocess -- so the
allowlist, interpreter list, YOLO mode, signed escalations and session
grants all apply, and nothing here can widen them.  The child environment
is the credential-stripped one every MCP subprocess gets.
"""
from __future__ import annotations

import subprocess
from typing import Dict, List, Optional, Tuple


def adjudicate(command: str, *, project_root: str,
               shell_commands: Optional[List[str]] = None) -> Tuple[bool, str, Dict[str, str]]:
    """Ask the shell policy whether ``command`` may run with these grants.

    Returns ``(allowed, reason, shell_env)``.  ``shell_env`` is the
    environment the verdict was computed over; the child inherits it
    (after credential stripping) so the policy and the run agree.
    """
    from app.jobs import policy
    task_scope = None
    if shell_commands:
        task_scope = {
            "writable": [], "readable": [],
            "shell_commands": list(shell_commands),
            "shell_timeout_secs": None,
            "project_root": project_root,
        }
    decision, shell_env = policy.check(
        command, project_root=project_root, outputs=[], task_scope=task_scope,
    )
    return bool(decision.allowed), decision.reason or "", dict(shell_env or {})


def child_env(shell_env: Dict[str, str], *, project_root: str, cwd: str,
              extra: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    """Credential-stripped environment for the command's process."""
    from app.jobs.policy import _strip_escalation_keys
    from app.mcp.client import build_mcp_subprocess_env
    env = _strip_escalation_keys(build_mcp_subprocess_env({}, base_env=shell_env))
    env["ZIYA_USER_CODEBASE_DIR"] = project_root
    env["PWD"] = cwd
    if extra:
        env.update({k: str(v) for k, v in extra.items() if v is not None})
    return env


def run_sh(command: str, cwd: str, env: Dict[str, str], timeout: float,
           stdin_text: Optional[str] = None) -> subprocess.CompletedProcess:
    """``sh -c command`` with captured output.  Raises ``TimeoutExpired``
    and ``OSError`` to the caller, which decides what each means."""
    return subprocess.run(
        ["sh", "-c", command], cwd=cwd, env=env,
        input=(stdin_text.encode("utf-8") if stdin_text is not None else None),
        capture_output=True, timeout=timeout,
    )


def output_tail(cp: subprocess.CompletedProcess, limit: int) -> str:
    """stdout then stderr, decoded, trimmed to the last ``limit`` chars."""
    chunks = []
    for raw in (cp.stdout, cp.stderr):
        if raw:
            chunks.append(raw.decode("utf-8", errors="replace") if isinstance(raw, bytes) else str(raw))
    text = "\n".join(c.strip() for c in chunks if c.strip()).strip()
    if len(text) > limit:
        text = "…" + text[-limit:]
    return text
