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
            from app.utils.ast_parser.integration import (
                _initialized_projects, _indexing_in_progress,
                initialize_ast_capabilities,
            )
            from app.utils.directory_util import get_ignored_patterns

            abs_root = os.path.abspath(project_root)
            if abs_root in _initialized_projects or abs_root in _indexing_in_progress:
                return

            patterns = get_ignored_patterns(abs_root)
            max_depth = int(os.environ.get("ZIYA_MAX_DEPTH", 15))

            t = threading.Thread(
                target=initialize_ast_capabilities,
                args=(abs_root, patterns, max_depth),
                daemon=True,
            )
            t.start()
            logger.info(f"AST background indexing started for project: {abs_root}")
        except Exception as e:
            logger.debug(f"Could not trigger AST indexing for {project_root}: {e}")
