"""
Server-side chat search.

Chats are stored as per-project JSON files on disk (``<ziya_home>/projects/
<pid>/chats/*.json``), optionally ALE-encrypted.  Search scans those files,
but it does so **once on the server, one file at a time**, instead of shipping
every message body of every conversation to the browser and scanning in JS.
Peak memory is a single chat record: each file is read, matched, and released
before the next is opened.

The response shape mirrors the frontend ``SearchResult[]`` contract
(see frontend/src/utils/types.ts) so the client can swap its local IndexedDB
scan for a fetch without changing any rendering code.

Scope:
  * ``all_projects=False`` — strictly the requested project's chats.
  * ``all_projects=True``  — every project's chats.

Read-only: this module never mutates or deletes files.  Inactive chats
(isActive == False) are skipped to match the sidebar list.
"""

import json
import time
from pathlib import Path
from typing import List, Optional

from app.utils.logging_utils import logger


def _to_searchable_text(content) -> str:
    """Coerce a message's ``content`` to a searchable string.

    Content is typed as a string but at runtime can be an array of content
    blocks (multimodal / tool / image messages) or ``None``.  Mirrors the
    frontend ``toSearchableText`` helper: concatenate the ``text`` of block
    entries, drop everything else.
    """
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict) and isinstance(block.get("text"), str):
                parts.append(block["text"])
        return " ".join(parts)
    return ""


def _build_match(content_str: str, search_term: str, term_len: int,
                 index: int, role: str, timestamp: int,
                 max_snippet_length: int, case_sensitive: bool) -> Optional[dict]:
    """Build one MessageMatch dict, or None if the term isn't present.

    Snippet/highlight construction mirrors the original client logic so the
    rendered result matches what users saw with the old local search.
    """
    haystack = content_str if case_sensitive else content_str.lower()
    if search_term not in haystack:
        return None

    occurrences = []
    pos = 0
    while pos < len(haystack):
        found = haystack.find(search_term, pos)
        if found == -1:
            break
        occurrences.append({"start": found, "length": term_len})
        pos = found + term_len

    if not occurrences:
        return None

    first = occurrences[0]["start"]
    snippet_start = max(0, first - 50)
    snippet_end = min(len(content_str), first + term_len + 100)
    snippet = content_str[snippet_start:snippet_end]
    if snippet_start > 0:
        snippet = "..." + snippet
    if snippet_end < len(content_str):
        snippet = snippet + "..."
    if len(snippet) > max_snippet_length:
        snippet = snippet[:max_snippet_length] + "..."

    return {
        "messageIndex": index,
        "messageRole": role,
        "snippet": snippet,
        # fullContent is in the type but unused by the UI; send the snippet
        # window rather than the whole body to keep responses small.
        "fullContent": snippet,
        "timestamp": timestamp,
        "highlightPositions": occurrences,
    }


def _read_chat_data(chat_file: Path) -> Optional[dict]:
    """Read + decrypt a chat file to a raw dict, or None on any failure."""
    try:
        raw = chat_file.read_bytes()
        if not raw:
            return None
        from app.utils.encryption import is_encrypted, get_encryptor
        if is_encrypted(raw):
            raw = get_encryptor().decrypt(raw)
        data = json.loads(raw)
        return data if isinstance(data, dict) else None
    except Exception as exc:
        logger.debug(f"chat_search: skipping {chat_file}: {exc}")
        return None


def _search_one_chat(data: dict, project_id: str, search_term: str,
                     case_sensitive: bool, max_snippet_length: int) -> Optional[dict]:
    """Search a single chat dict; return a SearchResult dict or None."""
    if data.get("isActive") is False:
        return None

    term_len = len(search_term)
    title = data.get("title") if isinstance(data.get("title"), str) else ""
    title_hay = title if case_sensitive else title.lower()
    title_matches = search_term in title_hay

    matches: List[dict] = []
    messages = data.get("messages") or []
    if isinstance(messages, list):
        for index, msg in enumerate(messages):
            if not isinstance(msg, dict):
                continue
            content_str = _to_searchable_text(msg.get("content"))
            if not content_str:
                continue
            ts = (msg.get("_timestamp") or msg.get("timestamp")
                  or data.get("lastAccessedAt") or data.get("lastActiveAt") or 0)
            match = _build_match(
                content_str, search_term, term_len, index,
                msg.get("role") or "assistant", ts,
                max_snippet_length, case_sensitive,
            )
            if match:
                matches.append(match)

    if not matches and not title_matches:
        return None

    return {
        "conversationId": data.get("id"),
        "conversationTitle": title,
        "folderId": data.get("folderId") or data.get("groupId"),
        "projectId": data.get("projectId") or project_id,
        "matches": matches,
        "totalMatches": len(matches) + (1 if title_matches else 0),
        "lastAccessedAt": data.get("lastAccessedAt") or data.get("lastActiveAt") or 0,
    }


def _iter_chat_files(chats_dir: Path):
    """Yield non-meta chat files in a chats directory."""
    if not chats_dir.exists():
        return
    for chat_file in chats_dir.glob("*.json"):
        if chat_file.name.startswith("_"):
            continue
        if chat_file.name.endswith(".bindings.json"):
            continue
        yield chat_file


def search_chats(ziya_home: Path, project_id: str, query: str,
                 all_projects: bool = False, case_sensitive: bool = False,
                 max_snippet_length: int = 150) -> List[dict]:
    """Scan chat files for *query* and return SearchResult dicts.

    Streams one file at a time so peak memory is a single chat record
    regardless of history size.  ``all_projects=False`` scans strictly the
    requested project; ``True`` scans every project directory.
    """
    if not query or not query.strip():
        return []

    t0 = time.perf_counter()
    search_term = query if case_sensitive else query.lower()
    projects_dir = ziya_home / "projects"
    if not projects_dir.exists():
        return []

    results: List[dict] = []
    seen_ids = set()
    n_files = 0

    for proj_dir in projects_dir.iterdir():
        if not proj_dir.is_dir():
            continue
        if not all_projects and proj_dir.name != project_id:
            continue

        for chat_file in _iter_chat_files(proj_dir / "chats"):
            data = _read_chat_data(chat_file)
            if not data:
                continue
            chat_id = data.get("id")
            if not chat_id or chat_id in seen_ids:
                continue
            n_files += 1
            result = _search_one_chat(
                data, proj_dir.name, search_term, case_sensitive, max_snippet_length
            )
            if result:
                seen_ids.add(chat_id)
                results.append(result)

    # Relevance (match count) then recency — same ordering as the old client.
    results.sort(key=lambda r: (r["totalMatches"], r["lastAccessedAt"]), reverse=True)
    logger.debug(
        f"search_chats[{project_id[:8]}] q={query!r} all={all_projects}: "
        f"{len(results)} hits over {n_files} files in "
        f"{(time.perf_counter()-t0)*1000:.0f}ms"
    )
    return results
