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

import math
import json
import re
import time
from pathlib import Path
from typing import List, Optional

from app.utils.logging_utils import logger

# Relevance weighting.  Raw occurrence count was the old score, which let a
# long conversation that mentions the term incidentally a dozen times outrank
# the conversation whose *title* is the term.  Three corrections: a title hit
# is worth many body hits, because a title is a short deliberate label; the
# opening messages that establish a conversation's subject count double; and
# repeated hits inside one message saturate, so no single verbose message can
# dominate however many times it repeats the term.
TITLE_WEIGHT = 8.0
OPENING_MESSAGE_COUNT = 2
OPENING_MULTIPLIER = 2.0
# BM25-style term-frequency saturation constant.  A message's contribution
# asymptotes at TF_K1 + 1 (~2.2), so 100 hits in one message is worth barely
# more than 10.
TF_K1 = 1.2
# Conversations up to this length are not length-penalised at all.
LENGTH_NORM_FREE_MESSAGES = 10

SORT_MODES = ("relevance", "newest", "oldest")


def _relevance_score(matches: List[dict], title_matches: bool,
                     total_messages: int) -> float:
    """Score one conversation against the query.

    A matching message contributes occ*(k+1)/(occ+k), which rises from 1.0 at
    one occurrence towards an asymptote of k+1 - so repetition inside a single
    message is collapsed rather than counted linearly.  The body total is then
    divided by sqrt(messages / 10) for conversations longer than ten messages,
    so a sprawling thread must be proportionally on-topic to outrank a short
    one.  The title term is added outside that normalisation: a title hit
    describes the conversation, not its volume.
    """
    body = 0.0
    for m in matches:
        occ = max(1, len(m.get("highlightPositions") or ()))
        weight = occ * (TF_K1 + 1.0) / (occ + TF_K1)
        if m.get("messageIndex", 0) < OPENING_MESSAGE_COUNT:
            weight *= OPENING_MULTIPLIER
        body += weight
    norm = max(1.0, math.sqrt(max(1, total_messages) / LENGTH_NORM_FREE_MESSAGES))
    return (TITLE_WEIGHT if title_matches else 0.0) + body / norm


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


def compile_query(query: str, case_sensitive: bool = False) -> "re.Pattern[str]":
    """Turn a search string into the pattern the scan matches with.

    Each whitespace run in the query matches any whitespace run in the text,
    so ``⏱️ STALL`` finds a log line written ``⏱️  STALL`` (two spaces, as the
    stall warning is formatted) and a phrase typed on one line finds the same
    words wrapped across a newline.  Everything else is literal: no wildcards,
    no word stemming.  The frontend fallback (frontend/src/utils/searchPattern.ts)
    builds the identical pattern so local and server results agree.
    """
    tokens = query.split()
    if not tokens:
        return re.compile(r"(?!x)x")  # matches nothing
    body = r"\s+".join(re.escape(t) for t in tokens)
    return re.compile(body, 0 if case_sensitive else re.IGNORECASE)


def _build_match(content_str: str, pattern: "re.Pattern[str]",
                 index: int, role: str, timestamp: int,
                 max_snippet_length: int) -> Optional[dict]:
    """Build one MessageMatch dict, or None if the term isn't present.

    Snippet/highlight construction mirrors the original client logic so the
    rendered result matches what users saw with the old local search.  Each
    highlight carries its own length because a whitespace-tolerant match can
    span more characters than the query itself.
    """
    occurrences = [
        {"start": m.start(), "length": m.end() - m.start()}
        for m in pattern.finditer(content_str)
        if m.end() > m.start()
    ]

    if not occurrences:
        return None

    first = occurrences[0]["start"]
    term_len = occurrences[0]["length"]
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


def _search_one_chat(data: dict, project_id: str, pattern: "re.Pattern[str]",
                     max_snippet_length: int) -> Optional[dict]:
    """Search a single chat dict; return a SearchResult dict or None."""
    if data.get("isActive") is False:
        return None

    title = data.get("title") if isinstance(data.get("title"), str) else ""
    title_matches = pattern.search(title) is not None

    matches: List[dict] = []
    messages = data.get("messages") or []
    total_messages = len(messages) if isinstance(messages, list) else 0
    newest_msg_ts = 0
    if isinstance(messages, list):
        for index, msg in enumerate(messages):
            if not isinstance(msg, dict):
                continue
            raw_ts = msg.get("_timestamp") or msg.get("timestamp") or 0
            if isinstance(raw_ts, (int, float)) and raw_ts > newest_msg_ts:
                newest_msg_ts = int(raw_ts)
            content_str = _to_searchable_text(msg.get("content"))
            if not content_str:
                continue
            ts = (raw_ts or data.get("lastActiveAt")
                  or data.get("lastAccessedAt") or 0)
            match = _build_match(
                content_str, pattern, index,
                msg.get("role") or "assistant", ts, max_snippet_length,
            )
            if match:
                matches.append(match)

    if not matches and not title_matches:
        return None

    # Age is last *activity*, not last access.  lastAccessedAt moves when a
    # chat is merely opened to skim, which made a two-year-old thread the
    # "freshest" result the moment you looked at it.  lastActiveAt is when
    # the chat was last written to; the newest message stamp covers records
    # written before that field existed.
    last_activity = (data.get("lastActiveAt") or newest_msg_ts
                     or data.get("lastAccessedAt") or 0)
    return {
        "conversationId": data.get("id"),
        "conversationTitle": title,
        "folderId": data.get("folderId") or data.get("groupId"),
        "projectId": data.get("projectId") or project_id,
        "matches": matches,
        "totalMatches": len(matches) + (1 if title_matches else 0),
        "relevanceScore": _relevance_score(matches, title_matches, total_messages),
        "lastActivityAt": last_activity,
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


def _candidate_files(ziya_home: Path, projects_dir: Path, project_id: str,
                     all_projects: bool, id_filter: Optional[set],
                     query: str = ""):
    """Yield (project_id, chat_file) pairs to scan.

    Listed ids name specific conversations, so they are resolved directly
    through the cross-project chat index (the file stem is the id) and only
    those files are opened, whatever the project scope.  Scanning the scope
    and filtering afterwards decrypted every chat file in it (7-22 s per
    call with all_projects), and an id owned by another project was never
    reached without all_projects (case 606f5f05).
    """
    if id_filter is not None:
        from app.storage import chat_index
        resolved, missing = chat_index.lookup_many(ziya_home, sorted(id_filter))
        if missing:
            # Possibly written by another process since the index was built.
            chat_index.invalidate()
            more, _ = chat_index.lookup_many(ziya_home, missing)
            resolved.update(more)
        for path in resolved.values():
            yield path.parent.parent.name, path
        return
    # Otherwise the in-memory trigram filter narrows the scan to chats that
    # can contain the query (app/utils/chat_search_index.py); None means the
    # query has no usable trigram, or the index is off, so scan everything.
    from app.utils import chat_search_index
    hits = chat_search_index.candidate_files(ziya_home, project_id,
                                             all_projects, query)
    if hits is not None:
        yield from hits
        return
    for proj_dir in projects_dir.iterdir():
        if not proj_dir.is_dir():
            continue
        if not all_projects and proj_dir.name != project_id:
            continue
        for chat_file in _iter_chat_files(proj_dir / "chats"):
            yield proj_dir.name, chat_file


def search_chats(ziya_home: Path, project_id: str, query: str,
                 all_projects: bool = False, case_sensitive: bool = False,
                 max_snippet_length: int = 150,
                 sort: str = "relevance",
                 conversation_ids: Optional[List[str]] = None) -> List[dict]:
    """Scan chat files for *query* and return SearchResult dicts.

    Streams one file at a time so peak memory is a single chat record
    regardless of history size.  ``all_projects=False`` scans strictly the
    requested project; ``True`` scans every project directory.

    ``sort`` is one of ``relevance`` (weighted score, see _relevance_score),
    ``newest`` or ``oldest`` (by last activity).  An unrecognised value falls
    back to ``relevance`` rather than erroring, so a stale client that sends
    a mode this server does not know still gets usable results.

    ``conversation_ids``, when given and non-empty, restricts hits to those
    conversations, wherever they live: the ids are resolved across projects
    and ``all_projects`` / ``project_id`` do not apply.  This is what lets a
    caller ask "where in *this* thread did we decide X" — without it every
    search is project-wide.
    """
    if not query or not query.strip():
        return []
    if sort not in SORT_MODES:
        sort = "relevance"
    id_filter = set(conversation_ids) if conversation_ids else None

    t0 = time.perf_counter()
    pattern = compile_query(query, case_sensitive)
    projects_dir = ziya_home / "projects"
    if not projects_dir.exists():
        return []

    results: List[dict] = []
    seen_ids = set()
    n_files = 0

    for proj_name, chat_file in _candidate_files(
            ziya_home, projects_dir, project_id, all_projects, id_filter,
            query):
        data = _read_chat_data(chat_file)
        if not data:
            continue
        chat_id = data.get("id")
        if not chat_id or chat_id in seen_ids:
            continue
        if id_filter is not None and chat_id not in id_filter:
            continue
        n_files += 1
        result = _search_one_chat(data, proj_name, pattern, max_snippet_length)
        if result:
            seen_ids.add(chat_id)
            results.append(result)

    if sort == "newest":
        results.sort(key=lambda r: (r["lastActivityAt"], r["relevanceScore"]),
                     reverse=True)
    elif sort == "oldest":
        # A 0 timestamp means "unknown", so push those last instead of letting
        # them pose as the oldest conversations in the project.
        results.sort(key=lambda r: (r["lastActivityAt"] or float("inf"),
                                    -r["relevanceScore"]))
    else:
        results.sort(key=lambda r: (r["relevanceScore"], r["lastActivityAt"]),
                     reverse=True)
    logger.debug(
        f"search_chats[{project_id[:8]}] q={query!r} all={all_projects} sort={sort}: "
        f"{len(results)} hits over {n_files} files in "
        f"{(time.perf_counter()-t0)*1000:.0f}ms"
    )
    return results
