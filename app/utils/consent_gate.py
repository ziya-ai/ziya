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
from typing import Any, AsyncGenerator, Callable, Dict, List, Optional, Set, Tuple

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
    return declared_mode(tool_instance, op)


def declared_mode(tool_instance: Any, op: str) -> str:
    """The mode a tool declares for itself, ignoring configuration.

    ``consent_op_defaults`` (``{op: mode}``) wins over ``consent_default`` so
    a tool can exempt its read-only ops — the git tool's ``status`` must
    not prompt.  This is also the FAIL-CLOSED fallback when configuration
    cannot be read: a tool that declares ``ask`` stays ``ask``.  Never raises.
    """
    try:
        per_op = getattr(tool_instance, "consent_op_defaults", None)
        if isinstance(per_op, dict) and per_op.get(op) in _MODES:
            return per_op[op]
        declared = getattr(tool_instance, "consent_default", None)
        if declared in _MODES:
            return declared
    except Exception:  # noqa: BLE001
        pass
    return MODE_OFF


def human_only_ops(tool_instance: Any) -> Set[str]:
    """Ops a tool requires a human to approve on EVERY call.

    A tool declares ``consent_human_only_ops = ("op", ...)``.  For those ops
    no configuration (permissions.json ``always``), no autonomous project
    mode and no standing grant skips the prompt, and the panel offers only
    ``once``.  The git tool uses it for history rewriting (``commit_amend``,
    ``push_force``).  Never raises.
    """
    try:
        ops = getattr(tool_instance, "consent_human_only_ops", None)
        if isinstance(ops, (list, tuple, set, frozenset)):
            return {o for o in ops if isinstance(o, str) and o}
    except Exception:  # noqa: BLE001
        pass
    return set()


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


def op_for_call(tool_instance: Any, args: Dict[str, Any],
                conversation_id: Optional[str] = None) -> str:
    """The consent op for one call.

    A tool whose op depends on state rather than on one argument (e.g.
    memory_delete: "did THIS conversation save the memory?") declares
    ``consent_op_for(args, conversation_id) -> str``.  It wins over
    ``consent_op_key``.  A hook that raises yields "*", which resolves to
    the tool's declared default, so a broken hook fails to that default
    rather than to an exempt op.  Callers without a conversation pass
    None; the hook must treat that as the least-trusted case.
    """
    hook = getattr(tool_instance, "consent_op_for", None)
    if callable(hook):
        try:
            op = hook(dict(args or {}), conversation_id)
            return str(op) if op not in (None, "") else "*"
        except Exception as exc:  # noqa: BLE001
            logger.debug(f"consent_op_for failed: {exc}")
            return "*"
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
        # Approved out-of-policy paths (design/scope-consent.md C2), keyed
        # per tool on the exact realpath: (conv, tool, root, path) and
        # (tool, root, path).  Not per op: the paths are what was shown.
        self._conversation_paths: Set[Tuple[str, str, str, str]] = set()
        self._session_paths: Set[Tuple[str, str, str]] = set()

    def covers(self, tool: str, op: str, conversation_id: Optional[str]) -> bool:
        if (tool, op) in self._session:
            return True
        return bool(conversation_id) and (conversation_id, tool, op) in self._conversation

    def remember(self, scope: str, tool: str, op: str, conversation_id: Optional[str]) -> None:
        if scope == SCOPE_CONVERSATION and conversation_id:
            self._conversation.add((conversation_id, tool, op))
        elif scope in (SCOPE_SESSION, SCOPE_ALWAYS):
            self._session.add((tool, op))

    def remember_paths(self, scope: str, tool: str, conversation_id: Optional[str],
                       root: Optional[str], paths: list) -> None:
        """Remember approved paths for *scope*.  ``once`` remembers nothing;
        floor paths are never remembered (``write_grants.grant_key``)."""
        if scope not in (SCOPE_CONVERSATION, SCOPE_SESSION, SCOPE_ALWAYS):
            return
        from app.utils.write_grants import grant_key
        for p in paths or ():
            key = grant_key(p, root)
            if key is None:
                continue
            if scope == SCOPE_CONVERSATION:
                if conversation_id:
                    self._conversation_paths.add((conversation_id, tool, *key))
            else:
                self._session_paths.add((tool, *key))

    def covers_paths(self, tool: str, conversation_id: Optional[str],
                     root: Optional[str], paths: list) -> bool:
        """True only when EVERY path is remembered for this tool.  The floor
        is re-checked here, so a path that became floor (e.g. now a symlink
        out of the project) is not covered."""
        if not paths:
            return False
        from app.utils.write_grants import grant_key
        for p in paths:
            key = grant_key(p, root)
            if key is None:
                return False
            if (tool, *key) in self._session_paths:
                continue
            if conversation_id and (conversation_id, tool, *key) in self._conversation_paths:
                continue
            return False
        return True

    def forget_conversation(self, conversation_id: str) -> None:
        self._conversation = {k for k in self._conversation if k[0] != conversation_id}
        self._conversation_paths = {k for k in self._conversation_paths if k[0] != conversation_id}

    def reset(self) -> None:
        self._conversation.clear()
        self._session.clear()
        self._conversation_paths.clear()
        self._session_paths.clear()


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

async def _preflight(
    tool_instance: Any, args: Dict[str, Any], project_root: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Tool-declared ``preflight(**args) -> dict`` (sync or async), or None."""
    fn = getattr(tool_instance, "preflight", None)
    if not callable(fn):
        return None
    try:
        kw = dict(args or {})
        # The gate runs before the executor injects ``_workspace_path``, so
        # hand the preflight the SAME root the execution will use.  It rides
        # the preflight call only, never the payload args the panel shows.
        if project_root:
            kw.setdefault("_workspace_path", project_root)
        out = fn(**kw)
        if hasattr(out, "__await__"):
            out = await out
        return out if isinstance(out, dict) else {"value": out}
    except Exception as exc:  # noqa: BLE001 — preflight is advisory
        return {"error": str(exc)[:300]}


def _unwrap(tool: Any) -> Any:
    return getattr(tool, "tool_instance", tool)


def fails_closed(tool: Any, args: Dict[str, Any]) -> bool:
    """True when a gate malfunction must REFUSE this call rather than dispatch.

    The executor degrades a broken gate to "ungated" so an ordinary tool is
    never stuck behind a consent bug.  That is wrong for a tool that declares
    ``ask`` for this op: unattended execution is exactly what it opted out
    of.  Configuration is deliberately not consulted — it may be what broke.
    Never raises; an unreadable tool is treated as not declaring ``ask``.
    """
    try:
        inst = _unwrap(tool)
        return declared_mode(inst, op_for_call(inst, args or {})) == MODE_ASK
    except Exception:  # noqa: BLE001
        return False


# ── the gate ──────────────────────────────────────────────────────────────

ANSWERED_BY_PREFLIGHT = "preflight"
ANSWERED_BY_POLICY = "write policy"
_DETAIL_CAP = 2000
_ARG_DISPLAY_CAP = 4000


def _wants_scope_preflight(inst: Any) -> bool:
    """A tool whose writes can fall outside the write policy declares
    ``scope_preflight = True`` so its preflight runs even when consent is off."""
    try:
        return getattr(inst, "scope_preflight", False) is True
    except Exception:  # noqa: BLE001
        return False


def _is_refusal(preflight: Any) -> bool:
    # ``error: True`` is the tool's own refusal.  A preflight that RAISED
    # comes back as ``{"error": "<text>"}`` and stays advisory.
    return isinstance(preflight, dict) and preflight.get("error") is True


def _outside_policy(preflight: Any) -> list:
    if not isinstance(preflight, dict):
        return []
    from app.utils.write_grants import outside_entries
    return outside_entries(preflight.get("outside_policy") or [])


def _grant_root(project_root: Optional[str]) -> Optional[str]:
    if project_root:
        return project_root
    try:
        from app.context import get_project_root
        return get_project_root()
    except Exception:  # noqa: BLE001
        return None


EDIT_MAX_CHARS = 20000


def editable_args(tool_instance: Any, op: str) -> List[str]:
    """Argument names the human may rewrite in the approval panel for this op.

    A tool declares ``consent_editable_args = {op: (arg, ...)}``.  The value
    the human approves then replaces the model's (git ``commit`` /
    ``stash_push`` message).  Only string arguments that do not change WHAT
    the call touches belong here: the preflight shown is not recomputed.
    Never raises; anything malformed means nothing is editable.
    """
    try:
        per_op = getattr(tool_instance, "consent_editable_args", None)
        if not isinstance(per_op, dict):
            return []
        keys = per_op.get(op) or ()
        return [k for k in keys if isinstance(k, str) and k]
    except Exception:  # noqa: BLE001
        return []


def sanitize_edits(edits: Any, allowed: List[str]) -> Dict[str, str]:
    """The subset of ``edits`` the gate will apply: allowed keys, string
    values, within EDIT_MAX_CHARS.  Anything else is dropped (the API has
    already refused it with a 422; this is the second line)."""
    if not isinstance(edits, dict) or not allowed:
        return {}
    return {k: v for k, v in edits.items()
            if k in allowed and isinstance(v, str) and len(v) <= EDIT_MAX_CHARS}


def _display_args(args: Dict[str, Any]) -> Dict[str, Any]:
    """Args for the panel and the ledger file: long strings (file_write
    content) are truncated.  Execution always uses the real args."""
    out: Dict[str, Any] = {}
    for k, v in (args or {}).items():
        if isinstance(v, str) and len(v) > _ARG_DISPLAY_CAP:
            v = v[:_ARG_DISPLAY_CAP] + f"… ({len(v) - _ARG_DISPLAY_CAP} more characters)"
        out[k] = v
    return out


def _not_run_text(label: str, reason: str, detail: Any) -> str:
    text = f"`{label}` was not run: {reason or 'the call cannot run'}."
    if isinstance(detail, dict):
        extra = {k: v for k, v in detail.items() if k not in ("error", "op", "message")}
        if extra:
            import json
            blob = json.dumps(extra, default=str)
            text += " Details: " + (blob if len(blob) <= _DETAIL_CAP else blob[:_DETAIL_CAP] + "…")
    return text + " Fix the arguments and call again, or ask the user how to proceed."


def refusal_text(tool_name: str, op: str, answer: Dict[str, Any]) -> str:
    who = (answer.get("answered_by") or "the user").strip() or "the user"
    reason = (answer.get("answer") or "").strip()
    label = f"{tool_name}" + (f" {op}" if op and op != "*" else "")
    if who in (ANSWERED_BY_PREFLIGHT, ANSWERED_BY_POLICY):
        # Nobody declined anything: the call could not run as asked.
        return _not_run_text(label, reason, answer.get("detail"))
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
    project_root: Optional[str] = None,
) -> AsyncGenerator[Dict[str, Any], None]:
    """Yield stream events, ending with a ``DECISION_EVENT``.

    The decision event is ``{"type": DECISION_EVENT, "approved": bool,
    "answer": {...} | None, "asked": bool}``.  ``asked`` is False when the
    gate did not need to consult anyone (mode off/always, or a standing
    grant), so callers can tell "approved silently" from "approved by hand".
    """
    inst = _unwrap(tool)
    op = op_for_call(inst, args, conversation_id)

    # Per-project mode (app/utils/project_tool_modes.py) is the most specific
    # layer and wins over permissions.json.  ``disabled`` refuses outright —
    # the loader should not have advertised the tool, so reaching here means
    # a stale tool list or a direct call.
    project_mode = None
    try:
        from app.utils.project_tool_modes import mode_for as _project_mode_for
        project_mode = _project_mode_for(tool_name, project_root)
    except Exception as exc:  # noqa: BLE001
        logger.debug(f"project tool mode unavailable for {tool_name}: {exc}")
    if project_mode == "disabled":
        answer = {"decision": "reject", "answer": "this tool is disabled for this project",
                  "answered_by": "project settings", "scope": SCOPE_ONCE}
        yield {"type": DECISION_EVENT, "approved": False, "answer": answer, "asked": False}
        return

    try:
        if project_mode == "autonomous":
            mode = MODE_ALWAYS
        elif project_mode == "gated":
            # Gate every op the tool considers mutating; its read-only ops
            # (declared ``off``, e.g. git ``status``) still run unprompted.
            mode = MODE_OFF if declared_mode(inst, op) == MODE_OFF else MODE_ASK
        else:
            mode = consent_mode(tool_name, op, inst)
    except Exception as exc:  # noqa: BLE001
        # Fail closed to what the tool declares: a config read error must not
        # turn an ``ask`` tool into an unattended one.
        mode = declared_mode(inst, op)
        logger.warning(f"consent mode lookup failed for {tool_name}; using declared {mode!r}: {exc}")

    human_only = op in human_only_ops(inst)
    if human_only:
        mode = MODE_ASK
    needs_ask = mode == MODE_ASK and (
        human_only or not _grants.covers(tool_name, op, conversation_id))
    preflight: Optional[Dict[str, Any]] = None
    if needs_ask or _wants_scope_preflight(inst):
        preflight = await _preflight(inst, args, project_root)

    # Part B (design/scope-consent.md): a plan that already knows the call
    # cannot succeed is returned to the model, not put in front of a human.
    if _is_refusal(preflight):
        answer = {"decision": "reject", "answered_by": ANSWERED_BY_PREFLIGHT,
                  "answer": str(preflight.get("message") or "the call cannot run"),
                  "scope": SCOPE_ONCE, "detail": preflight}
        yield {"type": DECISION_EVENT, "approved": False, "answer": answer, "asked": False}
        return

    # A call that writes outside the write policy asks unless the human
    # already approved EXACTLY these paths for this tool with a standing
    # scope (conversation / session).  No consent mode, (tool, op) grant or
    # autonomous project skips it, because approving the paths is the
    # widening.  A remembered approval still yields only a per-call grant.
    outside = _outside_policy(preflight)
    if not outside and not needs_ask:
        yield {"type": DECISION_EVENT, "approved": True, "answer": None, "asked": False}
        return
    if outside and not needs_ask:
        root = _grant_root(project_root)
        out_paths = [o["path"] for o in outside]
        if _grants.covers_paths(tool_name, conversation_id, root, out_paths):
            yield {"type": DECISION_EVENT, "approved": True, "answer": None, "asked": False,
                   "write_grant": {"root": root, "paths": out_paths},
                   "remembered_paths": True}
            return

    if not conversation_id:
        # No conversation → nowhere for an answer to arrive (no relay, no
        # panel).  Refuse rather than hang: an ``ask``-mode tool with no human
        # reachable is by definition not approved.
        if outside:
            answer = {"decision": "reject", "answered_by": ANSWERED_BY_POLICY, "scope": SCOPE_ONCE,
                      "answer": ("these paths are outside the write policy and there is no "
                                 "conversation to ask for approval: "
                                 + ", ".join(o["path"] for o in outside[:20])),
                      "detail": {"outside_policy": outside}}
        else:
            answer = {"decision": "reject", "answer": "no interactive conversation to ask",
                      "answered_by": "system", "scope": SCOPE_ONCE}
        yield {"type": DECISION_EVENT, "approved": False, "answer": answer, "asked": False}
        return

    request_id = conversation_request_id(conversation_id, tool_id)
    payload = {
        "kind": "tool_call",
        "tool": tool_name,
        "op": op,
        "args": _display_args(args),
        "preflight": preflight,
        "tool_id": tool_id,
    }
    if outside:
        payload["outside_policy"] = outside
    editable = editable_args(inst, op)
    if editable:
        payload["editable"] = editable
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
        "scopes": [SCOPE_ONCE] if human_only else list(SCOPES),
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
    if human_only:
        # Nothing standing is recorded for these ops, whatever the client
        # sent: the next call asks again.
        scope = SCOPE_ONCE
    approved = decision == "approve"
    if approved:
        # The standing scope covers the (tool, op), and for a conversation /
        # session / always scope also the exact out-of-policy paths shown.
        # The grant handed to the executor is still for THIS call only.
        _grants.remember(scope, tool_name, op, conversation_id)
    write_grant = None
    if approved and outside:
        write_grant = {"root": _grant_root(project_root), "paths": [o["path"] for o in outside]}
        _grants.remember_paths(scope, tool_name, conversation_id,
                               write_grant["root"], write_grant["paths"])
    # Edits apply to THIS call only; a standing grant never carries them.
    arg_edits = sanitize_edits(answer.get("edits"), editable) if approved else {}

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
        "write_grant_paths": list(write_grant["paths"]) if write_grant else [],
        "edited_args": sorted(arg_edits),
        "at": time.time(),
    }
    yield {"type": DECISION_EVENT, "approved": approved, "answer": answer, "asked": True,
           "write_grant": write_grant, "arg_edits": arg_edits or None}
