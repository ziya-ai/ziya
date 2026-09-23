"""
Handoff prelude — the standing context around a conversation handoff.

Two documents, one shape (``HandoffDoc``):

- ``Chat.handoff`` on a CONTINUATION child: the inherited document, injected
  every turn (section "Inherited handoff" below).
- ``Chat.handoffDraft`` on a SOURCE: the LIVING draft the model maintains via
  the ``handoff_write`` tool as context tightens.  While it exists, every
  turn's prelude carries it plus an instruction to merge in anything
  relevant that changed this turn.  A continuation child carries no draft
  until the user asks for one or context pressure recommends it.

Context pressure is measured server-side from the messages actually
assembled for the previous turn (``record_context_pressure``, called from
``build_messages_for_streaming``) and kept in a process-local cache — one
turn stale, lost on restart, never written to the chat record (a per-turn
server write would race the frontend's sync).  At or above
``ZIYA_HANDOFF_NUDGE_RATIO`` (default 0.7, the same 70% the frontend's
TokenCountDisplay turns orange at) a conversation with no draft gets a
nudge to start one.  The nudge is text; nothing is automatic.

A handoff (design/conversation-handoff.md) creates a child conversation with
an EMPTY transcript and a user-editable handoff document stored on the child
record (``Chat.handoff``).  The document is not a message; it is injected
into the system prompt on every turn so that editing it — at any point in
the child's life — takes effect on the next turn without touching history.

The inherited-handoff prelude has three parts:

1. Retrieval instructions + the predecessor segment ids, so the model knows
   the prior transcript exists and how to reach it (``chat_read`` /
   ``chat_search(conversation_ids=[...])``).  Nothing is summarised away;
   the document is an index into a transcript that is still whole.
2. The stored document (model-drafted, user-edited).
3. Open threads, derived LIVE from the bead tree at prompt-build time.  A
   handoff child shares its lineage's bead tree (``lineageRootId``, the "b2"
   model), so this section always reflects the current state — a bead closed
   in the child is closed everywhere, and never shows as open here.

Delivery is split for prompt caching (see split_handoff_prelude): parts 1–2
are stable and go into the system prompt; part 3, the living draft and the
pressure nudge change turn-to-turn and ride on the final user message so the
cached prefix (files, history) is never invalidated by them.

The record loader mirrors ``app.utils.chat_context_files``: it resolves the
project from the request root and reads the raw chat JSON.  It never raises
and never registers a project as a side effect — this is a read path inside
prompt construction.
"""

import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from app.utils.logging_utils import logger

# Fields on the chat record that make it a handoff child.
HANDOFF_FIELD = "handoff"
# The living draft on a source conversation (written by handoff_write).
DRAFT_FIELD = "handoffDraft"
LINEAGE_KIND_HANDOFF = "handoff"

# Section keys handoff_write(mode='merge') accepts, in render order.
DRAFT_SECTIONS = ("objective", "state", "decisions", "gotchas", "references")

DEFAULT_NUDGE_RATIO = 0.7

# conversation_id -> (tokens, limit) from the last assembled turn.
_pressure_cache: Dict[str, Tuple[int, int]] = {}


def nudge_ratio() -> float:
    raw = os.environ.get("ZIYA_HANDOFF_NUDGE_RATIO")
    if not raw:
        return DEFAULT_NUDGE_RATIO
    try:
        v = float(raw)
    except ValueError:
        return DEFAULT_NUDGE_RATIO
    return v if 0 < v <= 1 else DEFAULT_NUDGE_RATIO


def estimate_messages_tokens(messages: List[Any]) -> int:
    """Cheap chars/4 estimate over assembled messages.  A nudge threshold
    does not justify a tokenizer pass over a 150k-token context per turn."""
    total = 0
    for m in messages or []:
        content = m.get("content") if isinstance(m, dict) else getattr(m, "content", None)
        if isinstance(content, str):
            total += len(content)
        elif isinstance(content, list):
            for block in content:
                if isinstance(block, dict):
                    t = block.get("text")
                    if isinstance(t, str):
                        total += len(t)
                elif isinstance(block, str):
                    total += len(block)
    return total // 4


def record_context_pressure(conversation_id: Optional[str], tokens: int,
                            limit: Optional[int]) -> None:
    """Remember the assembled size of this turn for the NEXT turn's nudge."""
    if not conversation_id or not limit or limit <= 0:
        return
    _pressure_cache[conversation_id] = (int(tokens), int(limit))


def get_context_pressure(conversation_id: Optional[str]) -> Optional[float]:
    """Fraction of the context limit the previous turn used, or None."""
    if not conversation_id:
        return None
    hit = _pressure_cache.get(conversation_id)
    if not hit:
        return None
    tokens, limit = hit
    return tokens / limit if limit else None


def clear_pressure_cache() -> None:
    _pressure_cache.clear()

# Bound on the predecessor walk; a handoff chain is linear so this is a
# cycle guard, not a real limit.
_MAX_CHAIN = 50

_OPEN_BEAD_STATUSES = ("active", "parked")


def _iso(ms: Any) -> Optional[str]:
    if not isinstance(ms, (int, float)) or ms <= 0:
        return None
    try:
        return datetime.fromtimestamp(ms / 1000.0, tz=timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ")
    except (OverflowError, OSError, ValueError):
        return None


def resolve_chat_storage_for_request():
    """ChatStorage for the project registered at the request root, or None.

    Same resolution as get_model_pinned_files; factored so the prelude and
    any later handoff endpoint share one notion of "the current project".
    """
    try:
        from app.context import get_project_root_or_none
        from app.storage.chats import ChatStorage
        from app.storage.projects import ProjectStorage
        from app.utils.paths import get_project_dir, get_ziya_home
    except ImportError:
        return None
    project_root = (get_project_root_or_none()
                    or os.environ.get("ZIYA_USER_CODEBASE_DIR"))
    if not project_root:
        return None
    project = ProjectStorage(get_ziya_home()).get_by_path(project_root)
    if not project:
        return None
    return ChatStorage(get_project_dir(project.id))


def _read_record(storage, chat_id: str) -> Optional[dict]:
    try:
        data = storage._read_json(storage._chat_file(chat_id))
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def predecessor_chain(storage, record: dict) -> List[dict]:
    """Walk ``branchedFrom`` back through the handoff chain, newest first.

    Only HANDOFF hops are followed: a fork or branch ancestor beyond the
    first handoff source is not a prior segment of this track, and its
    transcript was copied into the source anyway.  The immediate source is
    always included whatever its own kind.  A missing ancestor record is
    represented by an id-only placeholder so the model still gets the id.
    """
    chain: List[dict] = []
    seen = {record.get("id")}
    parent_id = record.get("branchedFrom")
    kind = record.get("lineageKind")
    depth = 0
    while parent_id and kind == LINEAGE_KIND_HANDOFF and depth < _MAX_CHAIN:
        if parent_id in seen:
            break
        seen.add(parent_id)
        parent = _read_record(storage, parent_id) if storage else None
        if not parent:
            chain.append({"id": parent_id})
            break
        chain.append(parent)
        parent_id = parent.get("branchedFrom")
        kind = parent.get("lineageKind")
        depth += 1
    return chain


def _open_beads(storage, conversation_id: str) -> List[Tuple[str, str]]:
    """(status, content) for open beads on the conversation's SHARED tree."""
    try:
        from app.storage.beads import load_bead_tree
        tree = load_bead_tree(chat_storage=storage, conversation_id=conversation_id)
    except Exception as e:
        logger.debug(f"handoff prelude: bead tree unavailable: {e}")
        return []
    out: List[Tuple[str, str]] = []
    for b in tree.beads:
        if b.status in _OPEN_BEAD_STATUSES and b.content:
            out.append((b.status, b.content))
    # Active first, then parked; stable within each group.
    out.sort(key=lambda t: 0 if t[0] == "active" else 1)
    return out


def render_handoff_prelude(
    record: dict,
    predecessors: List[dict],
    open_beads: List[Tuple[str, str]],
) -> str:
    """Full inherited prelude: document + predecessors + live open threads.

    Kept as the combined view (tests, diagnostics).  Production splits it:
    render_inherited_prelude is STABLE across turns and lives in the system
    prompt; render_open_threads is VOLATILE and rides on the final user
    message so the cached prefix is untouched.  See split_handoff_prelude.
    """
    head = render_inherited_prelude(record, predecessors)
    if not head:
        return ""
    return head + "\n\n" + render_open_threads(open_beads)


def render_open_threads(open_beads: List[Tuple[str, str]]) -> str:
    """The live open-threads block.  Changes whenever a bead in the shared
    lineage tree opens or closes, so it must never sit inside the cached
    system prompt."""
    lines = ["Open threads (live, from the shared bead tree; a bead closed "
             "in any segment of this lineage is closed here too):"]
    if open_beads:
        for status, content in open_beads:
            lines.append(f"  - [{status}] {content}")
    else:
        lines.append("  - (none open)")
    return "\n".join(lines)


def render_inherited_prelude(record: dict, predecessors: List[dict]) -> str:
    """Retrieval instructions, predecessor ids and the inherited document.
    Changes only when the user edits the document — safe to cache."""
    handoff = record.get(HANDOFF_FIELD)
    if not isinstance(handoff, dict):
        return ""
    document = (handoff.get("document") or "").strip()
    if not document:
        return ""

    ids = [p.get("id") for p in predecessors if p.get("id")]
    lines: List[str] = [
        "[Conversation handoff]",
        "This conversation CONTINUES earlier work.  The earlier segment's full "
        "transcript is not in context; the handoff document below is an index "
        "into it, not a replacement for it.  When you need a prior decision, "
        "tool result, file, or the user's exact words, retrieve them at full "
        "fidelity rather than guessing:",
        "  - chat_read(chat_id=<segment id>, ...) to read turns or a message range",
        "  - chat_search(query=..., conversation_ids=[<segment ids>]) to find where "
        "something was discussed",
        "",
        "Predecessor segments (newest first):",
    ]
    if predecessors:
        for p in predecessors:
            pid = p.get("id")
            if not pid:
                continue
            title = p.get("title")
            msgs = p.get("messages")
            count = len(msgs) if isinstance(msgs, list) else None
            detail = []
            if title:
                detail.append(f'"{title}"')
            if count is not None:
                detail.append(f"{count} messages")
            suffix = f" — {', '.join(detail)}" if detail else ""
            lines.append(f"  - {pid}{suffix}")
    else:
        lines.append("  - (source id unavailable)")
    if ids:
        lines.append(f"Default conversation_ids for chat_search: {ids}")

    edited = _iso(handoff.get("editedAt"))
    generated = _iso(handoff.get("generatedAt"))
    stamp = (f"edited by the user {edited}" if edited
             else f"generated {generated}" if generated else None)
    lines += ["", "Handoff document" + (f" ({stamp})" if stamp else "") + ":",
              document]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Living draft (source side)
# ---------------------------------------------------------------------------

def render_sections(sections: Dict[str, str]) -> str:
    """Render section-keyed text into the stored markdown document."""
    parts: List[str] = []
    for key in DRAFT_SECTIONS:
        text = (sections.get(key) or "").strip()
        if text:
            parts.append(f"## {key.capitalize()}\n{text}")
    return "\n\n".join(parts)


def parse_sections(document: str) -> Dict[str, str]:
    """Inverse of render_sections for documents in that shape.  Text before
    the first recognised heading (or a document with no headings) lands
    under ``state`` so a free-form document still merges rather than being
    lost."""
    out: Dict[str, str] = {}
    current: Optional[str] = None
    buf: List[str] = []

    def flush():
        if buf:
            text = "\n".join(buf).strip()
            if text:
                key = current or "state"
                out[key] = (out[key] + "\n" + text) if out.get(key) else text
        buf.clear()

    for line in (document or "").splitlines():
        if line.startswith("## "):
            head = line[3:].strip().lower()
            if head in DRAFT_SECTIONS:
                flush()
                current = head
                continue
        buf.append(line)
    flush()
    return out


def merge_draft(existing: Optional[dict], *, document: Optional[str] = None,
                sections: Optional[Dict[str, str]] = None, mode: str = "merge",
                now_ms: Optional[int] = None, source_message_count: Optional[int] = None) -> dict:
    """Produce the new ``handoffDraft`` value.

    ``replace``: the given document (or rendered sections) becomes the whole
    draft.  ``merge``: given sections overwrite the same-named sections of
    the existing draft; unnamed sections are kept.  A user edit is never
    silently dropped — ``editedAt`` is cleared only on ``replace`` because
    that is the one case where the model has explicitly superseded it.
    """
    import time
    now = now_ms if now_ms is not None else int(time.time() * 1000)
    existing = existing if isinstance(existing, dict) else None
    if mode not in ("merge", "replace"):
        raise ValueError(f"mode must be 'merge' or 'replace', got {mode!r}")

    if mode == "replace" or not existing:
        if document is None and sections:
            document = render_sections(sections)
        new_doc = (document or "").strip()
        if not new_doc:
            raise ValueError("A handoff draft needs a non-empty document or sections.")
        return {
            "document": new_doc,
            "generatedAt": now,
            "updatedAt": now,
            "editedAt": None,
            "sourceMessageCount": source_message_count or (existing or {}).get("sourceMessageCount", 0),
        }

    # merge into existing
    merged = parse_sections(existing.get("document") or "")
    if sections:
        for k, v in sections.items():
            if k in DRAFT_SECTIONS and isinstance(v, str) and v.strip():
                merged[k] = v.strip()
    if document is not None and document.strip():
        # A plain document under merge is treated as the state section.
        merged["state"] = document.strip()
    new_doc = render_sections(merged)
    if not new_doc:
        raise ValueError("Merge produced an empty draft.")
    return {
        **existing,
        "document": new_doc,
        "updatedAt": now,
        "sourceMessageCount": source_message_count or existing.get("sourceMessageCount", 0),
    }


def render_draft_prelude(draft: dict) -> str:
    document = (draft.get("document") or "").strip()
    if not document:
        return ""
    stamp = _iso(draft.get("editedAt")) or _iso(draft.get("updatedAt"))
    who = "edited by the user" if draft.get("editedAt") else "last updated"
    lines = [
        "[Handoff draft — living]",
        "A handoff draft exists for this conversation.  The user may continue "
        "this work in a fresh conversation at any time, and this document is "
        "what that continuation will receive.  If this turn produced a "
        "decision, a change of state, a gotcha, or a reference worth carrying "
        "forward, update the draft before finishing with "
        "handoff_write(sections={...}, mode='merge') — touch only the sections "
        "that changed.  Do not rewrite it wholesale unless it is wrong.  Do not "
        "write a handoff to any file; this record is the handoff.",
        "",
        f"Current draft ({who} {stamp}):" if stamp else "Current draft:",
        document,
    ]
    return "\n".join(lines)


def render_pressure_nudge(ratio: float, has_inherited: bool) -> str:
    # Deliberately NO percentage: the text must be byte-identical from one
    # turn to the next, or every turn past the threshold re-bills whatever
    # prefix it shares a cache block with.  The exact figure is available
    # to the model via handoff_read.
    lines = [
        "[Context pressure]",
        "This conversation's assembled context is above the handoff "
        "threshold of the model's limit (handoff_read reports the exact "
        "fraction).  Consider starting a handoff draft with "
        "handoff_write(sections={objective, state, decisions, gotchas, "
        "references}, mode='replace') so the user can continue in a fresh "
        "conversation without losing the thread.  Cite turns or message "
        "indices in `references` so the continuation can retrieve them with "
        "chat_read.  Do not write a handoff to a file.  This is a suggestion "
        "to raise with the user, not an instruction to stop working.",
    ]
    if has_inherited:
        lines.append(
            "Start the new draft from the current state of THIS segment; the "
            "inherited handoff above already covers the earlier ones.")
    return "\n".join(lines)


def split_handoff_prelude(conversation_id: Optional[str], storage=None,
                          pressure: Optional[float] = None) -> Tuple[str, str]:
    """(system_part, turn_part) for ``conversation_id``; both "" for a
    conversation that is neither a handoff child, nor carries a draft, nor
    is under pressure (the overwhelmingly common case — one cheap JSON read).

    The split is about PROMPT CACHING.  Providers place the cache marker on
    the system block (and on the last history message), so anything that
    changes turn-to-turn inside the system prompt re-bills the whole cached
    prefix — precisely the large contexts handoff exists for.

      system_part — inherited handoff document + predecessor ids.  Changes
                    only on a user edit.  Appended to system_prompt_addition.
      turn_part   — live open threads (bead state), the living draft (the
                    model is told to merge into it every turn) and the
                    pressure nudge.  Appended to the FINAL user message,
                    which is never inside a cache boundary.  A mid-history
                    system-role message is NOT a safe alternative: the
                    Anthropic wrapper overwrites system_content with it and
                    the Bedrock wrapper merges it into the leading block.

    ``pressure`` defaults to the cached value from the previous turn.
    ``storage`` may be passed explicitly (tests, callers that already hold
    one); otherwise it is resolved from the request root.  Never raises.
    """
    if not conversation_id:
        return "", ""
    try:
        if storage is None:
            storage = resolve_chat_storage_for_request()
        if storage is None:
            return "", ""
        record = _read_record(storage, conversation_id)
        if not record:
            return "", ""
        system_part = ""
        turn_parts: List[str] = []

        has_inherited = isinstance(record.get(HANDOFF_FIELD), dict)
        if has_inherited:
            predecessors = predecessor_chain(storage, record)
            system_part = render_inherited_prelude(record, predecessors)
            if system_part:
                turn_parts.append(render_open_threads(_open_beads(storage, conversation_id)))

        draft = record.get(DRAFT_FIELD)
        has_draft = isinstance(draft, dict) and bool((draft.get("document") or "").strip())
        if has_draft:
            turn_parts.append(render_draft_prelude(draft))
        else:
            if pressure is None:
                pressure = get_context_pressure(conversation_id)
            if pressure is not None and pressure >= nudge_ratio():
                turn_parts.append(render_pressure_nudge(pressure, has_inherited))

        return system_part, "\n\n".join(p for p in turn_parts if p)
    except Exception as e:
        logger.warning(f"handoff prelude failed (non-fatal): {e}")
        return "", ""


def build_handoff_prelude(conversation_id: Optional[str], storage=None,
                          pressure: Optional[float] = None) -> str:
    """The whole prelude as one string (system part, then turn part).  For
    tests and diagnostics; production uses split_handoff_prelude so the
    two parts land in different places."""
    system_part, turn_part = split_handoff_prelude(conversation_id, storage=storage,
                                                   pressure=pressure)
    return "\n\n".join(p for p in (system_part, turn_part) if p)


def append_handoff_prelude(system_prompt_addition: str, conversation_id: Optional[str],
                           storage=None) -> str:
    """Return ``system_prompt_addition`` with the STABLE handoff prelude (the
    inherited document, if this is a continuation) appended; unchanged
    otherwise.  The volatile part is delivered by append_handoff_turn_prelude."""
    system_part, _ = split_handoff_prelude(conversation_id, storage=storage)
    return join_prelude(system_prompt_addition, system_part)


def join_prelude(base: Optional[str], prelude: str) -> str:
    if not prelude:
        return base or ""
    base = (base or "").rstrip()
    return f"{base}\n\n{prelude}" if base else prelude


TURN_PRELUDE_MARKER = "[Ziya — handoff status for this turn]"


def append_handoff_turn_prelude(messages: List[Any], turn_part: str) -> bool:
    """Append ``turn_part`` to the LAST user message in an assembled message
    list, after a marker that tells the model this block is harness state,
    not the user's words.  Handles string and block-list content (the same
    two shapes the file-context image injection handles).  Returns True if
    something was appended.  Never raises."""
    if not turn_part or not messages:
        return False
    block = f"\n\n{TURN_PRELUDE_MARKER}\n{turn_part}"
    try:
        for msg in reversed(messages):
            if not (isinstance(msg, dict) and msg.get("role") == "user"):
                continue
            content = msg.get("content")
            if isinstance(content, str):
                msg["content"] = content + block
                return True
            if isinstance(content, list):
                # Append to the last text block, or add one.
                for b in reversed(content):
                    if isinstance(b, dict) and b.get("type") == "text" and isinstance(b.get("text"), str):
                        b["text"] = b["text"] + block
                        return True
                content.append({"type": "text", "text": block.lstrip()})
                return True
            return False
    except Exception as e:
        logger.warning(f"handoff turn prelude append failed (non-fatal): {e}")
    return False
