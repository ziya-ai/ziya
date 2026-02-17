"""
Middleware that extracts X-Project-Root from incoming requests and sets
the request-scoped ContextVar so all downstream code sees the correct
project directory — without touching os.environ.
"""

import os
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
            else:
                logger.warning(
                    f"X-Project-Root header contains non-existent path: "
                    f"{project_root!r}, ignoring"
                )

        return await call_next(request)
