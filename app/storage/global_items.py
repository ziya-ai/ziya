"""
Cross-project global item collection.

When a chat or folder is marked isGlobal, it should be visible in all
projects — not just the one where it was created.  The server stores
chats per-project on disk, so this module scans sibling project
directories to find globally-shared items.
"""

import json
from pathlib import Path
from typing import List

from app.utils.logging_utils import logger
from ..models.chat import Chat
from ..models.group import ChatGroup


def collect_global_chats(
    ziya_home: Path,
    exclude_project_id: str,
) -> List[Chat]:
    """Scan all projects (except *exclude_project_id*) for isGlobal chats.

    Returns Chat objects with full messages so callers can use them for
    both summary and full-fetch responses.
    """
    projects_dir = ziya_home / "projects"
    if not projects_dir.exists():
        return []

    results: List[Chat] = []
    for project_dir in projects_dir.iterdir():
        if not project_dir.is_dir() or project_dir.name == exclude_project_id:
            continue

        chats_dir = project_dir / "chats"
        if not chats_dir.exists():
            continue

        for chat_file in chats_dir.glob("*.json"):
            if chat_file.name.startswith("_"):
                continue
            try:
                raw = chat_file.read_bytes()
                if not raw:
                    continue

                from app.utils.encryption import is_encrypted, get_encryptor
                if is_encrypted(raw):
                    raw = get_encryptor().decrypt(raw)

                data = json.loads(raw)
                if data.get("isGlobal"):
                    results.append(Chat(**data))
            except Exception as exc:
                logger.debug(f"Skipping {chat_file} during global scan: {exc}")

    return results


def collect_global_groups(
    ziya_home: Path,
    exclude_project_id: str,
) -> List[ChatGroup]:
    """Scan all projects (except *exclude_project_id*) for isGlobal groups/folders."""
    projects_dir = ziya_home / "projects"
    if not projects_dir.exists():
        return []

    results: List[ChatGroup] = []
    for project_dir in projects_dir.iterdir():
        if not project_dir.is_dir() or project_dir.name == exclude_project_id:
            continue

        groups_file = project_dir / "chats" / "_groups.json"
        if not groups_file.exists():
            continue

        try:
            raw = groups_file.read_bytes()

            from app.utils.encryption import is_encrypted, get_encryptor
            if is_encrypted(raw):
                raw = get_encryptor().decrypt(raw)

            data = json.loads(raw)
            for group_data in data.get("groups", []):
                if group_data.get("isGlobal"):
                    results.append(ChatGroup(**group_data))
        except Exception as exc:
            logger.debug(f"Skipping {groups_file} during global scan: {exc}")

    return results
