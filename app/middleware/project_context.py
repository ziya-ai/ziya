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
            if abs_root in _initialized_projects or abs_root in _indexing_in_progress:
                return

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

            patterns = get_ignored_patterns(abs_root)
            max_depth = int(os.environ.get("ZIYA_MAX_DEPTH", 15))

            def _index_and_update_status():
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

            t = threading.Thread(
                target=_index_and_update_status,
                daemon=True,
            )
            t.start()
            logger.info(f"AST background indexing started for project: {abs_root}")
        except Exception as e:
            logger.debug(f"Could not trigger AST indexing for {project_root}: {e}")
