"""Session-signal gating of builtin tool categories.

``BUILTIN_TOOL_CATEGORIES`` decides which tools *exist* in a process; it is
static and its result is cached for the process lifetime.  Some categories,
though, are only useful under a condition that is knowable per request and
that the tool itself checks at call time and fails on otherwise:

* ``task_artifacts`` — ``emit_artifact`` returns "only available inside a
  Task Card run" unless an artifact collector is open in this context.

Shipping that definition on every turn of every conversation where it
cannot succeed is pure tax (~1.1k tokens of the default builtin payload).
This module drops it per request, at the one seam every request passes
through (``StreamingToolExecutor._load_and_prepare_tools``), so the
process-level tool cache is untouched.

Why only one category.  The tool list is the FIRST segment of the provider
request prefix (tools -> system -> messages), so any change to it misses
every prompt-cache breakpoint downstream -- system prompt, history, files.
A gate is therefore only safe if its signal cannot change within a
conversation.  ``task_artifacts`` qualifies: an artifact collector is open
for a whole task run or not at all.  Two further gates were built and
withdrawn because their signals CAN flip mid-conversation and the cache
cost of the flip outweighs the tokens saved:

* ``shadow`` (a ``ziya shadow`` session is live) -- a session can be
  started at any turn.
* ``pdf_rag`` (a PDF is indexed or mentioned) -- a PDF can be added at any
  turn, and with files positioned last in the prompt that add would
  otherwise cost only the file segment, not the whole prefix.

Their signal detectors and ``SessionSignals`` fields are retained so a
per-model tool profile (a small local model with an 8k budget may prefer
the tokens over the cache) can re-enable them deliberately; they are
simply not in ``CATEGORY_GATES``.

Only *hard* signals are used — facts about process/session state, not
guesses about user intent from prose.  A tool explicitly requested by a
Task block's ``scope.tools`` is never gated: the author asked for it by
name, and a scope that names a tool the session cannot use is reported
through ``unmatched_scope_tools``, not silently amputated here.

``ZIYA_TOOL_GATING=0`` disables gating entirely (every enabled category
is sent every turn, the pre-gating behaviour).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Set, Tuple

from app.utils.logging_utils import logger


@dataclass(frozen=True)
class SessionSignals:
    """Per-request facts that decide which gated categories are exposed."""
    in_task_run: bool = False
    shadow_sessions: bool = False
    pdf_relevant: bool = False


# category -> predicate over SessionSignals.  Categories absent here are
# never gated by this module.  Only add a gate whose signal cannot change
# within a conversation (see module docstring); ``shadow`` and ``pdf_rag``
# were removed for exactly that reason.
CATEGORY_GATES: Dict[str, Callable[[SessionSignals], bool]] = {
    "task_artifacts": lambda s: s.in_task_run,
}


def gating_enabled() -> bool:
    return os.environ.get("ZIYA_TOOL_GATING", "1").lower() not in ("0", "false", "no")


# ---------------------------------------------------------------------------
# Signal detection
# ---------------------------------------------------------------------------

def _in_task_run() -> bool:
    try:
        from app.utils.task_artifacts import collection_active
        return collection_active()
    except Exception:  # pragma: no cover - import failure means no task machinery
        return False


def _shadow_sessions_live() -> bool:
    """True if any ``ziya shadow`` session registry entry is alive.

    Uses the registry (a directory glob + liveness check), not
    ``app.shadow.client.list_sessions``, which additionally performs a
    socket round-trip per session and is far too slow for a per-request
    probe.  ``sessions_dir()`` creates ``~/.ziya/shadow/sessions`` if
    missing; skip the call entirely when the directory does not exist so
    a never-used feature leaves no footprint.
    """
    try:
        d = Path(os.path.expanduser("~/.ziya/shadow/sessions"))
        if not d.is_dir():
            return False
        from app.shadow import registry
        return bool(registry.list_sessions())
    except Exception as e:
        logger.debug(f"shadow session probe failed: {e}")
        return False


def _messages_text(messages: Optional[Iterable[Any]]) -> str:
    """Flatten message content to one lowercase string for literal scans."""
    parts: List[str] = []
    for m in messages or []:
        content = m.get("content") if isinstance(m, dict) else getattr(m, "content", None)
        if isinstance(content, str):
            parts.append(content)
        elif isinstance(content, list):
            for block in content:
                if isinstance(block, dict):
                    t = block.get("text")
                    if isinstance(t, str):
                        parts.append(t)
                elif isinstance(block, str):
                    parts.append(block)
    return "\n".join(parts).lower()


def _pdf_relevant(messages: Optional[Iterable[Any]], project_root: Optional[str]) -> bool:
    if project_root:
        try:
            idx = Path(project_root) / ".ziya" / "pdf_index"
            if idx.is_dir() and any(idx.iterdir()):
                return True
        except OSError:
            pass
    return ".pdf" in _messages_text(messages)


# ---------------------------------------------------------------------------
# Per-conversation hysteresis
# ---------------------------------------------------------------------------
#
# The tool list is the FIRST segment of the provider request prefix
# (tools -> system -> messages), and every prompt-cache breakpoint sits
# downstream of it.  Any change to the tool set therefore misses every
# breakpoint on that turn -- system prompt, files, history -- for both
# explicit caching (Anthropic/Bedrock) and KV-prefix reuse (Ollama,
# llama.cpp).  With the active gate table (task_artifacts only) no signal
# can flip within a conversation, so this is belt-and-braces: it guarantees
# that a gate which somehow opened can never CLOSE again and cost a second
# miss, and it is the mechanism a per-model profile re-enabling the
# withdrawn shadow/pdf gates would rely on.
#
# Gates are sticky per conversation: once a signal has been observed
# true for a conversation_id it is reported true for the rest of that
# conversation's life in this process.  Bounded LRU so an idle server
# does not accumulate one entry per conversation ever seen.

_STICKY_MAX_CONVERSATIONS = 1024
_sticky_open: "Dict[str, Set[str]]" = {}


def _remember_and_widen(conversation_id: Optional[str], signals: SessionSignals) -> SessionSignals:
    if not conversation_id:
        return signals
    now_open = {f for f in signals.__dataclass_fields__ if getattr(signals, f)}
    prior = _sticky_open.pop(conversation_id, set())
    merged = prior | now_open
    _sticky_open[conversation_id] = merged  # re-insert: most recent last
    while len(_sticky_open) > _STICKY_MAX_CONVERSATIONS:
        _sticky_open.pop(next(iter(_sticky_open)))
    if merged == now_open:
        return signals
    return SessionSignals(**{f: (f in merged) for f in signals.__dataclass_fields__})


def forget_conversation_gates(conversation_id: str) -> None:
    """Drop the sticky record for a conversation (tests; conversation delete)."""
    _sticky_open.pop(conversation_id, None)


def detect_session_signals(
    messages: Optional[Iterable[Any]] = None,
    project_root: Optional[str] = None,
    conversation_id: Optional[str] = None,
) -> SessionSignals:
    """Current gate signals, widened by any gate already opened for
    ``conversation_id`` (see the hysteresis note above).  Without a
    conversation_id the raw per-request signals are returned."""
    raw = SessionSignals(
        in_task_run=_in_task_run(),
        shadow_sessions=_shadow_sessions_live(),
        pdf_relevant=_pdf_relevant(messages, project_root),
    )
    return _remember_and_widen(conversation_id, raw)


# ---------------------------------------------------------------------------
# Tool -> category resolution
# ---------------------------------------------------------------------------

_tool_category_cache: Optional[Dict[str, str]] = None


def tool_category_map() -> Dict[str, str]:
    """``tool_name -> category`` for every builtin category, gated or not.

    Built once per process by instantiating each category's tool classes
    (``name`` is an instance property).  Only categories present in
    ``CATEGORY_GATES`` are consulted by the filter, but the full map is
    cheap and useful to callers reporting what was dropped.
    """
    global _tool_category_cache
    if _tool_category_cache is not None:
        return _tool_category_cache
    mapping: Dict[str, str] = {}
    try:
        from app.mcp.builtin_tools import (
            BUILTIN_TOOL_CATEGORIES, get_builtin_tools_for_category,
        )
        for category in BUILTIN_TOOL_CATEGORIES:
            for cls in get_builtin_tools_for_category(category):
                try:
                    mapping[cls().name] = category
                except Exception as e:  # a broken tool must not break gating
                    logger.debug(f"tool_category_map: could not instantiate {cls}: {e}")
    except Exception as e:
        logger.debug(f"tool_category_map: builtin registry unavailable: {e}")
    _tool_category_cache = mapping
    return mapping


def invalidate_tool_category_cache() -> None:
    global _tool_category_cache
    _tool_category_cache = None


# ---------------------------------------------------------------------------
# Filter
# ---------------------------------------------------------------------------

def gated_out_categories(signals: SessionSignals) -> Set[str]:
    """Categories whose gate is closed under ``signals``."""
    return {cat for cat, gate in CATEGORY_GATES.items() if not gate(signals)}


def filter_tools_by_session(
    tools: List[Any],
    signals: SessionSignals,
    keep: Optional[Iterable[str]] = None,
) -> Tuple[List[Any], List[str]]:
    """Drop tools whose category gate is closed.

    ``tools`` items need only expose ``name``.  ``keep`` is a set of names
    that must survive regardless (a task scope's explicit allowlist);
    matching is prefix-normalized like the scope resolver so
    ``run_shell_command`` protects ``mcp_run_shell_command``.

    Returns ``(kept_tools, dropped_names)``.  Returns the input unchanged
    when gating is disabled or nothing is gated out.
    """
    if not gating_enabled():
        return tools, []
    closed = gated_out_categories(signals)
    if not closed:
        return tools, []

    from app.utils.task_tool_floor import normalize_tool_name
    protected = {normalize_tool_name(k) for k in (keep or []) if k}
    categories = tool_category_map()

    kept: List[Any] = []
    dropped: List[str] = []
    for tool in tools:
        name = getattr(tool, "name", "") or ""
        category = categories.get(name) or categories.get(normalize_tool_name(name))
        if category in closed and normalize_tool_name(name) not in protected:
            dropped.append(name)
        else:
            kept.append(tool)
    return kept, dropped


__all__ = [
    "SessionSignals",
    "CATEGORY_GATES",
    "detect_session_signals",
    "filter_tools_by_session",
    "gated_out_categories",
    "gating_enabled",
    "tool_category_map",
    "invalidate_tool_category_cache",
]
