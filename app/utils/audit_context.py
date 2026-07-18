"""
Co-presence context snapshots for the tool audit log (ASR NF-006 / NF-008).

Forensic honesty note — this is CORRELATION, not CAUSATION.  The findings ask
to link "retrieved content X" → "tool call Y".  A language model does not emit
which context tokens drove a given tool call, so any ``influenced_by:<id>``
claiming a specific causal edge would be FABRICATED attribution — worse than no
log, because an investigator would trust a causal claim the system cannot
substantiate.  Instead we record what was DEMONSTRABLY in the context window
when the call fired (recent tool-result ids + active memory ids), explicitly
labeled ``relation: co_present_at_decision``.  That is the true forensic
primitive: "when curl fired, tool-result t3 and memory prop_abc were present"
— the investigator draws the inference; the log never invents the edge.

Gating: OFF by default (community).  Enabled per-deployment via the env flag
``ZIYA_AUDIT_CONTEXT_SNAPSHOT`` OR by a config provider whose
``should_capture_audit_context()`` returns True (enterprise policy).  Env wins
when explicitly set, so a public user can always opt in with the env var.
"""
from typing import Any, Dict, List, Optional

from app.utils.logging_utils import logger


def is_context_capture_enabled() -> bool:
    """Resolve the capture flag: explicit env var > plugin policy > off.

    - ``ZIYA_AUDIT_CONTEXT_SNAPSHOT`` set (truthy/falsy) is authoritative when
      present, so any user can force it on or off.
    - Otherwise, if any active config provider declares
      ``should_capture_audit_context() -> True``, capture is on (enterprise
      default-on without touching the env).
    - Otherwise off (community default).
    """
    import os
    raw = os.environ.get("ZIYA_AUDIT_CONTEXT_SNAPSHOT")
    if raw is not None:
        return raw.strip().lower() in ("1", "true", "yes")
    try:
        from app.plugins import get_active_config_providers
        for provider in get_active_config_providers():
            fn = getattr(provider, "should_capture_audit_context", None)
            if callable(fn) and fn():
                return True
    except Exception as e:
        logger.debug(f"audit-context policy resolve skipped: {e}")
    return False


def extract_recent_tool_result_ids(
    conversation: List[Dict[str, Any]], limit: int = 10
) -> List[str]:
    """tool_use_ids of tool_result blocks currently in the conversation window.

    Scans newest-first, returns up to *limit* in chronological order.  Tolerant
    of string-content turns and malformed blocks.
    """
    ids: List[str] = []
    for msg in reversed(conversation or []):
        if not isinstance(msg, dict):
            continue
        content = msg.get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if isinstance(block, dict) and block.get("type") == "tool_result":
                tid = block.get("tool_use_id")
                if tid:
                    ids.append(tid)
        if len(ids) >= limit:
            break
    return list(reversed(ids[:limit]))


def build_context_snapshot(
    conversation_id: Optional[str],
    conversation: List[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    """Build the co-presence snapshot, or None when capture is disabled.

    Never raises — a forensics helper must not break the tool it observes.
    """
    if not is_context_capture_enabled():
        return None
    snap: Dict[str, Any] = {"relation": "co_present_at_decision"}
    try:
        snap["recent_tool_result_ids"] = extract_recent_tool_result_ids(conversation)
    except Exception:
        snap["recent_tool_result_ids"] = []
    active_mem: List[str] = []
    if conversation_id:
        try:
            from app.memory.feedback import get_loaded_memory_ids
            active_mem = sorted(get_loaded_memory_ids(conversation_id))
        except Exception:
            active_mem = []
    snap["active_memory_ids"] = active_mem
    return snap


__all__ = [
    "is_context_capture_enabled",
    "extract_recent_tool_result_ids",
    "build_context_snapshot",
]
