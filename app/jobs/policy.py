"""All-or-nothing policy pre-check for ``job_start`` (design/job-runner.md, Policy).

Security model, stated once:

* The verdict comes from the SAME code the shell tool runs —
  ``ShellServer.is_command_allowed`` and ``ShellWriteChecker.check`` —
  constructed over the SAME environment the MCP manager would hand the
  shell subprocess (its configured env plus the escalation overlay:
  signed deltas, session nonce, session grants).  So the allowlist, the
  interpreter list, YOLO, signed escalations and ephemeral grants all
  apply exactly as they do for ``run_shell_command``, and nothing here can
  widen them: an unsigned escalation in that env is clamped to the floor
  by the shell server's own gate, as it is in the subprocess.
* The task-scope grant envelope is the one ``MCPManager.call_tool`` builds
  for the shell server, applied the same way (allowlist check and write
  check both see it).
* Background operators are refused on top of all that.  The runner is the
  only sanctioned way to background; an unmanaged ``&``/``nohup`` child
  would escape ``job_kill`` and the process-group discipline.
* Declared outputs must be writable under the effective policy
  (base policy or task grant) and never inside a server-only path.
* Refusal at any step means nothing is created and nothing runs.

The child environment is built here too, from the same shell-subprocess
env: credential-stripped by ``build_mcp_subprocess_env`` exactly as the
shell tool's children are (their parent IS that subprocess).  Spawning
from the main server with ``os.environ`` would hand the job the server's
AWS credentials — a floor the shell tool never had.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import threading
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from app.utils.logging_utils import logger

# Operators and launchers that detach a process from the tree the runner
# tracks.  Checked per segment, after quote-aware splitting, so a literal
# ``&`` inside a quoted argument is not mistaken for backgrounding.
_BACKGROUND_LAUNCHERS = ("nohup", "setsid", "disown", "daemon", "daemonize")
_TRAILING_AMP = re.compile(r"(?<![&>|])&\s*$")


@dataclass
class PolicyDecision:
    allowed: bool
    reason: str = ""
    # Audit record stored in job.json: what was consulted, not what was granted.
    segments: List[str] = field(default_factory=list)
    task_scope_used: bool = False
    yolo: bool = False
    policy_fingerprint: str = ""
    clamp_notice: Optional[str] = None


class _ShellPolicy:
    """One in-process ``ShellServer`` per distinct shell-subprocess env.

    Rebuilt when the env the manager would spawn with changes (a new
    session grant, a shell-config edit), so a verdict is never computed
    over a stale grant set.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._server = None
        self._env_key: Optional[str] = None
        self._env: Dict[str, str] = {}

    def shell_env(self) -> Dict[str, str]:
        """The env the manager would give the shell subprocess, right now."""
        from app.mcp.manager import get_mcp_manager
        mgr = get_mcp_manager()
        cfg = (getattr(mgr, "server_configs", {}) or {}).get("shell") or {}
        env = os.environ.copy()
        overrides = cfg.get("env")
        if isinstance(overrides, dict):
            env.update(overrides)
        apply = getattr(mgr, "_apply_escalation_overlay", None)
        if callable(apply):
            apply(env, "shell")
        return env

    def server(self):
        env = self.shell_env()
        key = hashlib.sha256(json.dumps(sorted(env.items())).encode()).hexdigest()
        with self._lock:
            if self._server is None or key != self._env_key:
                from app.mcp_servers.shell_server import ShellServer
                self._server = ShellServer(env=env)
                self._env_key, self._env = key, env
            return self._server, dict(self._env), key


_POLICY = _ShellPolicy()


def _strip_escalation_keys(env: Dict[str, str]) -> Dict[str, str]:
    """Signatures, grants and nonces authorise the shell subprocess; a job's
    command has no use for them.  Dropping them cannot widen anything."""
    from app.config.scope_canonical import (
        SIG_ENV_KEY, SESSION_GRANT_ENV_KEY, SESSION_NONCE_ENV_KEY, EPHEMERAL_PUBKEY_ENV_KEY,
    )
    drop = {SIG_ENV_KEY, SESSION_GRANT_ENV_KEY, SESSION_NONCE_ENV_KEY, EPHEMERAL_PUBKEY_ENV_KEY}
    return {k: v for k, v in env.items() if k not in drop and not k.startswith("Malloc")}


def child_env(shell_env: Dict[str, str], *, project_root: str, cwd: str, job_id: str,
              conversation_id: Optional[str]) -> Dict[str, str]:
    """Environment for the job's process tree.

    Credential-stripped like every MCP subprocess (ASR F-003), plus the
    job id and the evidence-ledger scope so ``ziya ledger`` / ``ziya data``
    inside the job are attributed to the owning conversation or block.
    """
    from app.mcp.client import build_mcp_subprocess_env
    env = _strip_escalation_keys(build_mcp_subprocess_env({}, base_env=shell_env))
    env["ZIYA_USER_CODEBASE_DIR"] = project_root
    env["PWD"] = cwd
    env["ZIYA_JOB_ID"] = job_id
    try:
        from app.utils.evidence.scope import build_envelope
        scope = build_envelope(project_root, conversation_id)
        if scope:
            env["ZIYA_LEDGER_URL"] = str(scope["url"])
            env["ZIYA_LEDGER_SCOPE"] = str(scope["token"])
    except Exception:  # noqa: BLE001 -- attribution is best effort
        pass
    return env


def _background_refusal(server, command: str) -> Optional[str]:
    try:
        segments = server._split_by_shell_operators(command)
    except Exception:  # noqa: BLE001 -- the allowlist check reports the parse failure
        return None
    for seg in segments:
        # Tuples are (operator, command); the command is the second element.
        text = (seg[1] if isinstance(seg, tuple) else seg) or ""
        s = text.strip()
        if not s:
            continue
        if _TRAILING_AMP.search(s):
            return ("background operator '&' is not allowed inside a job; the job "
                    "runner is the only sanctioned way to background a command")
        head = s.split(None, 1)[0]
        if head in _BACKGROUND_LAUNCHERS:
            return (f"'{head}' detaches a process the runner could not track or kill; "
                    f"start it as the job's command instead")
    if "&" in command and _TRAILING_AMP.search(command.strip()):
        return "background operator '&' is not allowed inside a job"
    return None


def check(command: str, *, project_root: str, outputs: List[str],
          task_scope: Optional[Dict[str, Any]]) -> Tuple[PolicyDecision, Dict[str, str]]:
    """Adjudicate a job before anything is created.  Returns the decision and
    the shell-subprocess env the decision was made over (for ``child_env``)."""
    if not command or not command.strip():
        return PolicyDecision(False, "Empty command"), {}
    server, shell_env, key = _POLICY.server()
    decision = PolicyDecision(False, policy_fingerprint=key[:16],
                              clamp_notice=getattr(server, "_scope_clamp_notice", None),
                              yolo=bool(getattr(server, "yolo_mode", False)),
                              task_scope_used=bool(task_scope))

    bg = _background_refusal(server, command)
    if bg:
        decision.reason = bg
        return decision, shell_env

    server.write_checker.set_task_scope(task_scope)
    server.write_checker.set_project_root(project_root)
    try:
        ok, why = server.is_command_allowed(command)
        if not ok:
            decision.reason = f"command refused: {why}"
            return decision, shell_env
        if not server.yolo_mode:
            ok, why = server.write_checker.check(command, server._split_by_shell_operators)
            if not ok:
                decision.reason = f"write refused: {why}"
                return decision, shell_env
        for out in outputs:
            target = out if os.path.isabs(out) else os.path.join(project_root, out)
            target = os.path.realpath(target.rstrip("/")) if target.rstrip("/") else target
            if server.wp_manager.is_server_only_path(target, project_root):
                decision.reason = f"declared output {out!r} is inside a server-only path"
                return decision, shell_env
            if not (server.wp_manager.is_write_allowed(target, project_root)
                    or server.write_checker._task_scope_grants_write(target)):
                decision.reason = (f"declared output {out!r} is not writable under the "
                                   f"effective write policy")
                return decision, shell_env
    finally:
        server.write_checker.clear_task_scope()
        server.write_checker.clear_project_root()

    try:
        decision.segments = [
            (s[1] if isinstance(s, tuple) else s) for s in server._split_by_shell_operators(command)
        ]
    except Exception:  # noqa: BLE001
        decision.segments = [command]
    decision.allowed = True
    return decision, shell_env
