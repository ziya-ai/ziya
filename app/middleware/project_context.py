"""
Middleware that extracts X-Project-Root from incoming requests and sets
the request-scoped ContextVar so all downstream code sees the correct
project directory — without touching os.environ.
"""

import os
import threading
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

from app.context import set_project_root
from app.utils.logging_utils import logger

# Projects this middleware has already spawned an indexing thread for.
# Kept separate from integration._indexing_in_progress deliberately: that set
# is added to inside the worker (after a full-tree get_ignored_patterns walk),
# so it is claimed far too late to deduplicate concurrent requests, and
# pre-adding to it would make initialize_ast_capabilities() bail out and skip
# indexing entirely.
_ast_kickoff_claimed: set = set()
_ast_kickoff_lock = threading.Lock()


class ProjectContextMiddleware(BaseHTTPMiddleware):
    """
    Sets the per-request project root from the X-Project-Root header.

    The header is optional. When absent the ContextVar remains unset and
    get_project_root() falls through to the env-var / cwd default — which
    is correct for CLI usage and backwards compatibility with older
    frontends that haven't been updated yet.
    """

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        project_root = request.headers.get("X-Project-Root")

        if project_root:
            # Basic validation: must be an existing directory
            if os.path.isdir(project_root):
                set_project_root(project_root)
                self._ensure_ast_indexed(project_root)
            else:
                logger.warning(
                    f"X-Project-Root header contains non-existent path: "
                    f"{project_root!r}, ignoring"
                )

        return await call_next(request)

    @staticmethod
    def _ensure_ast_indexed(project_root: str) -> None:
        """Trigger background AST indexing for a project if not already done."""
        try:
            from app.utils.context_enhancer import _ast_indexing_status, _broadcast_ast_complete
            from app.utils.ast_parser.integration import (
                _initialized_projects, _indexing_in_progress,
                initialize_ast_capabilities,
            )
            from app.utils.directory_util import get_ignored_patterns

            abs_root = os.path.abspath(project_root)
            # Claim the project synchronously, before starting any thread.
            # Concurrent requests otherwise all pass this check and each spawn
            # a worker; the losers then race into initialize_ast_capabilities(),
            # return files_processed=0, and clobber _ast_indexing_status with a
            # spurious "Indexing returned no files" error while the real index
            # is still running.
            with _ast_kickoff_lock:
                if (
                    abs_root in _initialized_projects
                    or abs_root in _indexing_in_progress
                    or abs_root in _ast_kickoff_claimed
                ):
                    return
                _ast_kickoff_claimed.add(abs_root)

            # Update the global status dict that /api/ast/status reads.
            # Without this, the status stays stuck on the error from a
            # previous project (e.g. home-dir rejection at startup).
            _ast_indexing_status.update({
                'is_indexing': True,
                'enabled': True,
                'completion_percentage': 0,
                'is_complete': False,
                'indexed_files': 0,
                'total_files': 0,
                'error': None,
            })

            max_depth = int(os.environ.get("ZIYA_MAX_DEPTH", 15))

            def _index_and_update_status():
                # Build ignore patterns inside the worker thread: this walks
                # the entire project tree looking for .gitignore files (up to
                # ZIYA_GITIGNORE_TIMEOUT seconds). Calling it in dispatch()
                # blocked the event loop — freezing every request — on the
                # first request after a project switch.
                try:
                    patterns = get_ignored_patterns(abs_root)
                    result = initialize_ast_capabilities(abs_root, patterns, max_depth)
                    files = result.get("files_processed", 0)
                    if result.get("initialized") and files > 0:
                        _ast_indexing_status.update({
                            'is_indexing': False,
                            'completion_percentage': 100,
                            'is_complete': True,
                            'indexed_files': files,
                            'total_files': files,
                            'error': None,
                        })
                        _broadcast_ast_complete(files)
                    else:
                        _ast_indexing_status.update({
                            'is_indexing': False,
                            'is_complete': False,
                            'error': result.get("error", "Indexing returned no files"),
                        })
                finally:
                    # Release the claim unless the project actually indexed, so a
                    # failed or crashed run can be retried by a later request.
                    # Successful runs stay claimed via _initialized_projects.
                    if abs_root not in _initialized_projects:
                        with _ast_kickoff_lock:
                            _ast_kickoff_claimed.discard(abs_root)

            t = threading.Thread(
                target=_index_and_update_status,
                daemon=True,
            )
            t.start()
            logger.info(f"AST background indexing started for project: {abs_root}")
        except Exception as e:
            logger.debug(f"Could not trigger AST indexing for {project_root}: {e}")
