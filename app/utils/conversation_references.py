"""
Conversation references (design/conversation-references.md, slice 1).

A conversation leans on things outside its message text: URLs, files, PDFs,
other threads (``ziya://chat/…``).  This module keeps a per-conversation
record of them on the chat record (``_references``), next to the auto-context
fields ``auto_context.py`` already maintains there.

Two provenance classes, never merged:

  * **pinned** -- the user put it there (``pin_reference``).  Listed always;
    never fades.
  * **picked_up** -- a tool touched it during a turn.  Derived from the
    ``tool_display`` chunks ``stream_chunks`` already forwards, the same way
    ``capability_events`` derives the ✦ skill pickup: every executor path
    passes through there, so no tool needs a side channel.

**Uses** accumulate per turn: ``read`` (a tool returned its content), ``cited``
(its locator or title appears in the assistant's reply), ``opened`` (the user
clicked it; recorded by the UI in a later slice).  ``written`` stays with the
tracked-files mechanism in ``auto_context``; it is not duplicated here.

A picked-up reference is **useful** when it was cited, or read in two or more
turns.  One with no use for FADE_TURNS turns is *faded*: still counted, not
listed.  Pinned items are never faded.

The model sees one line per listed reference in the per-turn volatile tail
(``format_references_notice``), link-only: kind, locator, use summary, and
for files whether they changed on disk since last read.  It expands a
reference with the tools that already exist (``file_read``, ``mcp_fetch``,
``chat_read``, ``pdf_read_pages``).  Bytes never enter the prompt from here.

Rules
-----
* One chat-record write per turn (``record_turn``), after the reply is
  complete, so pickups and citations land together.
* Every entry point swallows its own errors: recording a reference is a side
  effect of a turn and must never make the turn look failed.
* Scratch paths (``.ziya/``, ``/tmp``) and tool errors produce no reference.
"""
from __future__ import annotations

import hashlib
import os
import re
import time
from typing import Any, Dict, Iterable, List, Optional, Tuple

from app.utils.logging_utils import logger
from app.utils.ziya_links import SCHEME as ZIYA_SCHEME, chat_link, parse_ziya_link

REFERENCES_FIELD = "_references"
FADE_TURNS = 8            # picked-up and unused for this many turns -> not listed
USEFUL_READ_TURNS = 2     # read in this many distinct turns counts as useful
MAX_LISTED = 24           # cap on lines in the prompt notice

KINDS = ("url", "file", "pdf", "chat")
PROVENANCES = ("pinned", "picked_up")

# tool name (prefix-stripped) -> (argument that holds the locator, kind or
# None to infer from the locator).  Tool names may arrive as ``mcp_<name>``.
_LOCATOR_ARGS: Dict[str, Tuple[str, Optional[str]]] = {
    "file_read": ("path", None),
    "show_document": ("path", None),
    "context_add_file": ("path", None),
    "pdf_read_pages": ("path", "pdf"),
    "pdf_search": ("path", "pdf"),
    "pdf_outline": ("path", "pdf"),
    "fetch": ("url", "url"),
    "chat_read": ("chat_id", "chat"),
}

_SCRATCH_PREFIXES = (".ziya/", ".ziya" + os.sep)
_URL_RE = re.compile(r"^https?://", re.IGNORECASE)


# ---------------------------------------------------------------------------
# locators
# ---------------------------------------------------------------------------

def infer_kind(locator: str) -> Optional[str]:
    """Kind of a locator, or None for one this module does not track."""
    s = (locator or "").strip()
    if not s:
        return None
    if _URL_RE.match(s):
        return "url"
    if s.startswith(ZIYA_SCHEME):
        parsed = parse_ziya_link(s)
        return "chat" if parsed and parsed.get("kind") == "chat" else None
    if s.lower().endswith(".pdf"):
        return "pdf"
    return "file"


def normalize_locator(locator: str, kind: str, project_root: Optional[str]) -> Optional[str]:
    """Canonical form: URLs stripped of fragments, files project-relative when
    inside the project (else absolute), chats as ``ziya://chat/<id>``.
    None for scratch paths and malformed links."""
    s = (locator or "").strip()
    if not s:
        return None
    if kind == "url":
        return s.split("#", 1)[0]
    if kind == "chat":
        if s.startswith(ZIYA_SCHEME):
            parsed = parse_ziya_link(s)
            return chat_link(parsed["conversationId"]) if parsed and parsed.get("kind") == "chat" else None
        return chat_link(s) if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", s) else None
    # file / pdf
    path = os.path.expanduser(s)
    if project_root and not os.path.isabs(path):
        path = os.path.join(project_root, path)
    try:
        real = os.path.realpath(path)
    except (OSError, ValueError):
        return None
    if project_root:
        try:
            root = os.path.realpath(project_root)
        except (OSError, ValueError):
            root = None
        if root and (real == root or real.startswith(root + os.sep)):
            rel = os.path.relpath(real, root).replace(os.sep, "/")
            if rel == ".ziya" or rel.startswith(_SCRATCH_PREFIXES):
                return None
            return rel
    # Scratch dirs, before and after symlink resolution (macOS: /tmp -> /private/tmp).
    for cand in (path, real):
        if cand.startswith(("/tmp/", "/var/tmp/", "/private/tmp/", "/private/var/tmp/")):
            return None
    return real


def reference_id(kind: str, locator: str) -> str:
    return "ref_" + hashlib.sha1(f"{kind}:{locator}".encode("utf-8")).hexdigest()[:12]


def _tool_base_name(name: str) -> str:
    n = str(name or "")
    return n[4:] if n.startswith("mcp_") else n


def pickup_from_tool_display(chunk: Any, project_root: Optional[str]) -> Optional[Dict[str, str]]:
    """The reference a ``tool_display`` chunk implies, as
    ``{"kind", "locator", "tool"}``, or None.

    Only chunks that carry ``args`` can name a locator; the direct provider
    wrappers emit ``tool_display`` without them and produce no pickup.
    """
    if not isinstance(chunk, dict) or chunk.get("type") != "tool_display":
        return None
    if chunk.get("is_error"):
        return None
    tool = _tool_base_name(chunk.get("tool_name"))
    spec = _LOCATOR_ARGS.get(tool)
    if spec is None:
        return None
    args = chunk.get("args")
    if not isinstance(args, dict):
        return None
    raw = args.get(spec[0])
    if not isinstance(raw, str) or not raw.strip():
        return None
    kind = spec[1] or infer_kind(raw)
    if kind not in KINDS:
        return None
    locator = normalize_locator(raw, kind, project_root)
    if not locator:
        return None
    return {"kind": kind, "locator": locator, "tool": tool}


# ---------------------------------------------------------------------------
# chat record
# ---------------------------------------------------------------------------

def _open_chat(conversation_id: str, project_root: Optional[str]):
    from app.utils.auto_context import _open_chat as _open
    if not project_root:
        from app.context import get_project_root_or_none
        project_root = get_project_root_or_none() or os.environ.get("ZIYA_USER_CODEBASE_DIR")
    if not project_root:
        return None
    return _open(conversation_id, project_root)


def get_references(chat_data: Optional[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    refs = (chat_data or {}).get(REFERENCES_FIELD)
    return refs if isinstance(refs, dict) else {}


def _save(storage, chat_file, chat: Dict[str, Any], refs: Dict[str, Any]) -> None:
    now = int(time.time() * 1000)
    chat[REFERENCES_FIELD] = refs
    chat["lastActiveAt"] = now
    chat["_version"] = now
    storage._write_json(chat_file, chat)


def _file_hash(locator: str, project_root: Optional[str]) -> Optional[str]:
    path = locator if os.path.isabs(locator) else os.path.join(project_root or "", locator)
    try:
        with open(path, "rb") as f:
            return hashlib.sha256(f.read()).hexdigest()
    except OSError:
        return None


def _add_use(ref: Dict[str, Any], kind: str, turn: int, **extra: Any) -> None:
    uses = ref.setdefault("uses", [])
    if any(u.get("kind") == kind and u.get("turn") == turn for u in uses):
        return
    uses.append({"kind": kind, "turn": turn, **extra})
    ref["last_used_turn"] = max(int(ref.get("last_used_turn") or 0), turn)


def _upsert(refs: Dict[str, Any], kind: str, locator: str, provenance: str,
            turn: int, title: Optional[str] = None) -> Dict[str, Any]:
    rid = reference_id(kind, locator)
    ref = refs.get(rid)
    if ref is None:
        ref = {"id": rid, "kind": kind, "locator": locator, "provenance": provenance,
               "added_turn": turn, "uses": [], "title": title}
        refs[rid] = ref
    elif provenance == "pinned" and ref.get("provenance") != "pinned":
        ref["provenance"] = "pinned"            # promotion, never demotion
        ref["pinned_turn"] = turn
    if title and not ref.get("title"):
        ref["title"] = title
    return ref


# ---------------------------------------------------------------------------
# write side
# ---------------------------------------------------------------------------

def _cited_in(text: str, ref: Dict[str, Any]) -> bool:
    loc = ref.get("locator") or ""
    if not loc:
        return False
    if loc in text:
        return True
    if ref.get("kind") == "chat":
        parsed = parse_ziya_link(loc)
        return bool(parsed and parsed["conversationId"] in text)
    if ref.get("kind") in ("file", "pdf"):
        base = os.path.basename(loc)
        return len(base) > 3 and base in text
    title = ref.get("title")
    return bool(title and len(title) > 8 and title in text)


def record_turn(conversation_id: Optional[str], project_root: Optional[str],
                pickups: Iterable[Dict[str, str]], assistant_text: str,
                turn: int) -> Optional[Dict[str, int]]:
    """Record one turn's pickups and citations with a single write.

    Returns ``{"picked_up": n_new, "read": n, "cited": n}`` or None when
    nothing was recorded (no conversation, no chat record, or an error).
    """
    if not conversation_id:
        return None
    try:
        pickups = list(pickups)
        opened = _open_chat(conversation_id, project_root)
        if opened is None:
            return None
        storage, chat_file, chat = opened
        refs = dict(get_references(chat))
        new = read = cited = 0
        for p in pickups:
            before = len(refs)
            ref = _upsert(refs, p["kind"], p["locator"], "picked_up", turn)
            new += len(refs) - before
            _add_use(ref, "read", turn, tool=p.get("tool"))
            read += 1
            if ref["kind"] in ("file", "pdf"):
                ref["last_read_hash"] = _file_hash(ref["locator"], project_root)
        text = assistant_text or ""
        if text:
            for ref in refs.values():
                if _cited_in(text, ref):
                    _add_use(ref, "cited", turn)
                    cited += 1
        if not (pickups or cited):
            return {"picked_up": 0, "read": 0, "cited": 0}
        _save(storage, chat_file, chat, refs)
        logger.info(f"📎 REFERENCES: turn {turn} of {conversation_id[:8]}: "
                    f"{new} new, {read} read, {cited} cited")
        return {"picked_up": new, "read": read, "cited": cited}
    except Exception as e:
        logger.warning(f"conversation_references: record_turn failed (non-fatal): {e}")
        return None


def pin_reference(conversation_id: Optional[str], project_root: Optional[str],
                  locator: str, turn: Optional[int] = None,
                  title: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Pin a reference the user supplied (or promote a picked-up one).

    ``turn`` defaults to the chat record's current turn (the API has no
    chat_history to count).  Returns the stored reference, or None when the
    locator is not trackable or nothing could be recorded.
    """
    if not conversation_id:
        return None
    try:
        kind = infer_kind(locator)
        if kind not in KINDS:
            return None
        norm = normalize_locator(locator, kind, project_root)
        if not norm:
            return None
        opened = _open_chat(conversation_id, project_root)
        if opened is None:
            return None
        storage, chat_file, chat = opened
        if turn is None:
            turn = current_turn(chat)
        refs = dict(get_references(chat))
        ref = _upsert(refs, kind, norm, "pinned", turn, title=title)
        _save(storage, chat_file, chat, refs)
        return dict(ref)
    except Exception as e:
        logger.warning(f"conversation_references: pin failed (non-fatal): {e}")
        return None


def promote_reference(conversation_id: Optional[str], project_root: Optional[str],
                      ref_id: str) -> Optional[Dict[str, Any]]:
    """Pin an existing (picked-up) reference by id.  None when unknown."""
    if not conversation_id:
        return None
    try:
        opened = _open_chat(conversation_id, project_root)
        if opened is None:
            return None
        storage, chat_file, chat = opened
        refs = dict(get_references(chat))
        ref = refs.get(ref_id)
        if ref is None:
            return None
        if ref.get("provenance") != "pinned":
            ref["provenance"] = "pinned"
            ref["pinned_turn"] = current_turn(chat)
            _save(storage, chat_file, chat, refs)
        return dict(ref)
    except Exception as e:
        logger.warning(f"conversation_references: promote failed (non-fatal): {e}")
        return None


def unpin_reference(conversation_id: Optional[str], project_root: Optional[str],
                    ref_id: str) -> bool:
    """Remove a reference entirely."""
    if not conversation_id:
        return False
    try:
        opened = _open_chat(conversation_id, project_root)
        if opened is None:
            return False
        storage, chat_file, chat = opened
        refs = dict(get_references(chat))
        if refs.pop(ref_id, None) is None:
            return False
        _save(storage, chat_file, chat, refs)
        return True
    except Exception as e:
        logger.warning(f"conversation_references: unpin failed (non-fatal): {e}")
        return False


# ---------------------------------------------------------------------------
# read side
# ---------------------------------------------------------------------------

def is_useful(ref: Dict[str, Any]) -> bool:
    uses = ref.get("uses") or []
    if any(u.get("kind") == "cited" for u in uses):
        return True
    return len({u.get("turn") for u in uses if u.get("kind") == "read"}) >= USEFUL_READ_TURNS


def is_faded(ref: Dict[str, Any], current_turn: int) -> bool:
    if ref.get("provenance") == "pinned" or is_useful(ref):
        return False
    last = int(ref.get("last_used_turn") or ref.get("added_turn") or 0)
    return current_turn - last >= FADE_TURNS


def summarize(chat_data: Optional[Dict[str, Any]], current_turn: int) -> Dict[str, Any]:
    """Counts the UI line shows: pinned, picked up, useful, faded."""
    refs = list(get_references(chat_data).values())
    pinned = [r for r in refs if r.get("provenance") == "pinned"]
    picked = [r for r in refs if r.get("provenance") != "pinned"]
    return {
        "pinned": len(pinned),
        "picked_up": len(picked),
        "useful": sum(1 for r in picked if is_useful(r)),
        "faded": sum(1 for r in picked if is_faded(r, current_turn)),
    }


def _use_summary(ref: Dict[str, Any]) -> str:
    uses = ref.get("uses") or []
    read_turns = sorted({u["turn"] for u in uses if u.get("kind") == "read"})
    cited = sum(1 for u in uses if u.get("kind") == "cited")
    parts = []
    if read_turns:
        parts.append("read turn " + ", ".join(str(t) for t in read_turns[-3:]))
    if cited:
        parts.append(f"cited x{cited}")
    return "; ".join(parts) if parts else "not yet used"


def format_references_notice(conversation_id: Optional[str], project_root: Optional[str],
                             current_turn: int) -> str:
    """One line per listed reference, for the per-turn (non-cached) prompt part.

    Empty string when there is nothing to report or anything goes wrong.
    """
    if not conversation_id:
        return ""
    try:
        opened = _open_chat(conversation_id, project_root)
        if opened is None:
            return ""
        refs = list(get_references(opened[2]).values())
        if not refs:
            return ""
        listed = [r for r in refs if not is_faded(r, current_turn)]
        listed.sort(key=lambda r: (r.get("provenance") != "pinned", -int(r.get("last_used_turn") or 0)))
        lines = []
        for r in listed[:MAX_LISTED]:
            tag = "pinned" if r.get("provenance") == "pinned" else "picked up"
            head = f"- [{r['kind']}, {tag}] {r['locator']}"
            if r.get("title"):
                head += f" — {r['title']}"
            extra = _use_summary(r)
            if r["kind"] in ("file", "pdf") and r.get("last_read_hash"):
                current = _file_hash(r["locator"], project_root)
                if current is None:
                    extra += "; file no longer exists"
                elif current != r["last_read_hash"]:
                    extra += "; CHANGED on disk since last read"
            lines.append(f"{head} ({extra})")
        faded = len(refs) - len(listed)
        if faded:
            lines.append(f"- ({faded} picked-up reference(s) unused for {FADE_TURNS}+ turns not listed)")
        return (
            "References this conversation depends on (link-only; content is NOT "
            "shown here). Expand one with file_read, pdf_read_pages, mcp_fetch or "
            "chat_read when you need it.\n" + "\n".join(lines)
        )
    except Exception as e:
        logger.debug(f"format_references_notice({conversation_id}): {e}")
        return ""


def turn_index(chat_history: Optional[List[Any]]) -> int:
    """1-based index of the turn being answered: prior human messages + 1.

    Accepts the frontend's history (``type``) and the chat record's stored
    messages (``role``), which spell the human side ``human`` or ``user``.
    """
    n = 0
    for m in chat_history or []:
        if isinstance(m, dict) and (m.get("type") in ("human", "user")
                                    or m.get("role") in ("human", "user")):
            n += 1
    return n + 1


def current_turn(chat_data: Optional[Dict[str, Any]]) -> int:
    """The turn the next reply will be, from the chat record's messages."""
    return turn_index((chat_data or {}).get("messages") or [])


def list_references(conversation_id: Optional[str],
                    project_root: Optional[str]) -> Optional[Dict[str, Any]]:
    """What the UI shows: every reference with computed ``useful`` / ``faded``
    flags, pinned first then by recency, plus the summary counts and the
    current turn.  None when the chat record cannot be opened."""
    if not conversation_id:
        return None
    try:
        opened = _open_chat(conversation_id, project_root)
        if opened is None:
            return None
        chat = opened[2]
        turn = current_turn(chat)
        refs = list(get_references(chat).values())
        items = []
        for r in refs:
            items.append({**r, "useful": is_useful(r), "faded": is_faded(r, turn)})
        items.sort(key=lambda r: (r.get("provenance") != "pinned",
                                  -int(r.get("last_used_turn") or r.get("added_turn") or 0)))
        return {"turn": turn, "summary": summarize(chat, turn), "references": items}
    except Exception as e:
        logger.debug(f"list_references({conversation_id}): {e}")
        return None
