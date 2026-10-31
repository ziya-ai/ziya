"""
Resolve a folded tributary handle back to the chat record.

design/tributary-fold.md, slice 3.  A ``tb-…`` handle in replayed history
names the exact substring of an assistant message that runs from its
first tool block to its last (``fold_handle`` in tool_history_rewrite).
This module finds that substring in the persisted record and renders it
for the model:

  * ``full``     — the span re-encoded through ``rewrite_assistant_text``,
                   so it arrives in the inert ‹tool_result› form rather than
                   the renderer's fence form;
  * ``commands`` — one label per tool call, so the model can re-run them
                   against current state instead of re-reading a stale result;
  * ``results``  — each call's label followed by its payload.

The record, not the replay, is the source: a message that was since muted
still resolves, and spans that never met the fold thresholds resolve too
(the handle is content-addressed over any assistant segment with at least
one block).  Pure functions; the tool in app/mcp/tools/tributary_tools.py
supplies the record and the conversation scoping.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from app.utils.tool_history_rewrite import (
    FOLD_HANDLE_PREFIX, ToolBlock, _clean_label, _non_overlapping, _norm_tool,
    _text_segments, find_tool_blocks, fold_handle, payload_for_block,
    rewrite_assistant_text,
)

_ASSISTANT_ROLES = frozenset({"assistant", "ai"})
MODES = ("full", "commands", "results")
DEFAULT_MAX_CHARS = 60_000


@dataclass(frozen=True)
class Tributary:
    handle: str
    message_index: int      # index in the record's ``messages`` list
    segment_index: int      # text segment within that message
    muted: bool
    span: str               # exact record substring
    blocks: Tuple[ToolBlock, ...]   # offsets relative to the SEGMENT text


def iter_tributaries(record: Dict[str, Any]):
    """Yield every span a handle could name, in record order."""
    for mi, msg in enumerate(record.get("messages") or []):
        if not isinstance(msg, dict):
            continue
        if (msg.get("role") or msg.get("type") or "") not in _ASSISTANT_ROLES:
            continue
        for si, text in enumerate(_text_segments(msg.get("content"))):
            if "tool:" not in text and "TOOL_BLOCK_START" not in text:
                continue
            blocks = _non_overlapping(find_tool_blocks(text))
            if not blocks:
                continue
            span = text[blocks[0].start:blocks[-1].end]
            yield Tributary(
                handle=fold_handle(span), message_index=mi, segment_index=si,
                muted=bool(msg.get("muted")), span=span, blocks=tuple(blocks),
            )


def find_tributary(record: Dict[str, Any], handle: str) -> Optional[Tributary]:
    """The tributary whose content address is ``handle``, or None."""
    h = (handle or "").strip()
    if not h.startswith(FOLD_HANDLE_PREFIX):
        h = FOLD_HANDLE_PREFIX + h
    for t in iter_tributaries(record):
        if t.handle == h:
            return t
    return None


def _label(b: ToolBlock) -> str:
    return _clean_label(b.header) or _norm_tool(b.tool)


def render_tributary(t: Tributary, mode: str = "full") -> str:
    """Render a tributary in one of ``MODES``."""
    if mode == "commands":
        return "\n".join(_label(b) for b in t.blocks)
    if mode == "results":
        parts: List[str] = []
        for b in t.blocks:
            parts.append(f"## {_label(b)}")
            parts.append(payload_for_block(b).rstrip())
        return "\n".join(parts)
    # full: the span is itself a valid assistant text (starts and ends on a
    # block), so the ordinary rewrite re-encodes every block to an envelope
    # and defangs the interim prose.
    return rewrite_assistant_text(t.span)


def grep_lines(text: str, pattern: str) -> str:
    """Keep only the lines matching ``pattern`` (case-insensitive regex)."""
    try:
        rx = re.compile(pattern, re.IGNORECASE)
    except re.error:
        rx = re.compile(re.escape(pattern), re.IGNORECASE)
    return "\n".join(line for line in text.splitlines() if rx.search(line))


def cap(text: str, max_chars: int) -> Tuple[str, bool]:
    if max_chars and len(text) > max_chars:
        return (text[:max_chars]
                + f"\n[… {len(text) - max_chars:,} more chars; narrow with grep= "
                f"or raise max_chars]"), True
    return text, False


def recall(record: Dict[str, Any], handle: str, mode: str = "full",
           grep: Optional[str] = None,
           max_chars: int = DEFAULT_MAX_CHARS) -> Dict[str, Any]:
    """Resolve ``handle`` in ``record`` and render it.

    An unknown handle is reported as ``found: False`` with a plain
    "not found in this conversation" message — never as an error, which
    the model could read as a verdict on the earlier work.
    """
    if mode not in MODES:
        return {"found": False, "error": True,
                "message": f"mode must be one of {', '.join(MODES)}"}
    t = find_tributary(record, handle)
    if t is None:
        return {"found": False, "handle": handle,
                "message": (f"Tributary {handle} not found in this conversation. "
                            f"Handles are scoped to the conversation they were "
                            f"issued in; nothing about the earlier result is "
                            f"implied.")}
    text = render_tributary(t, mode)
    if grep:
        text = grep_lines(text, grep)
    text, truncated = cap(text, max_chars)
    return {
        "found": True,
        "handle": t.handle,
        "message_index": t.message_index,
        "muted": t.muted,
        "mode": mode,
        "n_calls": len(t.blocks),
        "span_chars": len(t.span),
        "truncated": truncated,
        "text": text,
    }


__all__ = ["Tributary", "MODES", "DEFAULT_MAX_CHARS", "iter_tributaries",
           "find_tributary", "render_tributary", "grep_lines", "recall"]
