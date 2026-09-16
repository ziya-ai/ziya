"""Background warm-up of the chat summary caches at server boot.

WHY THIS EXISTS

``ChatStorage.list_summaries`` (app/storage/chats.py) and
``collect_global_chat_summaries`` (app/storage/global_items.py) each keep a
process-local, mtime+size-keyed ``_summary_cache``.  Warm, a ``GET
/api/v1/projects/{pid}/chats`` answers in ~0.1s.  Cold — every server start —
the first call must read, decrypt, parse and summarise every chat file in
EVERY project (the global scan runs on every per-project call).  Measured on
a real workspace: 1,689 encrypted files / 459 MB across 34 projects, which
exceeded the client's 25s ``LIST_TIMEOUT_MS``.  The client then throws
(deliberately — an empty list would be read as "everything was deleted
elsewhere"), abandons the sync cycle, and renders an empty sidebar until the
next poll.

Worse, the lifespan already walked every one of those files synchronously
BEFORE ``yield`` — ``chat_integrity.run_startup_check`` — and threw the parse
away, so a cold boot paid the full scan twice: once blocking the server from
accepting any connection at all, once more on the first list request.

WHAT THIS DOES

A single daemon thread, started from the lifespan and never awaited, that:

1. warms the per-project cache by calling the REAL ``list_summaries`` on each
   project (no duplicated cache-shape logic, so it cannot drift);
2. warms the cross-project cache with one ``collect_global_chat_summaries``
   pass (``exclude_project_id=""`` matches no directory, so every project is
   visited);
3. THEN runs the chat-integrity check, off the blocking path.

Task-run reconciliation deliberately stays synchronous in the lifespan: it is
small (a few thousand tiny files) and clients must never observe a stranded
"running" row, so it is not moved here.

Cache entries are per-file and self-heal on mtime/size change, so a request
that races the warm-up sees a partially warm cache and simply does the
remaining work itself — there is no correctness hazard, only less speed-up.

Side effect worth knowing: ``list_summaries`` enforces the retention policy
as it scans (expired chats are deleted).  Warming at boot means that runs a
few seconds after start instead of on the first list request — same code
path, same policy, earlier.

Never raises out of the thread; every phase is fenced so one bad project
cannot stop the others or the integrity check.
"""
from __future__ import annotations

import logging
import threading
import time
from pathlib import Path
from typing import Callable, Dict, Iterable, Optional

logger = logging.getLogger(__name__)

# Directory names under ~/.ziya/projects that are not project ids.
_SKIP_DIRS = frozenset({"p"})


def iter_project_dirs(ziya_home: Path) -> Iterable[Path]:
    """Yield each project directory that has a ``chats/`` subdirectory."""
    projects_dir = ziya_home / "projects"
    if not projects_dir.exists():
        return
    for project_dir in sorted(projects_dir.iterdir()):
        if not project_dir.is_dir() or project_dir.name in _SKIP_DIRS:
            continue
        if not (project_dir / "chats").exists():
            continue
        yield project_dir


def warm_summary_caches(ziya_home: Path) -> Dict[str, object]:
    """Populate both summary caches.  Synchronous; call from a worker thread.

    Returns a small stats dict (used by tests and the boot log line).
    """
    from app.storage.chats import ChatStorage
    from app.storage.global_items import collect_global_chat_summaries

    t0 = time.perf_counter()
    n_projects = 0
    n_summaries = 0
    errors = 0
    for project_dir in iter_project_dirs(ziya_home):
        try:
            n_summaries += len(ChatStorage(project_dir).list_summaries())
            n_projects += 1
        except Exception as exc:  # noqa: BLE001 — one bad project must not stop the rest
            errors += 1
            logger.warning("summary warm-up failed for %s: %s", project_dir.name, exc)
    t_local = time.perf_counter() - t0

    t1 = time.perf_counter()
    n_global = 0
    try:
        # "" matches no directory name, so every project is scanned.
        n_global = len(collect_global_chat_summaries(ziya_home, exclude_project_id=""))
    except Exception as exc:  # noqa: BLE001
        errors += 1
        logger.warning("global summary warm-up failed: %s", exc)
    t_global = time.perf_counter() - t1

    stats = {
        "projects": n_projects,
        "summaries": n_summaries,
        "global_summaries": n_global,
        "errors": errors,
        "local_ms": round(t_local * 1000),
        "global_ms": round(t_global * 1000),
    }
    logger.info(
        "chat summary caches warm: %d projects / %d summaries in %dms, "
        "%d global in %dms (%d error(s))",
        n_projects, n_summaries, stats["local_ms"], n_global, stats["global_ms"], errors,
    )
    return stats


def _run_all(ziya_home: Path, integrity_check: Optional[Callable[[Path], object]]) -> None:
    try:
        warm_summary_caches(ziya_home)
    except Exception as exc:  # noqa: BLE001 — never let the thread die loudly
        logger.warning("chat summary warm-up failed (non-fatal): %s", exc)
    if integrity_check is not None:
        try:
            integrity_check(ziya_home)
        except Exception as exc:  # noqa: BLE001
            logger.warning("chat-integrity startup check skipped: %s", exc)


def start_background_warmup(
    ziya_home: Path,
    integrity_check: Optional[Callable[[Path], object]] = None,
) -> threading.Thread:
    """Start the warm-up thread and return it immediately (never joined).

    ``integrity_check`` — normally ``chat_integrity.run_startup_check`` — runs
    after the caches are warm so the first list request is served before the
    read-only integrity walk competes for disk.
    """
    thread = threading.Thread(
        target=_run_all,
        args=(ziya_home, integrity_check),
        name="ziya-summary-warmup",
        daemon=True,
    )
    thread.start()
    return thread
