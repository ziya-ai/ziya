"""
Bead backlog aggregation -- scan a project's chat files for parked/abandoned
beads across all conversations (design/bead-backlog-browser.md).
"""
import time
from pathlib import Path
from typing import Dict, List, Optional

from app.models.bead import BeadTree
from app.storage.beads import _parse_beads
from app.storage.chats import ChatStorage
from app.utils.paths import get_project_dir
from app.utils.logging_utils import logger

_BEADS_FIELD = "_beads"
_LINEAGE_ROOT_FIELD = "lineageRootId"

_extract_cache: Dict[str, tuple] = {}


def invalidate(conversation_id: str) -> None:
    if not conversation_id:
        return
    drop = [
        p for p, (_, ex) in _extract_cache.items()
        if ex is not None and ex.get("conversation_id") == conversation_id
    ]
    for p in drop:
        _extract_cache.pop(p, None)


def _build_extract(raw: dict) -> Optional[dict]:
    conv_id = raw.get("id")
    if not conv_id:
        return None
    lineage_root = raw.get(_LINEAGE_ROOT_FIELD)
    if lineage_root and lineage_root != conv_id:
        return None
    beads = raw.get(_BEADS_FIELD)
    if not isinstance(beads, list) or not beads:
        return None

    messages = raw.get("messages") or []
    seam_snippets: Dict[str, dict] = {}
    for b in beads:
        if not isinstance(b, dict):
            continue
        mi = b.get("message_index")
        if mi is None:
            continue
        idx = mi - 1
        if 0 <= idx < len(messages):
            msg = messages[idx] or {}
            content = msg.get("content")
            text = content if isinstance(content, str) else ""
            seam_snippets[b.get("id")] = {
                "role": msg.get("role"),
                "text": text[:240],
            }

    return {
        "conversation_id": conv_id,
        "title": raw.get("title"),
        "folderId": raw.get("folderId"),
        "beads": beads,
        "seam_snippets": seam_snippets,
    }


def _scan_project(project_dir: Path):
    storage = ChatStorage(project_dir)
    chats_dir = storage.chats_dir
    extracts: List[dict] = []
    scanned = 0
    if not chats_dir.exists():
        return extracts, scanned

    for chat_file in sorted(chats_dir.glob("*.json")):
        if chat_file.name.startswith("_"):
            continue
        if chat_file.name.endswith(".bindings.json"):
            continue
        scanned += 1
        try:
            st = chat_file.stat()
        except OSError:
            continue
        path_str = str(chat_file)
        cached = _extract_cache.get(path_str)
        if cached is not None and cached[0] == st.st_mtime:
            extract = cached[1]
        else:
            raw = storage._read_json(chat_file)
            extract = _build_extract(raw) if raw else None
            _extract_cache[path_str] = (st.st_mtime, extract)
        if extract is not None:
            extracts.append(extract)
    return extracts, scanned


def _count_descendants_with_status(tree: BeadTree, bead_id: str, status: str) -> int:
    count = 0
    stack = list(tree.get_children(bead_id))
    while stack:
        node = stack.pop()
        if node.status == status:
            count += 1
        stack.extend(tree.get_children(node.id))
    return count


def get_backlog(project_id: str, statuses: List[str]) -> dict:
    project_dir = get_project_dir(project_id)
    extracts, scanned = _scan_project(project_dir)
    requested = set(statuses)
    now = int(time.time() * 1000)

    items: List[dict] = []
    counts = {"parked": 0, "abandoned": 0}

    for ex in extracts:
        tree = BeadTree(beads=_parse_beads(ex["beads"]))

        for b in tree.beads:
            if b.status == "parked":
                counts["parked"] += 1
            elif b.status == "abandoned":
                counts["abandoned"] += 1

        for b in tree.beads:
            if b.status not in requested:
                continue
            path = tree.get_path_to_root(b.id)
            ancestors = path[1:]
            if any(a.status == b.status for a in ancestors):
                continue

            origin = None
            if b.origin_conversation_id and b.origin_bead_id:
                origin = {
                    "conversation_id": b.origin_conversation_id,
                    "bead_id": b.origin_bead_id,
                }

            items.append({
                "bead": b.model_dump(),
                "conversation_id": ex["conversation_id"],
                "conversation_title": ex["title"],
                "folder_id": ex["folderId"],
                "breadcrumb": [n.content for n in reversed(path)],
                "descendant_parked_count":
                    _count_descendants_with_status(tree, b.id, "parked"),
                "seam_snippet": ex["seam_snippets"].get(b.id),
                "age_ms": now - b.created_at,
                "can_branch": b.message_index is not None,
                "origin": origin,
            })

    logger.debug(
        f"backlog: {len(items)} item(s) from {scanned} chat(s) "
        f"(parked={counts['parked']}, abandoned={counts['abandoned']})"
    )
    return {"items": items, "counts": counts, "scanned_chats": scanned}
