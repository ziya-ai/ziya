"""Deterministic exit gate for an Until block (``until_mode="expression"``).

In expression mode ``until_condition`` is a shell command.  After each
iteration of the body the command runs in the block's working directory;
exit status 0 means the condition is satisfied and the loop stops, any
other status means it is not and the loop continues (bounded by
``until_max``).  Nothing is judged by a model: ``pytest -q`` either passes
or it does not.  The command's trailing output is returned as the
rationale, so the next iteration's ``{{until.last_rationale}}`` carries
the failing test names or lint lines the body needs to act on.

Policy.  The command is adjudicated by the same code the shell tool and
the job runner use (``app.jobs.policy.check`` over ``ShellServer``), with
the Until block's own signed scope as the task-scope envelope.  A refused
command is reported as ``refused`` so the loop stops at once instead of
spending ``until_max`` iterations on a gate that can never pass; nothing
here can widen the allowlist.  The child environment is the
credential-stripped one every MCP subprocess gets.
"""
from __future__ import annotations

import asyncio
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from app.utils import policy_exec
from app.utils.logging_utils import logger

# Bound on how long one gate run may take when the block's scope sets no
# ``shell_timeout_secs``.  A test suite that needs longer should say so.
DEFAULT_TIMEOUT_SECS = 600
# How much of the command's output is handed back as the rationale.  It is
# bound into the next iteration's instructions, so it must be enough to
# name the failures and small enough not to crowd them out.
RATIONALE_TAIL_CHARS = 4000


@dataclass
class GateResult:
    satisfied: bool
    rationale: str = ""
    exit_code: Optional[int] = None
    # The gate cannot pass without changing the card: the command was
    # refused by the shell policy, could not start, or was empty.  The
    # loop stops on this rather than retrying.
    refused: bool = False
    timed_out: bool = False


def gate_cwd(project_root: str, scope_cwd: Optional[str]) -> str:
    """Resolve ``scope.cwd`` the way the task executor does: relative to
    the project root, confined to it, falling back to the root when the
    directory is missing or escapes."""
    if not scope_cwd:
        return project_root
    try:
        base = Path(project_root).resolve()
        cand = (base / scope_cwd).resolve()
        cand.relative_to(base)
        if cand.is_dir():
            return str(cand)
        logger.warning(f"until gate: cwd {scope_cwd!r} not found; using project root")
    except ValueError:
        logger.warning(f"until gate: cwd {scope_cwd!r} escapes project root; using project root")
    return project_root


# Module-level names so a test can stand in for the policy or the process
# (``patch.object(until_gate, "_adjudicate", ...)``).
def _adjudicate(command: str, *, project_root: str,
                shell_commands: Optional[List[str]]) -> Tuple[bool, str, Dict[str, str]]:
    return policy_exec.adjudicate(command, project_root=project_root, shell_commands=shell_commands)


def _child_env(shell_env: Dict[str, str], *, project_root: str, cwd: str) -> Dict[str, str]:
    return policy_exec.child_env(shell_env, project_root=project_root, cwd=cwd)


def _run(command: str, cwd: str, env: Dict[str, str], timeout: float) -> subprocess.CompletedProcess:
    return policy_exec.run_sh(command, cwd, env, timeout)


def _tail(cp: subprocess.CompletedProcess) -> str:
    return policy_exec.output_tail(cp, RATIONALE_TAIL_CHARS)


async def evaluate_command_gate(
    command: str, *,
    project_root: Optional[str],
    cwd: Optional[str] = None,
    shell_commands: Optional[List[str]] = None,
    timeout_secs: Optional[float] = None,
) -> GateResult:
    """Run the gate command once and report whether it passed."""
    cmd = (command or "").strip()
    if not cmd:
        return GateResult(False, "expression mode needs a shell command in until_condition", refused=True)
    root = project_root or os.getcwd()
    workdir = cwd or root
    timeout = float(timeout_secs or DEFAULT_TIMEOUT_SECS)

    try:
        allowed, why, shell_env = _adjudicate(cmd, project_root=root, shell_commands=shell_commands)
    except Exception as e:  # noqa: BLE001 -- an unadjudicated command never runs
        logger.warning(f"until gate: policy check failed for {cmd!r}: {e}")
        return GateResult(False, f"gate command could not be checked against the shell policy: {e}", refused=True)
    if not allowed:
        return GateResult(False, f"gate command refused by the shell policy: {why}", refused=True)

    env = _child_env(shell_env, project_root=root, cwd=workdir)
    try:
        cp = await asyncio.to_thread(_run, cmd, workdir, env, timeout)
    except subprocess.TimeoutExpired:
        return GateResult(False, f"gate command timed out after {int(timeout)} s", timed_out=True)
    except OSError as e:
        return GateResult(False, f"gate command could not start: {e}", refused=True)

    tail = _tail(cp)
    if cp.returncode == 0:
        return GateResult(True, "", exit_code=0)
    rationale = f"gate command exited {cp.returncode}"
    if tail:
        rationale += f":\n{tail}"
    return GateResult(False, rationale, exit_code=cp.returncode)
