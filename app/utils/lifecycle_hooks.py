"""Lifecycle hooks: shell commands a project runs at fixed points in a turn.

Configured in ``<project>/.ziya/hooks.yaml``::

    hooks:
      post_apply:                 # a project file was written by Ziya
        - run: "ruff check {file}"
          match: "*.py"           # fnmatch on the file's project-relative path
          await: true             # attach exit status + output to the tool result
          timeout: 60
      post_tool:                  # any tool call finished
        - run: "echo {tool} >> .ziya/tool-audit.log"
          tools: [run_shell_command, file_write]
      run_finished:               # a Task Card run reached a terminal status
        - run: 'curl -s -X POST "$ZIYA_NOTIFY_URL" -H content-type:application/json -d @-'
          status: [done, failed, partial, held]

Three events, deliberately few:

``post_apply``
    fires after the diff applier (``apply_diff_pipeline``) or the
    ``file_write`` tool lands a change on disk.  ``{file}`` is the path.
``post_tool``
    fires after a tool call returns.  ``{tool}`` is the tool name; the
    event payload carries whether it succeeded.
``run_finished``
    fires when a Task Card run ends (``done`` / ``partial`` / ``failed``
    / ``cancelled`` / ``held``), with the run id, status and card name.
    This is the notification point: nothing in Ziya waits on it.
``pre_tool``
    fires BEFORE a tool call runs and can veto it.  Always awaited.  Exit
    0 lets the call proceed; exit 2 refuses it and the hook's output is
    the reason the model is given.  Any other exit status is a hook
    error: the call proceeds (``on_error: allow``, the default) or is
    refused (``on_error: deny``), and the error is logged either way.
    Runs before the consent gate, so a project policy settles a call
    before a human is asked about it.  Hooks cannot rewrite arguments.
``session_start``
    fires once per conversation in a project (keyed on the conversation's
    start time; once per process when there is none), as the first turn's
    system prompt is built.  Always awaited.  Its stdout, up to
    ``SESSION_NOTES_CHARS``, is added to the Session Context block as
    *Session notes*, so a ``git status --short`` or an on-call note
    reaches the model without being pasted.  A failing or refused hook
    adds nothing.

Every hook gets the event as JSON on stdin and as ``ZIYA_HOOK_*``
variables; ``{placeholders}`` in ``run`` are shell-quoted before
substitution.  Hooks are fire-and-forget by default.  ``await: true``
runs the hook before the tool result is returned and appends its exit
status and output to that result, which is how "lint after every edit
and tell the model" works; it is honoured for ``post_apply`` and
``post_tool`` only.

Policy.  A hook command is adjudicated by the shell tool's own policy
(``app/utils/policy_exec.py``): a command the model could not run, a hook
cannot run either, and a refusal is logged rather than executed.  A
``hooks.yaml`` in a cloned repository therefore has exactly the reach the
model's shell tool has in that project, no more.  The child environment
is credential-stripped like every other subprocess
(``ZIYA_MCP_ENV_PASSTHROUGH`` forces named variables through).
``ZIYA_HOOKS=0`` disables hooks entirely.

Every hook run, refusal and failure is appended to
``<project>/.ziya/hooks.log`` as one JSON line.
"""
from __future__ import annotations

import fnmatch
import json
import os
import shlex
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.utils import policy_exec
from app.utils.logging_utils import logger

EVENTS = ("post_apply", "post_tool", "run_finished", "pre_tool", "session_start")
# Events whose result is consumed, so ``await`` is implied and cannot be
# turned off.
ALWAYS_AWAITED = frozenset({"pre_tool", "session_start"})
# Exit status by which a pre_tool hook refuses the call.
VETO_EXIT = 2
SESSION_NOTES_CHARS = 4000
CONFIG_RELPATH = os.path.join(".ziya", "hooks.yaml")
LOG_RELPATH = os.path.join(".ziya", "hooks.log")
DEFAULT_TIMEOUT_SECS = 60
MAX_TIMEOUT_SECS = 900
# How much hook output is attached to a tool result.  It sits in the
# model's context, so it is kept to what a lint or test run needs.
FEEDBACK_TAIL_CHARS = 3000
# Fire-and-forget hooks in flight at once; beyond this a hook is dropped
# and logged, so a hook storm cannot fork without bound.
MAX_BACKGROUND = 8


@dataclass
class Hook:
    event: str
    run: str
    await_result: bool = False
    timeout: float = DEFAULT_TIMEOUT_SECS
    match: Optional[str] = None          # post_apply: fnmatch on relative path
    tools: List[str] = field(default_factory=list)   # post_tool: tool-name filter
    statuses: List[str] = field(default_factory=list)  # run_finished: status filter
    name: str = ""
    # pre_tool: what a hook ERROR (not a veto) means for the call.
    on_error: str = "allow"

    def applies_to(self, payload: Dict[str, Any]) -> bool:
        if self.event == "post_apply" and self.match:
            rel = str(payload.get("file_rel") or payload.get("file") or "")
            if not (fnmatch.fnmatch(rel, self.match) or fnmatch.fnmatch(os.path.basename(rel), self.match)):
                return False
        if self.event in ("post_tool", "pre_tool") and self.tools:
            if str(payload.get("tool") or "") not in self.tools:
                return False
        if self.event == "run_finished" and self.statuses:
            if str(payload.get("status") or "") not in self.statuses:
                return False
        return True


@dataclass
class HookOutcome:
    hook: Hook
    ran: bool
    exit_code: Optional[int] = None
    output: str = ""
    error: str = ""          # refusal / timeout / spawn failure
    duration_ms: int = 0

    @property
    def ok(self) -> bool:
        return self.ran and self.exit_code == 0


# ----------------------------------------------------------------- config


_cache_lock = threading.Lock()
_cache: Dict[str, Any] = {}   # project_root -> (mtime, [Hook])


def enabled() -> bool:
    return os.environ.get("ZIYA_HOOKS", "1").strip().lower() not in ("0", "false", "no", "off")


def _parse(raw: Any, errors: List[str]) -> List[Hook]:
    hooks: List[Hook] = []
    table = raw.get("hooks") if isinstance(raw, dict) else None
    if not isinstance(table, dict):
        if raw not in (None, {}):
            errors.append("hooks.yaml: expected a top-level 'hooks:' mapping")
        return hooks
    for event, entries in table.items():
        if event not in EVENTS:
            errors.append(f"hooks.yaml: unknown event {event!r} (known: {', '.join(EVENTS)})")
            continue
        if not isinstance(entries, list):
            errors.append(f"hooks.yaml: {event} must be a list of hooks")
            continue
        for n, e in enumerate(entries):
            if isinstance(e, str):
                e = {"run": e}
            if not isinstance(e, dict) or not str(e.get("run") or "").strip():
                errors.append(f"hooks.yaml: {event}[{n}] has no 'run' command")
                continue
            try:
                timeout = float(e.get("timeout") or DEFAULT_TIMEOUT_SECS)
            except (TypeError, ValueError):
                errors.append(f"hooks.yaml: {event}[{n}] timeout is not a number")
                timeout = DEFAULT_TIMEOUT_SECS
            timeout = max(1.0, min(timeout, MAX_TIMEOUT_SECS))
            tools = e.get("tools") or []
            statuses = e.get("status") or e.get("statuses") or []
            on_error = str(e.get("on_error") or "allow").lower()
            if on_error not in ("allow", "deny"):
                errors.append(f"hooks.yaml: {event}[{n}] on_error must be allow or deny")
                on_error = "allow"
            hooks.append(Hook(
                event=event, run=str(e["run"]).strip(),
                await_result=(event in ALWAYS_AWAITED
                              or (bool(e.get("await", False)) and event != "run_finished")),
                on_error=on_error,
                timeout=timeout,
                match=(str(e["match"]) if e.get("match") else None),
                tools=[str(t) for t in (tools if isinstance(tools, list) else [tools])],
                statuses=[str(s) for s in (statuses if isinstance(statuses, list) else [statuses])],
                name=str(e.get("name") or f"{event}[{n}]"),
            ))
    return hooks


def load_hooks(project_root: str) -> List[Hook]:
    """Hooks for ``project_root``; cached by the file's mtime."""
    if not project_root or not enabled():
        return []
    path = os.path.join(project_root, CONFIG_RELPATH)
    try:
        mtime = os.stat(path).st_mtime
    except OSError:
        with _cache_lock:
            _cache.pop(project_root, None)
        return []
    with _cache_lock:
        hit = _cache.get(project_root)
        if hit and hit[0] == mtime:
            return list(hit[1])
    errors: List[str] = []
    try:
        import yaml
        with open(path, "r", encoding="utf-8") as fh:
            raw = yaml.safe_load(fh) or {}
        hooks = _parse(raw, errors)
    except Exception as e:  # noqa: BLE001 -- a bad config disables hooks, loudly
        errors.append(f"hooks.yaml: could not be read: {e}")
        hooks = []
    for msg in errors:
        logger.warning(f"🪝 {msg}")
        _log(project_root, {"event": "config_error", "message": msg})
    with _cache_lock:
        _cache[project_root] = (mtime, list(hooks))
    return hooks


# ------------------------------------------------------------------ firing


_log_lock = threading.Lock()


def _log(project_root: str, record: Dict[str, Any]) -> None:
    try:
        path = Path(project_root) / LOG_RELPATH
        path.parent.mkdir(parents=True, exist_ok=True)
        record = {"at": time.time(), **record}
        with _log_lock, open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, default=str) + "\n")
    except Exception:  # noqa: BLE001 -- the log is best effort
        pass


def _render(command: str, payload: Dict[str, Any]) -> str:
    """Substitute ``{name}`` placeholders with shell-quoted payload values.
    Unknown placeholders are left as written so a literal brace in a
    command (``awk '{print}'``) survives."""
    out = command
    for key, val in payload.items():
        token = "{" + key + "}"
        if token in out and not isinstance(val, (dict, list)):
            out = out.replace(token, shlex.quote("" if val is None else str(val)))
    return out


def _hook_env(payload: Dict[str, Any]) -> Dict[str, str]:
    env = {}
    for key, val in payload.items():
        if isinstance(val, (dict, list)):
            continue
        env["ZIYA_HOOK_" + key.upper()] = "" if val is None else str(val)
    return env


_inflight = threading.Semaphore(MAX_BACKGROUND)


def _execute(hook: Hook, payload: Dict[str, Any], project_root: str) -> HookOutcome:
    started = time.time()
    command = _render(hook.run, payload)
    try:
        allowed, why, shell_env = policy_exec.adjudicate(command, project_root=project_root)
    except Exception as e:  # noqa: BLE001 -- unadjudicated means not run
        out = HookOutcome(hook, ran=False, error=f"policy check failed: {e}")
        _log(project_root, {"event": hook.event, "hook": hook.name, "command": command,
                            "refused": out.error})
        return out
    if not allowed:
        out = HookOutcome(hook, ran=False, error=f"refused by the shell policy: {why}")
        logger.warning(f"🪝 hook {hook.name} refused: {why}")
        _log(project_root, {"event": hook.event, "hook": hook.name, "command": command,
                            "refused": why})
        return out
    env = policy_exec.child_env(shell_env, project_root=project_root, cwd=project_root,
                                extra={"ZIYA_HOOK_EVENT": hook.event, **_hook_env(payload)})
    stdin_text = json.dumps({"event": hook.event, **payload}, default=str)
    try:
        cp = policy_exec.run_sh(command, project_root, env, hook.timeout, stdin_text=stdin_text)
        out = HookOutcome(hook, ran=True, exit_code=cp.returncode,
                          output=policy_exec.output_tail(cp, FEEDBACK_TAIL_CHARS))
    except subprocess.TimeoutExpired:
        out = HookOutcome(hook, ran=False, error=f"timed out after {int(hook.timeout)} s")
    except OSError as e:
        out = HookOutcome(hook, ran=False, error=f"could not start: {e}")
    out.duration_ms = int((time.time() - started) * 1000)
    _log(project_root, {
        "event": hook.event, "hook": hook.name, "command": command,
        "exit_code": out.exit_code, "error": out.error or None,
        "duration_ms": out.duration_ms, "awaited": hook.await_result,
        "output_tail": out.output[-400:] if out.output else "",
    })
    return out


def _background(hook: Hook, payload: Dict[str, Any], project_root: str) -> None:
    if not _inflight.acquire(blocking=False):
        logger.warning(f"🪝 hook {hook.name} dropped: {MAX_BACKGROUND} hooks already running")
        _log(project_root, {"event": hook.event, "hook": hook.name, "dropped": "too many in flight"})
        return

    def _go() -> None:
        try:
            _execute(hook, payload, project_root)
        finally:
            _inflight.release()

    threading.Thread(target=_go, name=f"ziya-hook-{hook.event}", daemon=True).start()


def _safe_args(args: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Model arguments minus the routing keys the executor adds."""
    return {k: v for k, v in dict(args or {}).items() if not str(k).startswith("_")}


async def before_tool(tool_name: str, args: Optional[Dict[str, Any]], *,
                      project_root: Optional[str]) -> Optional[str]:
    """``pre_tool`` hooks.  Returns the refusal text when a hook vetoes the
    call (exit 2, or an error under ``on_error: deny``), else ``None``.
    A hook failure under the default ``on_error: allow`` never blocks."""
    if not project_root or not enabled():
        return None
    import asyncio
    try:
        outcomes = await asyncio.to_thread(
            fire, "pre_tool", {"tool": tool_name, "args": _safe_args(args)},
            project_root=project_root,
        )
    except Exception as e:  # noqa: BLE001 -- the gate must not take the turn down
        logger.warning(f"🪝 pre_tool hooks for {tool_name} failed: {e}")
        return None
    for o in outcomes:
        if o.ran and o.exit_code == 0:
            continue
        if o.ran and o.exit_code == VETO_EXIT:
            why = o.output or "no reason given"
            return (f"Tool call refused by the project's `pre_tool` hook "
                    f"`{o.hook.name}`: {why}")
        err = o.error or f"hook exited {o.exit_code}"
        if o.hook.on_error == "deny":
            return (f"Tool call refused: the project's `pre_tool` hook "
                    f"`{o.hook.name}` could not decide ({err}) and is configured "
                    f"on_error: deny. Tell the user; do not retry.")
        logger.warning(f"🪝 pre_tool hook {o.hook.name} errored ({err}); allowing {tool_name}")
    return None


_session_lock = threading.Lock()
_session_fired: Dict[str, str] = {}   # key -> notes (possibly "")


def session_notes(project_root: Optional[str], session_key: Optional[str]) -> str:
    """Run ``session_start`` hooks once per ``(project_root, session_key)``
    in this process and return the notes they produced (stdout of every
    hook that exited 0, concatenated, capped).  Sync: called while the
    system prompt is built."""
    if not project_root or not enabled():
        return ""
    if not load_hooks(project_root) or not any(h.event == "session_start" for h in load_hooks(project_root)):
        return ""
    key = f"{project_root}\x00{session_key or 'process'}"
    with _session_lock:
        if key in _session_fired:
            return _session_fired[key]
        # Reserve before running so a concurrent prompt build does not
        # fire the hooks twice; the notes land when the run completes.
        _session_fired[key] = ""
    try:
        outcomes = fire("session_start", {"session": session_key or "", "project_root": project_root},
                        project_root=project_root)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"🪝 session_start hooks failed: {e}")
        return ""
    parts = [o.output for o in outcomes if o.ran and o.exit_code == 0 and o.output]
    for o in outcomes:
        if not (o.ran and o.exit_code == 0):
            logger.warning(f"🪝 session_start hook {o.hook.name} did not contribute: "
                           f"{o.error or f'exited {o.exit_code}'}")
    notes = "\n".join(parts).strip()
    if len(notes) > SESSION_NOTES_CHARS:
        notes = notes[:SESSION_NOTES_CHARS] + "…"
    with _session_lock:
        _session_fired[key] = notes
    return notes


def reset_session_notes_for_tests() -> None:
    with _session_lock:
        _session_fired.clear()


def fire(event: str, payload: Dict[str, Any], *, project_root: Optional[str]) -> List[HookOutcome]:
    """Run every configured hook for ``event``.

    Awaited hooks run inline and their outcomes are returned; the rest
    run on daemon threads and return nothing.  Safe to call from sync
    code; async callers wrap it in ``asyncio.to_thread``.
    """
    if event not in EVENTS:
        raise ValueError(f"unknown hook event {event!r}")
    root = project_root or ""
    if not root:
        return []
    hooks = [h for h in load_hooks(root) if h.event == event and h.applies_to(payload)]
    if not hooks:
        return []
    payload = {**payload, "project_root": root}
    if "file" in payload and payload.get("file") and "file_rel" not in payload:
        try:
            payload["file_rel"] = os.path.relpath(str(payload["file"]), root)
        except ValueError:
            payload["file_rel"] = str(payload["file"])
    outcomes: List[HookOutcome] = []
    for hook in hooks:
        if hook.await_result:
            outcomes.append(_execute(hook, payload, root))
        else:
            _background(hook, payload, root)
    return outcomes


def feedback_text(outcomes: List[HookOutcome]) -> str:
    """What an awaited hook contributes to the tool result the model sees."""
    lines: List[str] = []
    for o in outcomes:
        if not o.ran:
            lines.append(f"[hook {o.hook.name}] did not run: {o.error}")
            continue
        head = f"[hook {o.hook.name}] `{o.hook.run}` exited {o.exit_code}"
        if o.output:
            lines.append(head + ":\n" + o.output)
        else:
            lines.append(head)
    return "\n\n".join(lines)


async def after_tool(tool_name: str, args: Optional[Dict[str, Any]], result: Any,
                     result_text: Any, *, project_root: Optional[str]) -> Any:
    """``post_tool`` (and ``post_apply`` for a successful ``file_write``)
    after a tool call.  Returns ``result_text`` with awaited hooks'
    feedback appended when it is a string; otherwise unchanged."""
    if not project_root or not enabled():
        return result_text
    import asyncio
    ok = not (isinstance(result, dict) and result.get("error"))
    outcomes: List[HookOutcome] = []
    try:
        outcomes += await asyncio.to_thread(
            fire, "post_tool",
            {"tool": tool_name, "ok": ok, "args": dict(args or {})},
            project_root=project_root,
        )
        path = (args or {}).get("path") if tool_name == "file_write" else None
        if ok and path:
            abs_path = path if os.path.isabs(path) else os.path.join(project_root, path)
            outcomes += await asyncio.to_thread(
                fire, "post_apply", {"file": abs_path, "source": "file_write"},
                project_root=project_root,
            )
    except Exception as e:  # noqa: BLE001 -- a hook must never fail the tool call
        logger.warning(f"🪝 hooks after {tool_name} failed: {e}")
        return result_text
    if outcomes and isinstance(result_text, str):
        return result_text.rstrip() + "\n\n" + feedback_text(outcomes)
    return result_text
