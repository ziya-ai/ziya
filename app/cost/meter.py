"""
Meter: the only thing the streaming executor talks to.

    rid = open_iteration(...)         # before the provider call (estimate)
    close_iteration(rid, usage)       # when provider usage is in hand (actual)

Every function here is a no-op on failure.  Cost accounting must never be
able to fail a model turn, and must not measurably slow one: the write is
a single indexed INSERT / UPDATE on a WAL database.

Attribution comes from three places, in order:
  * the ``usage_attribution`` ContextVar (set by task_executor: run_id,
    block_id, source=task; by the CLI: source=cli),
  * the executor's ``is_delegate`` flag (source=delegate),
  * default: source=chat.
The project is resolved from ``project_root`` through the project index
once per path per process; the index falls back to a full scan on a miss,
so the result — including "no project" — is cached.
"""

from __future__ import annotations

import getpass
import threading
import time
import uuid
from typing import Any, Dict, Optional

from app.utils.logging_utils import logger

_project_cache: Dict[str, Optional[str]] = {}
_project_cache_lock = threading.Lock()


def _resolve_project_id(project_root: Optional[str]) -> Optional[str]:
    if not project_root:
        return None
    with _project_cache_lock:
        if project_root in _project_cache:
            return _project_cache[project_root]
    pid: Optional[str] = None
    try:
        from app.storage.projects import ProjectStorage
        from app.utils.paths import get_ziya_home
        project = ProjectStorage(get_ziya_home()).get_by_path(project_root)
        pid = project.id if project else None
    except Exception as e:  # noqa: BLE001 - attribution is best effort
        logger.debug(f"usage meter: project lookup failed for {project_root!r}: {e}")
    with _project_cache_lock:
        _project_cache[project_root] = pid
    return pid


def _current_user() -> Optional[str]:
    try:
        return getpass.getuser()
    except Exception:  # noqa: BLE001
        return None


def _attribution(is_delegate: bool) -> Dict[str, Any]:
    attr: Dict[str, Any] = {"source": "delegate" if is_delegate else "chat",
                            "run_id": None, "block_id": None}
    try:
        from app.context import get_usage_attribution
        ctx = get_usage_attribution()
        if ctx:
            attr.update({k: v for k, v in ctx.items() if k in attr})
    except ImportError:
        pass
    return attr


def dims_from_usage(usage: Any) -> Dict[str, int]:
    """Map ``IterationUsage`` / ``UsageEvent`` fields onto canonical
    dimensions.  Providers do not yet distinguish 5-minute from 1-hour
    cache writes, so every write is recorded as ``cache_write_5m`` (the
    default TTL Ziya requests)."""
    d = {
        "input": int(getattr(usage, "input_tokens", 0) or 0),
        "output": int(getattr(usage, "output_tokens", 0) or 0),
        "cache_read": int(getattr(usage, "cache_read_tokens", 0) or 0),
        "cache_write_5m": int(getattr(usage, "cache_write_tokens", 0) or 0),
    }
    thinking = int(getattr(usage, "thinking_tokens", 0) or 0)
    if thinking:
        d["reasoning"] = thinking
    return {k: v for k, v in d.items() if v}


def open_iteration(*, provider: str, model_id: str, region: Optional[str],
                   conversation_id: Optional[str], project_root: Optional[str],
                   iteration: int, estimated_input_tokens: int,
                   is_delegate: bool = False) -> Optional[str]:
    """Write the ``estimate`` row for a provider call about to be made.
    Returns the record id to pass to :func:`close_iteration`, or None if
    the write failed (the caller ignores None)."""
    try:
        from app.context import get_task_service_tier
        from app.storage.usage_ledger import UsageRecord, get_ledger
        attr = _attribution(is_delegate)
        rec = UsageRecord(
            id=uuid.uuid4().hex, ts=time.time(),
            provider=provider or "unknown", model_id=model_id or "unknown",
            region=region, tier=get_task_service_tier() or "standard",
            status="estimate",
            dims={"input": max(0, int(estimated_input_tokens or 0))},
            user=_current_user(),
            project_id=_resolve_project_id(project_root), project_root=project_root,
            conversation_id=conversation_id, run_id=attr["run_id"], block_id=attr["block_id"],
            source=attr["source"], iteration=iteration,
        )
        return get_ledger().append(rec)
    except Exception as e:  # noqa: BLE001 - accounting must never break a turn
        logger.debug(f"usage meter: open_iteration failed (non-fatal): {e}")
        return None


def close_iteration(record_id: Optional[str], usage: Any) -> None:
    """Replace the estimate with the provider-reported usage."""
    if not record_id:
        return
    try:
        from app.storage.usage_ledger import get_ledger
        dims = dims_from_usage(usage)
        if not dims:
            # No usage at all — leave the row as an estimate.
            return
        get_ledger().actualize(record_id, dims)
    except Exception as e:  # noqa: BLE001
        logger.debug(f"usage meter: close_iteration failed (non-fatal): {e}")


def reset_for_tests() -> None:
    with _project_cache_lock:
        _project_cache.clear()
