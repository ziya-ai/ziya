"""Per-turn shadow-session context for the model (design doc §9, phase 2).

Attaching a session to a conversation is only half the loop: the model
must also *know* the terminal is there without being told each turn.
This module renders one compact tag describing the sessions attached to
the current conversation (and a one-line hint about other live sessions),
for injection onto the current user message.

Why the user message and not the system prompt: the session-context
block in the system prompt is a cached prefix, and this tag changes
every turn (record counts, last activity).  It rides the same relocation
path as ``CurrentDateTime`` so it never invalidates the cached prefix.

Cost discipline: this runs on every request.  Registry scan is a handful
of small files; each attached session gets one ``info`` round-trip with
a short timeout.  Nothing here may raise or block — any failure yields
an empty string.
"""
from typing import Any, Dict, List, Optional

INFO_TIMEOUT_S = 0.3
MAX_SESSIONS_IN_TAG = 6


def attached_sessions(conversation_id: Optional[str]) -> List[Dict[str, Any]]:
    """Structured view of the sessions attached to ``conversation_id``.

    Shared by the model's context tag and the chat UI's input-box chip so
    both always agree on what is attached.  Registry + one short ``info``
    round-trip per session; never raises.
    """
    if not conversation_id:
        return []
    try:
        from app.shadow import registry, client
        sessions = registry.list_sessions()
    except Exception:  # noqa: BLE001
        return []
    out: List[Dict[str, Any]] = []
    for e in sessions:
        if ((e.attached or {}).get("conversation_id") if e.attached else None) != conversation_id:
            continue
        row: Dict[str, Any] = {
            "session_id": e.session_id, "label": e.label, "display": e.display,
            "segmentation": e.segmentation, "headless": e.headless,
            "pending_ask": e.pending_ask, "control": None, "records": None,
            "last_activity": None, "reachable": True,
        }
        try:
            info = client.request(e, "info", timeout=INFO_TIMEOUT_S)
            row["records"] = info.get("tail_seq")
            row["last_activity"] = client._last_ts(info)
            st = client.request(e, "control_status", timeout=INFO_TIMEOUT_S).get("lease")
            if st and st.get("conversation_id") == conversation_id:
                row["control"] = {"restriction": st.get("restriction"),
                                  "policy": st.get("policy"), "granted": st.get("granted")}
        except Exception:  # noqa: BLE001
            row["reachable"] = False
        out.append(row)
        if len(out) >= MAX_SESSIONS_IN_TAG:
            break
    return out


def attached_sessions_tag(conversation_id: Optional[str]) -> str:
    """Return the ``<AttachedShadowSessions>`` tag for this turn, or ``""``.

    Includes every live session whose ``attached.conversation_id`` matches,
    plus a count of other live (unattached / attached-elsewhere) sessions so
    the model knows ``shadow_list`` is worth calling.
    """
    if not conversation_id:
        return ""
    try:
        from app.shadow import registry, client
        sessions = registry.list_sessions()
    except Exception:  # noqa: BLE001 — never let context assembly fail a turn
        return ""
    if not sessions:
        return ""

    mine: List[Any] = []
    unattached = 0
    for e in sessions:
        conv = (e.attached or {}).get("conversation_id") if e.attached else None
        if conv == conversation_id:
            mine.append(e)
        elif conv is None:
            unattached += 1
        # Attached to a *different* conversation: say nothing.  That
        # terminal is another chat's; surfacing it here reads as an
        # invitation to attach, and every unrelated conversation would
        # start asking the user about it.  shadow_list still shows it
        # when the user explicitly asks.

    if not mine and unattached == 0:
        return ""

    lines: List[str] = ["<AttachedShadowSessions>"]
    for e in mine[:MAX_SESSIONS_IN_TAG]:
        detail = _describe(e, client)
        lines.append(f"  {e.display}: {detail}")
    if mine:
        lines.append("  Read with shadow_read(session=<id>); search with search=<regex>; "
                     "page with from_seq. Observe-only: nothing is typed into the session.")
    if unattached:
        lines.append(f"  {unattached} unattached live shadow session(s) on this machine. "
                     "Do not offer to attach unprompted; use shadow_list only if the "
                     "user refers to their terminal.")
    lines.append("</AttachedShadowSessions>")
    return "\n".join(lines)


def _describe(e: Any, client: Any) -> str:
    parts: List[str] = [f"seg={e.segmentation}"]
    if e.headless:
        parts.append("headless")
    try:
        info: Dict[str, Any] = client.request(e, "info", timeout=INFO_TIMEOUT_S)
        tail = info.get("tail_seq")
        if tail:
            parts.append(f"{tail} journal records")
        last = client._last_ts(info)
        if last:
            parts.append(f"last activity {last}")
    except Exception:  # noqa: BLE001 — host busy/dying: describe from the registry only
        parts.append("(host not responding)")
    if e.pending_ask:
        parts.append("PENDING QUESTION from the human at the terminal — "
                     "read the journal for the `ask` record and answer via shadow_comment")
    return ", ".join(parts)
