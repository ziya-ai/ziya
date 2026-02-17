"""
Request-scoped project context.

Provides per-request isolation of project_root so concurrent requests from
different browser tabs (different projects) don't race on a shared global.

Uses Python's contextvars, which are natively async-safe: each FastAPI
request handler (and its entire call tree, including StreamingResponse
generators) gets its own copy.
"""

import contextvars
import os
from typing import Optional

# Per-request project root — set by middleware, read everywhere.
_request_project_root: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    'request_project_root', default=None
)


def set_project_root(path: str) -> None:
    """Set the project root for the current request context."""
    _request_project_root.set(path)


def get_project_root() -> str:
    """
    Get the project root for the current request.

    Resolution order:
      1. Per-request ContextVar (set by middleware from X-Project-Root header)
      2. ZIYA_USER_CODEBASE_DIR env var (startup / CLI bootstrap)
      3. os.getcwd() (last resort)
    """
    root = _request_project_root.get()
    if root:
        return root
    return os.environ.get("ZIYA_USER_CODEBASE_DIR") or os.getcwd()


def get_project_root_or_none() -> Optional[str]:
    """Get the request-scoped project root, or None if not in a request context."""
    return _request_project_root.get()
