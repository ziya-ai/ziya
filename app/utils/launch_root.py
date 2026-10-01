"""Which directory a task-card run works in.

A run's root was taken only from the launching request's ``X-Project-Root``
header (``app.context.get_project_root_or_none``).  Every caller that does
not send one -- a script, ``curl``, a model tool call, anything outside the
browser -- got ``None``, and the run's tasks then resolved paths against the
SERVER PROCESS's cwd.  On 2026-09-27 a resume of the memory-replay card
(run ecf5fa24) launched that way ran in ZiyaInternal, the directory the
server was started from, and reported the card's own scripts "absent".

The project record already names the project's directory, so a missing
header falls back to it.  An explicit header still wins: the browser sends
the root the user is working in, and that must not be second-guessed here.
"""
from __future__ import annotations

import os
from typing import Optional

from app.utils.logging_utils import logger


def resolve_launch_root(project_id: Optional[str],
                        request_root: Optional[str]) -> Optional[str]:
    """``request_root`` if given, else the project's recorded path when that
    directory exists, else None (the caller's previous behaviour)."""
    if request_root:
        return request_root
    if not project_id:
        return None
    try:
        from app.storage.projects import ProjectStorage
        from app.utils.paths import get_ziya_home
        project = ProjectStorage(get_ziya_home()).get(project_id)
    except Exception as e:
        logger.debug(f"launch root: project lookup failed for {project_id}: {e}")
        return None
    path = getattr(project, "path", None) if project else None
    if path and os.path.isdir(path):
        logger.info(f"📋 TASK_LAUNCH: no request project root; using project {project_id[:8]} path {path}")
        return path
    return None
