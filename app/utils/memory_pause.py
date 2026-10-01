"""Halt the memory subsystem on an infrastructure outage until a human resumes it.

The rule: an outage is not evidence.  When a memory pass (post-stream
extraction, retrieval feedback, the lifecycle or organize job, post-save
maintenance) hits a ``ServiceInfraError``, the pass is abandoned with
nothing recorded from it, and ``~/.ziya/memory/paused.json`` is written.
While that file exists every memory entry point returns
``{"skipped": True, "reason": "memory_paused"}`` without calling anything,
the domain clock does not tick, and the system prompt carries a one-line
notice.  ``POST /api/v1/memory/resume`` (or the Health tab) deletes the
file; the next turn extracts normally and the lifecycle job's next tick
catches up.

The pause is a circuit breaker, not a latch.  After PROBE_BASE_S (doubling
per repeated failure, capped at PROBE_MAX_S) the next entry point runs for
real: success clears the pause automatically, another outage re-arms it
with a longer wait.  ``POST /api/v1/memory/resume`` only skips the wait.
The first version had no automatic recovery, so a five-minute network
blip silenced memory until someone found the Health tab (2026-09-28).
Chat is never paused -- the memory service model may be a different endpoint.
"""
from __future__ import annotations

import contextvars
import functools
import inspect
import json
import os
import time
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from app.utils.infra_errors import ServiceInfraError
from app.utils.logging_utils import logger

PAUSE_FILE = "paused.json"
SKIPPED: Dict[str, Any] = {"skipped": True, "reason": "memory_paused"}


def _path() -> Path:
    from app.utils.paths import get_ziya_home
    return get_ziya_home() / "memory" / PAUSE_FILE


def status() -> Optional[Dict[str, Any]]:
    """The pause record, or None when memory is running."""
    p = _path()
    try:
        if not p.exists():
            return None
        d = json.loads(p.read_text())
        return d if isinstance(d, dict) else {"paused_at": None, "reason": "unreadable"}
    except Exception:
        return {"paused_at": None, "reason": "unreadable"}


def is_paused() -> bool:
    return _path().exists()


# Automatic recovery.  The wait before the next real attempt doubles with
# each consecutive failure so a long outage is probed a few times an hour,
# not on every turn.
PROBE_BASE_S = 300
PROBE_MAX_S = 3600


def probe_wait_s(rec: Optional[Dict[str, Any]]) -> int:
    n = int((rec or {}).get("repeat_count", 0) or 0)
    return min(PROBE_BASE_S * (2 ** n), PROBE_MAX_S)


def probe_due(rec: Optional[Dict[str, Any]] = None,
              now_ms: Optional[int] = None) -> bool:
    """Whether a paused memory system should try a real call now."""
    rec = status() if rec is None else rec
    if not rec:
        return True
    last = int(rec.get("last_seen_at") or rec.get("paused_at") or 0)
    now = int(now_ms if now_ms is not None else time.time() * 1000)
    return now - last >= probe_wait_s(rec) * 1000


def should_skip() -> bool:
    """Skip a memory entry point: paused AND the retry probe is not yet due."""
    rec = status()
    return rec is not None and not probe_due(rec)


def pause(exc: BaseException, where: str) -> Dict[str, Any]:
    """Record the outage.  Idempotent: an existing pause keeps its original
    timestamp and reason; later outages only bump ``repeat_count``."""
    p = _path()
    now = int(time.time() * 1000)
    rec = status()
    if rec and rec.get("paused_at"):
        rec["repeat_count"] = int(rec.get("repeat_count", 0)) + 1
        rec["last_seen_at"] = now
        rec.setdefault("where", [])
        if where not in rec["where"]:
            rec["where"].append(where)
    else:
        rec = {"paused_at": now, "last_seen_at": now, "repeat_count": 0,
               "kind": getattr(exc, "infra_kind", "") or "connection_error",
               "error": str(exc)[:500], "where": [where]}
        logger.warning(
            f"⏸️ Memory paused ({rec['kind']}) after an infrastructure failure in "
            f"{where}: {rec['error'][:160]}.  Nothing from that pass was recorded.  "
            f"Resume with POST /api/v1/memory/resume once the endpoint is back.")
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(rec, indent=1))
        os.replace(tmp, p)
    except OSError as e:
        logger.error(f"Could not write memory pause file: {e}")
    return rec


def resume(auto: bool = False) -> Optional[Dict[str, Any]]:
    """Clear the pause.  Returns the cleared record, or None if not paused.
    ``auto`` marks a probe that succeeded, as opposed to a user's resume."""
    rec = status()
    try:
        _path().unlink()
    except FileNotFoundError:
        return None
    if rec:
        secs = (time.time() * 1000 - (rec.get("paused_at") or time.time() * 1000)) / 1000
        how = "automatically" if auto else "by the user"
        logger.info(f"▶️ Memory resumed {how} after {secs / 60:.0f} min paused ({rec.get('kind')})")
    return rec


def prompt_notice() -> Optional[str]:
    """One line for the system prompt while paused, else None."""
    rec = status()
    if not rec:
        return None
    since = time.strftime("%H:%M", time.localtime((rec.get("paused_at") or 0) / 1000))
    return (f"*Memory extraction is paused since {since} ({rec.get('kind', 'outage')}); "
            f"it retries automatically (next attempt within {probe_wait_s(rec) // 60} min).*")


# Side effects a guarded pass made before the outage that must be undone
# with it (today: the domain-clock session tick).  Registered by the pass,
# run by ``guarded`` on ServiceInfraError, cleared on every exit.
_reverts: contextvars.ContextVar[Optional[list]] = contextvars.ContextVar("memory_pause_reverts", default=None)


def on_infra_revert(fn: Callable[[], Any]) -> None:
    """Register ``fn`` to run if the current guarded pass is abandoned."""
    lst = _reverts.get()
    if lst is not None:
        lst.append(fn)


def _run_reverts() -> None:
    for fn in reversed(_reverts.get() or []):
        try:
            fn()
        except Exception as e:  # noqa: BLE001 - a revert must not mask the pause
            logger.warning(f"memory pause: revert failed: {e}")


def guarded(where: str) -> Callable:
    """Decorate a memory entry point: skip while paused; on a
    ``ServiceInfraError`` run the registered reverts, pause, and return
    SKIPPED.  Works for sync and async callables; the decorated function
    keeps its name for lazy importers (app/memory/__init__.py maps names
    to attributes)."""
    def deco(fn: Callable) -> Callable:
        if inspect.iscoroutinefunction(fn):
            @functools.wraps(fn)
            async def aw(*a, **kw):
                if should_skip():
                    return dict(SKIPPED)
                probing = is_paused()
                tok = _reverts.set([])
                try:
                    out = await fn(*a, **kw)
                    if probing:
                        resume(auto=True)
                    return out
                except ServiceInfraError as e:
                    _run_reverts()
                    pause(e, where)
                    return dict(SKIPPED, error=str(e)[:200])
                finally:
                    _reverts.reset(tok)
            return aw

        @functools.wraps(fn)
        def sw(*a, **kw):
            if should_skip():
                return dict(SKIPPED)
            probing = is_paused()
            tok = _reverts.set([])
            try:
                out = fn(*a, **kw)
                if probing:
                    resume(auto=True)
                return out
            except ServiceInfraError as e:
                _run_reverts()
                pause(e, where)
                return dict(SKIPPED, error=str(e)[:200])
            finally:
                _reverts.reset(tok)
        return sw
    return deco
