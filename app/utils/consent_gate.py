"""
Consent gate for interactive tool calls (design/consent-runtime.md step 3).

Sits in ``app.tool_execution.execute_single_tool`` immediately before a tool
is dispatched.  If the tool's consent mode is ``ask`` and no standing grant
covers this (tool, op) for this conversation/session, it opens a record in the
shared ``ConsentLedger``, emits ``consent_opened`` on the stream, suspends the
chat-turn relay's disconnect grace (so a turn parked on a decision is never
cancelled for having no viewer), and waits for a human answer to land through
``POST /api/consent/{request_id}``.  The model then sees either the tool's
result or an explicit refusal.

Consent mode resolution (``consent_mode``):

    permissions.json  →  {"consent": {"tools": {<tool>: {"default": <mode>,
                                                         "ops": {<op>: <mode>}}}}}
    else the tool's declared ``consent_default`` attribute
    else "off"

Modes: ``off`` (tool does not participate — today's behaviour for every
tool), ``ask`` (per-call human consent), ``always`` (enabled unattended; from
the gate's point of view identical to ``off``, but it is a distinct state
because reaching it from ``ask`` is the signed widening — step 4).

The op of a call is ``args[tool.consent_op_key]`` when the tool declares
``consent_op_key`` (the git tool declares ``"op"``), else ``"*"``.  Grants are
per (tool, op): approving ``commit`` never approves ``push``.

Scope ladder for an approval:

    once          this call only
    conversation  in-memory, keyed on conversation id; a fork starts cold
    session       in-memory, this server process (the relay/session lifetime)
    always        recorded as a *session* grant here; staging it onto the
                  signed path (permissions.json ``enabled/always``) is step 4
                  and the answer event says so.

Every function here is defensive: a failure inside the gate must degrade to
"no gate" (the pre-existing behaviour), never to a stuck turn.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any, AsyncGenerator, Callable, Dict, Optional, Set, Tuple

from .consent_ledger import (
    ConsentCancelled,
    ConsentLedger,
    FileConsentStore,
    conversation_request_id,
)

logger = logging.getLogger(__name__)

MODE_OFF = "off"
MODE_ASK = "ask"
MODE_ALWAYS = "always"
_MODES = (MODE_OFF, MODE_ASK, MODE_ALWAYS)

SCOPE_ONCE = "once"
SCOPE_CONVERSATION = "conversation"
SCOPE_SESSION = "session"
SCOPE_ALWAYS = "always"
SCOPES = (SCOPE_ONCE, SCOPE_CONVERSATION, SCOPE_SESSION, SCOPE_ALWAYS)

# An unanswered chat consent expires as a reject after this long, so a held
# turn ends deterministically (design §Open: proposed 24 h).
DEFAULT_TTL_MS = 24 * 3600 * 1000

# The terminal item the gate generator yields; the caller consumes it rather
# than forwarding it to the client.
DECISION_EVENT = "_consent_decision"

# Relay hold reason prefix; one hold per request so overlapping asks compose.
_HOLD_PREFIX = "consent:"


# ── mode resolution ───────────────────────────────────────────────────────

def _consent_config() -> Dict[str, Any]:
    try:
        from app.mcp.permissions import get_permissions_manager
        perms = get_permissions_manager().get_permissions() or {}
        cfg = perms.get("consent") or {}
        return cfg if isinstance(cfg, dict) else {}
    except Exception as exc:  # noqa: BLE001
        logger.debug(f"consent config unavailable: {exc}")
        return {}


def consent_mode(tool_name: str, op: str, tool_instance: Any = None) -> str:
    """Resolve the consent mode for one (tool, op).  Never raises."""
    tools = (_consent_config().get("tools") or {})
    entry = tools.get(tool_name)
    if isinstance(entry, dict):
        ops = entry.get("ops") or {}
        mode = ops.get(op) if isinstance(ops, dict) else None
        if mode in _MODES:
            return mode
        mode = entry.get("default")
        if mode in _MODES:
            return mode
    elif isinstance(entry, str) and entry in _MODES:
        return entry
    declared = getattr(tool_instance, "consent_default", None)
    if declared in _MODES:
        return declared
    return MODE_OFF


def set_consent_mode(tool_name: str, mode: str, op: Optional[str] = None) -> None:
    """Persist a consent mode for a tool (or one op of it) in permissions.json.

    Widening to ``always`` is the signed step (step 4); this function is the
    unsigned write used for ``off``/``ask`` and by the signed path once it has
    verified the approval.  Callers are responsible for that gate.
    """
    if mode not in _MODES:
        raise ValueError(f"mode must be one of {_MODES}, got {mode!r}")
    from app.mcp.permissions import get_permissions_manager
    mgr = get_permissions_manager()
    perms = mgr.get_permissions() or {}
    consent = perms.setdefault("consent", {})
    tools = consent.setdefault("tools", {})
    entry = tools.get(tool_name)
    if not isinstance(entry, dict):
        entry = {"default": entry} if isinstance(entry, str) else {}
        tools[tool_name] = entry
    if op:
        entry.setdefault("ops", {})[op] = mode
    else:
        entry["default"] = mode
    mgr.save_permissions(perms)


def op_for_call(tool_instance: Any, args: Dict[str, Any]) -> str:
    key = getattr(tool_instance, "consent_op_key", None)
    if key and isinstance(args, dict):
        val = args.get(key)
        if val not in (None, ""):
            return str(val)
    return "*"


# ── standing grants ───────────────────────────────────────────────────────

class GrantCache:
    """In-memory grants for the ``conversation`` and ``session`` scopes.

    Process-lifetime by construction: a restart clears every grant, which is
    the conservative reading of "this session".  ``always`` is not held here;
    it lives in permissions.json (step 4).
    """

    def __init__(self) -> None:
        self._conversation: Set[Tuple[str, str, str]] = set()
        self._session: Set[Tuple[str, str]] = set()

    def covers(self, tool: str, op: str, conversation_id: Optional[str]) -> bool:
        if (tool, op) in self._session:
            return True
        return bool(conversation_id) and (conversation_id, tool, op) in self._conversation

    def remember(self, scope: str, tool: str, op: str, conversation_id: Optional[str]) -> None:
        if scope == SCOPE_CONVERSATION and conversation_id:
            self._conversation.add((conversation_id, tool, op))
        elif scope in (SCOPE_SESSION, SCOPE_ALWAYS):
            self._session.add((tool, op))

    def forget_conversation(self, conversation_id: str) -> None:
        self._conversation = {k for k in self._conversation if k[0] != conversation_id}

    def reset(self) -> None:
        self._conversation.clear()
        self._session.clear()


_grants = GrantCache()


def grants() -> GrantCache:
    return _grants


# ── ledger access ─────────────────────────────────────────────────────────

def _consents_dir() -> Path:
    from app.utils.paths import get_ziya_home
    return Path(get_ziya_home()) / "consents"


_ledger: Optional[ConsentLedger] = None


def ledger() -> ConsentLedger:
    """The process-wide chat-consent ledger (``conv:*`` records)."""
    global _ledger
    if _ledger is None:
        _ledger = ConsentLedger(FileConsentStore(_consents_dir()))
    return _ledger


def reset_for_tests(base_dir: Optional[Path] = None) -> None:
    global _ledger
    _ledger = ConsentLedger(FileConsentStore(base_dir)) if base_dir else None
    _grants.reset()


# ── preflight ─────────────────────────────────────────────────────────────

async def _preflight(tool_instance: Any, args: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Tool-declared ``preflight(**args) -> dict`` (sync or async), or None."""
    fn = getattr(tool_instance, "preflight", None)
    if not callable(fn):
        return None
    try:
        out = fn(**(args or {}))
        if hasattr(out, "__await__"):
            out = await out
        return out if isinstance(out, dict) else {"value": out}
    except Exception as exc:  # noqa: BLE001 — preflight is advisory
        return {"error": str(exc)[:300]}


def _unwrap(tool: Any) -> Any:
    return getattr(tool, "tool_instance", tool)


# ── the gate ──────────────────────────────────────────────────────────────

def refusal_text(tool_name: str, op: str, answer: Dict[str, Any]) -> str:
    who = (answer.get("answered_by") or "the user").strip() or "the user"
    reason = (answer.get("answer") or "").strip()
    label = f"{tool_name}" + (f" {op}" if op and op != "*" else "")
    text = f"Tool call refused: {who} declined to allow `{label}`."
    if reason:
        text += f" Reason: {reason}"
    text += " Do not retry this call; ask the user how to proceed or choose a different approach."
    return text


async def consent_gate(
    *,
    tool_name: str,
    tool_id: str,
    args: Dict[str, Any],
    conversation_id: Optional[str],
    tool: Any = None,
    cancel_requested: Optional[Callable[[], bool]] = None,
    ttl_ms: int = DEFAULT_TTL_MS,
) -> AsyncGenerator[Dict[str, Any], None]:
    """Yield stream events, ending with a ``DECISION_EVENT``.

    The decision event is ``{"type": DECISION_EVENT, "approved": bool,
    "answer": {...} | None, "asked": bool}``.  ``asked`` is False when the
    gate did not need to consult anyone (mode off/always, or a standing
    grant), so callers can tell "approved silently" from "approved by hand".
    """
    inst = _unwrap(tool)
    op = op_for_call(inst, args)
    try:
        mode = consent_mode(tool_name, op, inst)
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"consent mode lookup failed for {tool_name}; not gating: {exc}")
        mode = MODE_OFF

    if mode != MODE_ASK or _grants.covers(tool_name, op, conversation_id):
        yield {"type": DECISION_EVENT, "approved": True, "answer": None, "asked": False}
        return

    if not conversation_id:
        # No conversation → nowhere for an answer to arrive (no relay, no
        # panel).  Refuse rather than hang: an ``ask``-mode tool with no human
        # reachable is by definition not approved.
        answer = {"decision": "reject", "answer": "no interactive conversation to ask",
                  "answered_by": "system", "scope": SCOPE_ONCE}
        yield {"type": DECISION_EVENT, "approved": False, "answer": answer, "asked": False}
        return

    request_id = conversation_request_id(conversation_id, tool_id)
    payload = {
        "kind": "tool_call",
        "tool": tool_name,
        "op": op,
        "args": args,
        "preflight": await _preflight(inst, args),
        "tool_id": tool_id,
    }
    led = ledger()
    rec = led.open(request_id, payload, owner_id=conversation_id, ttl_ms=ttl_ms)
    hold_reason = _HOLD_PREFIX + request_id
    held = False
    try:
        from app.agents import chat_turn_relay as _relay
        held = await _relay.hold(conversation_id, hold_reason)
    except Exception as exc:  # noqa: BLE001
        logger.debug(f"relay hold unavailable: {exc}")

    yield {
        "type": "consent_opened",
        "request_id": request_id,
        "conversation_id": conversation_id,
        "tool_id": tool_id,
        "payload": payload,
        "scopes": list(SCOPES),
        "opened_at": rec.opened_at,
        "ttl_ms": ttl_ms,
        "at": time.time(),
    }

    answer: Optional[Dict[str, Any]] = None
    try:
        answer = await led.await_answer(request_id, cancel_requested)
    except ConsentCancelled:
        answer = {"decision": "reject", "answer": "cancelled", "answered_by": "system",
                  "scope": SCOPE_ONCE}
    finally:
        try:
            led.close(request_id)
        except Exception:  # noqa: BLE001
            pass
        if held:
            try:
                await _relay.release(conversation_id, hold_reason)
            except Exception:  # noqa: BLE001
                pass

    decision = str(answer.get("decision") or "reject").lower()
    scope = str(answer.get("scope") or SCOPE_ONCE)
    approved = decision == "approve"
    if approved:
        _grants.remember(scope, tool_name, op, conversation_id)

    yield {
        "type": "consent_answered",
        "request_id": request_id,
        "conversation_id": conversation_id,
        "tool_id": tool_id,
        "decision": decision,
        "scope": scope,
        "answered_by": answer.get("answered_by") or "",
        "answer": answer.get("answer") or "",
        # ``always`` is honoured as a session grant here; making it durable
        # is the signed widening in step 4.
        "always_pending_signature": bool(approved and scope == SCOPE_ALWAYS),
        "at": time.time(),
    }
    yield {"type": DECISION_EVENT, "approved": approved, "answer": answer, "asked": True}
