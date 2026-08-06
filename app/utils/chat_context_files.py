"""
Resolve a conversation's model-pinned context files (``additionalFiles``).

Background
----------
Two independent channels feed a turn's file context:

  * the user pins files in the file tree; those arrive on the request as
    ``files`` (from the frontend's ``checkedKeys``, or ``resolve_files``
    in the CLI);
  * the model pins files via the ``context_add_file`` MCP tool, which
    persists them to the chat record's ``additionalFiles``.

Only the first channel was ever read at prompt-build time.  The chat
record was written, round-tripped through ``ChatSummary``, and shipped to
the client on every list, but nothing merged it into the prompt — so
``context_add_file``'s persistence was inert (its ephemeral inline read
was the only delivery), ``context_remove_file`` removed entries that
never contributed, and ``context_list_files`` reported files as "in
context" when they were not.

This module supplies the missing read.  It is deliberately separate from
``app.server`` so the resolution logic can be tested without standing up
the FastAPI app, and it never raises: a failure to resolve degrades to
"no additional files", which is the pre-existing behaviour.

Path form
---------
``context_add_file`` stores the project-relative path when the file is
under the project root, and an absolute path otherwise (a safe-write or
approved-external location).  ``files`` uses the same convention, so the
values are union-compatible as stored and need no rewriting here.
"""

import os
from typing import Any, Dict, List, Optional, Tuple

from app.utils.logging_utils import logger


def get_model_pinned_files(conversation_id: Optional[str]) -> List[str]:
    """
    Return the ``additionalFiles`` recorded on ``conversation_id``.

    Returns an empty list — never raises — when the conversation, project
    or chat record cannot be resolved.  A missing chat record is normal:
    a brand-new conversation has nothing persisted yet.
    """
    if not conversation_id:
        return []
    try:
        from app.context import get_project_root_or_none
        from app.storage.chats import ChatStorage
        from app.storage.projects import ProjectStorage
        from app.utils.paths import get_project_dir, get_ziya_home
    except ImportError:
        return []

    try:
        project_root = (get_project_root_or_none()
                        or os.environ.get("ZIYA_USER_CODEBASE_DIR"))
        if not project_root:
            return []

        project = ProjectStorage(get_ziya_home()).get_by_path(project_root)
        if not project:
            # Unregistered root: nothing could have been persisted against
            # it.  Do NOT auto-register here — this is a read path, and
            # creating a project as a side effect of building a prompt
            # would be a surprising write.
            return []

        storage = ChatStorage(get_project_dir(project.id))
        chat_data = storage._read_json(storage._chat_file(conversation_id))
        if not chat_data:
            return []

        additional = chat_data.get("additionalFiles")
        if not isinstance(additional, list):
            # Hand-edited or corrupt record; treat as empty rather than
            # letting a bad type propagate into the prompt builder.
            return []
        return [p for p in additional if isinstance(p, str) and p]
    except Exception as e:
        logger.debug(f"get_model_pinned_files({conversation_id}): {e}")
        return []


def merge_context_files(
    user_files: Optional[List[str]],
    model_files: Optional[List[str]],
) -> Tuple[List[str], List[str]]:
    """
    Union user-pinned and model-pinned files, preserving order.

    Returns ``(merged, added)`` where ``added`` is the subset of
    ``model_files`` not already present in ``user_files`` — useful for
    logging what the model contributed to this turn.

    User files come first and keep their original order, so an existing
    prompt's file ordering is unchanged when the model has pinned
    nothing.  Duplicates within either list are collapsed.
    """
    merged: List[str] = []
    seen = set()
    for path in (user_files or []):
        if isinstance(path, str) and path and path not in seen:
            seen.add(path)
            merged.append(path)

    added: List[str] = []
    for path in (model_files or []):
        if isinstance(path, str) and path and path not in seen:
            seen.add(path)
            merged.append(path)
            added.append(path)

    return merged, added


def resolve_auto_add_token_limit(project_root: Optional[str] = None) -> int:
    """
    Read the project's ``contextManagement.auto_add_token_limit``.

    Falls back to the model default (12500) when the project or setting
    is absent.  A value <= 0 disables the limit, matching the frontend's
    ``filterByAutoAddTokenLimit`` contract.
    """
    from app.models.project import ContextManagementSettings
    default = ContextManagementSettings().auto_add_token_limit
    try:
        from app.context import get_project_root_or_none
        from app.storage.projects import ProjectStorage
        from app.utils.paths import get_ziya_home

        root = (project_root or get_project_root_or_none()
                or os.environ.get("ZIYA_USER_CODEBASE_DIR"))
        if not root:
            return default
        project = ProjectStorage(get_ziya_home()).get_by_path(root)
        if not project or not project.settings:
            return default
        cm = project.settings.contextManagement
        if cm is None or cm.auto_add_token_limit is None:
            return default
        return int(cm.auto_add_token_limit)
    except Exception as e:
        logger.debug(f"resolve_auto_add_token_limit: {e}")
        return default


def estimate_file_tokens(abs_path: str) -> int:
    """
    Best-effort token estimate for a single file.

    Returns 0 when the size cannot be determined.  Callers must treat 0
    as "unknown" and NOT block on it — the frontend's limit filter makes
    the same guarantee, so an unmeasurable file is never rejected.
    """
    try:
        from app.utils.directory_util import estimate_tokens_fast
        tokens = estimate_tokens_fast(abs_path)
        # -1 is the tool-backed-file sentinel; not a real size.
        return tokens if isinstance(tokens, int) and tokens > 0 else 0
    except Exception as e:
        logger.debug(f"estimate_file_tokens({abs_path}): {e}")
        return 0


def exceeds_auto_add_limit(abs_path: str, limit: int) -> Tuple[bool, int]:
    """
    Report whether ``abs_path`` is over ``limit`` tokens.

    Returns ``(exceeds, tokens)``.  ``limit <= 0`` disables the check.
    An unknown size (0) never exceeds — consistent with the frontend.
    """
    if not isinstance(limit, int) or limit <= 0:
        return False, 0
    tokens = estimate_file_tokens(abs_path)
    if tokens <= 0:
        return False, tokens
    return tokens > limit, tokens
