"""
Per-request, per-iteration context snapshots for debugging.

Background
----------
A single conversational turn can involve many agentic tool-calling
iterations before the model produces its final answer.  Only the final
text is ever persisted to the chat record — every intermediate
``conversation`` growth (tool_use blocks, tool_result blocks, file reads,
search results, etc.) is transient and, without this module, invisible to
any UI or debug surface once the turn ends.

This is what causes the "badge says <100k tokens, provider says >1M"
symptom: the token-count badge and the backend's own estimation-accuracy
check (``_track_estimation_accuracy``) both only ever look at iteration
0 of a turn — the persisted chat history plus checked files — because
that is the only state that exists before any tool call has run.  By
iteration 20, the ``conversation`` list handed to the provider may be
many times larger.

Request identity
----------------
Snapshots are grouped by **request**, not just by conversation.  A
conversation routinely has several *concurrent* provider streams against
the same ``conversation_id`` — the primary stream plus delegates and
background tasks — and each one restarts its agentic ``iteration``
counter at 0.  Keying only by conversation interleaved all of them into
one flat list whose ``iteration`` column read ``2,3,4,3,3,3,2,4,5,...``:
unattributable, and self-cannibalising, because a single per-conversation
ring buffer was consumed several times faster than one turn's worth.

Each stream invocation therefore mints a ``request_id``
(:func:`new_request_id`) and every entry carries it plus a monotonic
per-conversation ``seq``, so the true ordering survives interleaving and
each request gets its own capped buffer.  When a buffer does overflow, an
explicit ``evicted`` count is recorded rather than silently losing the
front of the turn.

Three capture tiers
-------------------
* **Always-on (in-memory)**: the token numbers are already in hand from
  the provider's usage event, so appending them to a ring buffer costs
  microseconds.  This tier can never meaningfully slow a turn and is
  therefore unconditional.
* **Detailed capture (opt-in)**: per-iteration disk persistence (so the
  history survives a crash or restart — exactly the failure you most
  want to inspect) and, at the call site, the per-iteration char counts
  of the submitted payload.  Enabled via :func:`set_enabled`.
* **Payload capture (opt-in, heaviest)**: the *actual submitted payload*
  — the pre-serialisation ``system_content`` and ``conversation``
  structures handed to the provider — written verbatim to disk so a
  request can be inspected in full rather than summarised.  Enabled
  separately via :func:`set_payload_enabled` because each frame is
  megabytes.

Payload retention
-----------------
Payload frames are deliberately short-lived: only the most recent
:data:`PAYLOAD_REQUESTS_RETAINED` requests per conversation are kept
(default 3), with a global byte ceiling as a backstop, and a failed
request is preserved even when it falls outside the window.  This is a
debugging window on "what did I just send", not an archive.

Security: payload frames contain the full submitted context — source
code, documents, tool output, and anything else in scope.  Files are
written 0600 inside a 0700 directory under the Ziya home, and payload
capture is off by default.

Failure isolation
-----------------
Every public function in this module is a no-op on failure: a
debug-and-diagnostics module must never be capable of taking down (or
even measurably slowing) an actual model turn.
"""

import json
import os
import shutil
import threading
import time
import uuid
from collections import OrderedDict, deque
from typing import Any, Dict, List, Optional, Tuple

from app.utils.logging_utils import logger

# Per-request entry cap.  Now that buffers are per REQUEST rather than
# per conversation, a long agentic turn (120+ iterations observed) fits
# without concurrent streams eating each other's history.
_MAX_ITERATIONS_PER_REQUEST = 250
# How many request groups to retain per conversation for the cheap token
# tier.  Cheap enough to keep some history for context.
_MAX_REQUESTS_PER_CONVERSATION = 12
_MAX_TRACKED_CONVERSATIONS = 200

#: Requests whose full payload frames are retained, per conversation.
#: "Only retain frames for 2-3 rounds" — this is the rolling window.
PAYLOAD_REQUESTS_RETAINED = 3
#: Conversations for which payload frames are retained at all (LRU).
PAYLOAD_CONVERSATIONS_RETAINED = 4
#: Backstop ceiling on total payload bytes on disk.
PAYLOAD_TOTAL_BYTES_CEILING = 512 * 1024 * 1024

_lock = threading.Lock()
# conversation_id -> OrderedDict[request_id -> request group]
_history: "OrderedDict[str, OrderedDict[str, Dict[str, Any]]]" = OrderedDict()
# conversation_id -> monotonic sequence counter
_seq_counters: Dict[str, int] = {}

# Cached opt-in flags.  None = not yet loaded from disk.  The env vars are
# re-read on every check so an operator can force capture on a process
# they can restart; the config file is read once and cached, with the
# setters keeping cache and disk in sync.
_enabled_cache: Optional[bool] = None
_payload_enabled_cache: Optional[bool] = None

#: Substrings that identify a provider's "input too large" rejection.
#: Matched case-insensitively.  Deliberately broad: this list existing in
#: three different narrow copies is why no overflow was ever recorded.
_OVERFLOW_MARKERS: Tuple[str, ...] = (
    "prompt is too long",
    "input is too long",
    "exceed context limit",                 # bedrock: "input length and `max_tokens` exceed context limit"
    "exceeds the maximum number of tokens",
    "maximum context length",               # openai
    "context_length_exceeded",              # openai error code
    "too many total text bytes",             # gemini
    "request payload size exceeds",          # vertex
    "reduce the length of the messages",
    "input token count exceeds",
    "context window",
)


def is_context_overflow_error(error: Any) -> bool:
    """Whether *error* is a provider rejection for an over-large input.

    Single source of truth, so the streaming executor, the Bedrock client
    wrapper, and the legacy agent path all agree on what counts as an
    overflow.  Accepts an exception or a string.  Never raises.
    """
    try:
        text = str(error).lower()
    except Exception:  # noqa: BLE001
        return False
    return any(marker in text for marker in _OVERFLOW_MARKERS)


def new_request_id() -> str:
    """Mint an id for one provider stream invocation (one "round")."""
    return uuid.uuid4().hex[:12]


def _debug_dir():
    from app.utils.paths import get_ziya_home
    d = get_ziya_home() / "debug" / "context_snapshots"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _payload_root():
    from app.utils.paths import get_ziya_home
    d = get_ziya_home() / "debug" / "context_payloads"
    d.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(d, 0o700)
    except OSError:
        pass
    return d


def _config_path():
    from app.utils.paths import get_ziya_home
    return get_ziya_home() / "debug" / "context_debug_config.json"


def _read_config() -> Dict[str, Any]:
    try:
        p = _config_path()
        if p.exists():
            data = json.loads(p.read_text())
            return data if isinstance(data, dict) else {}
    except Exception as e:  # noqa: BLE001
        logger.debug(f"context_debug config read failed: {e}")
    return {}


def _write_config(**updates: Any) -> None:
    try:
        p = _config_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        data = _read_config()
        data.update(updates)
        p.write_text(json.dumps(data))
    except Exception as e:  # noqa: BLE001
        logger.debug(f"context_debug config persist failed (non-fatal): {e}")


def _env_flag(name: str) -> Optional[bool]:
    raw = os.environ.get(name)
    if raw is None:
        return None
    return raw.strip().lower() not in ("", "0", "false", "no", "off")


def is_enabled() -> bool:
    """Whether detailed capture (disk persistence + char counts) is on.

    Resolution order: ``ZIYA_CONTEXT_DEBUG`` env var, then the persisted
    config flag, then off.  Never raises; any failure reads as "off".
    """
    env = _env_flag("ZIYA_CONTEXT_DEBUG")
    if env is not None:
        return env
    global _enabled_cache
    if _enabled_cache is None:
        _enabled_cache = bool(_read_config().get("enabled", False))
    return _enabled_cache


def set_enabled(enabled: bool) -> bool:
    """Toggle detailed capture; persists across restarts. Returns new state."""
    global _enabled_cache
    _enabled_cache = bool(enabled)
    _write_config(enabled=_enabled_cache)
    return _enabled_cache


def is_payload_enabled() -> bool:
    """Whether full submitted-payload capture is on.

    Independent of :func:`is_enabled` because payload frames are orders of
    magnitude more expensive than token counts.  Resolution order:
    ``ZIYA_CONTEXT_PAYLOAD_DEBUG`` env var, then the persisted flag, then
    off.  Never raises.
    """
    env = _env_flag("ZIYA_CONTEXT_PAYLOAD_DEBUG")
    if env is not None:
        return env
    global _payload_enabled_cache
    if _payload_enabled_cache is None:
        _payload_enabled_cache = bool(_read_config().get("payload_enabled", False))
    return _payload_enabled_cache


def set_payload_enabled(enabled: bool) -> bool:
    """Toggle full payload capture; persists across restarts."""
    global _payload_enabled_cache
    _payload_enabled_cache = bool(enabled)
    _write_config(payload_enabled=_payload_enabled_cache)
    return _payload_enabled_cache


# ── internal store ──────────────────────────────────────────────────────

def _next_seq(conversation_id: str) -> int:
    """Monotonic per-conversation sequence.  Caller must hold ``_lock``."""
    n = _seq_counters.get(conversation_id, 0)
    _seq_counters[conversation_id] = n + 1
    return n


def _group_for(conversation_id: str, request_id: str) -> Dict[str, Any]:
    """Fetch-or-create the request group.  Caller must hold ``_lock``."""
    conv = _history.get(conversation_id)
    if conv is None:
        conv = OrderedDict()
        _history[conversation_id] = conv
        while len(_history) > _MAX_TRACKED_CONVERSATIONS:
            evicted_conv, _ = _history.popitem(last=False)
            _seq_counters.pop(evicted_conv, None)
    else:
        _history.move_to_end(conversation_id)

    group = conv.get(request_id)
    if group is None:
        group = {
            "request_id": request_id,
            "started_ts": time.time(),
            "entries": deque(maxlen=_MAX_ITERATIONS_PER_REQUEST),
            "evicted": 0,
            "has_failure": False,
        }
        conv[request_id] = group
        while len(conv) > _MAX_REQUESTS_PER_CONVERSATION:
            conv.popitem(last=False)
    else:
        conv.move_to_end(request_id)
    return group


def _append_entry(
    conversation_id: str,
    request_id: str,
    entry: Dict[str, Any],
    *,
    force_persist: bool,
) -> None:
    """Append to the request's ring buffer and maybe persist.

    ``force_persist`` bypasses the detailed-capture toggle for the disk
    write.  Used by :func:`record_failure`, which fires once per failed
    call (not on the hot per-iteration path the toggle exists to protect)
    and is exactly the event most worth surviving a restart.
    """
    with _lock:
        group = _group_for(conversation_id, request_id)
        entries = group["entries"]
        was_full = len(entries) == entries.maxlen
        entry["seq"] = _next_seq(conversation_id)
        entry["request_id"] = request_id
        entries.append(entry)
        if was_full:
            # A silent drop is what made a 120-iteration turn appear to
            # start at iteration 59.  Count it so loss is visible.
            group["evicted"] += 1
        if entry.get("is_failure"):
            group["has_failure"] = True
        snapshot = _snapshot_locked(conversation_id) if (force_persist or is_enabled()) else None
    if snapshot is not None:
        _persist(conversation_id, snapshot)


def _snapshot_locked(conversation_id: str) -> Dict[str, Any]:
    """Build the serialisable view of a conversation.  Holds ``_lock``."""
    conv = _history.get(conversation_id) or OrderedDict()
    requests: List[Dict[str, Any]] = []
    flat: List[Dict[str, Any]] = []
    for group in conv.values():
        entries = list(group["entries"])
        flat.extend(entries)
        requests.append({
            "request_id": group["request_id"],
            "started_ts": group["started_ts"],
            "evicted": group["evicted"],
            "has_failure": group["has_failure"],
            "iterations": entries,
        })
    flat.sort(key=lambda e: e.get("seq", 0))
    return {"requests": requests, "iterations": flat}


def record_iteration(
    conversation_id: Optional[str],
    iteration: int,
    *,
    request_id: Optional[str] = None,
    fresh_tokens: int = 0,
    cache_read_tokens: int = 0,
    cache_write_tokens: int = 0,
    total_input_tokens: int = 0,
    effective_limit: Optional[int] = None,
    estimated_tokens: Optional[int] = None,
    system_chars: Optional[int] = None,
    conversation_chars: Optional[int] = None,
    message_count: Optional[int] = None,
    note: Optional[str] = None,
) -> None:
    """Record one iteration's actual (provider-reported) token usage.

    ``request_id`` attributes the entry to one stream invocation; callers
    that predate request identity fall back to a synthetic id so their
    entries are still grouped rather than interleaved with live streams.

    Never raises.  Called from the hot streaming path: the in-memory
    append is unconditional (microseconds); the best-effort disk write
    happens only when detailed capture is enabled.
    """
    if not conversation_id:
        return
    try:
        entry = {
            "iteration": iteration,
            "timestamp": time.time(),
            "fresh_tokens": fresh_tokens,
            "cache_read_tokens": cache_read_tokens,
            "cache_write_tokens": cache_write_tokens,
            "total_input_tokens": total_input_tokens,
            "effective_limit": effective_limit,
            "pct_of_limit": (
                round(100 * total_input_tokens / effective_limit, 1)
                if effective_limit else None
            ),
            "estimated_tokens": estimated_tokens,
            "system_chars": system_chars,
            "conversation_chars": conversation_chars,
            "message_count": message_count,
            "note": note,
            "is_estimated": False,
            "is_failure": False,
        }
        _append_entry(conversation_id, request_id or "legacy", entry, force_persist=False)
    except Exception as e:  # noqa: BLE001 - diagnostics must never break a turn
        logger.debug(f"context_debug.record_iteration failed (non-fatal): {e}")


def record_failure(
    conversation_id: Optional[str],
    iteration: int,
    *,
    error_message: str,
    request_id: Optional[str] = None,
    system_content: Any = None,
    conversation: Optional[List[Any]] = None,
    effective_limit: Optional[int] = None,
    origin: str = "executor",
) -> None:
    """Record a snapshot for a call that failed before any usage event.

    Background
    ----------
    A provider's "prompt is too long" rejection is raised synchronously
    by the HTTP client *before* any streaming response begins — no
    UsageEvent is ever produced, so :func:`record_iteration` (which only
    runs from inside ``_handle_usage_event``) never fires for that
    attempt.  Worse, the Bedrock client wrapper catches context-limit
    errors itself and retries with extended-context headers, so a
    successful retry meant the overflow that actually happened left no
    trace anywhere.  ``origin`` distinguishes those call sites so a
    retry chain is legible.

    Since no provider-reported number exists for a failed call, this
    computes a rough char/4 estimate from whatever payload was being
    submitted at the moment of failure and marks the entry
    ``is_estimated`` so it is never confused with a real provider figure.
    Always persists to disk regardless of the detailed-capture toggle.

    Never raises.
    """
    if not conversation_id:
        return
    try:
        system_chars = _measure_system_chars(system_content)
        conversation_chars = _measure_conversation_chars(conversation)
        total_chars = system_chars + conversation_chars
        # Rough, clearly-labelled estimate only.
        estimated_tokens = int(total_chars / 4) if total_chars else 0
        entry = {
            "iteration": iteration,
            "timestamp": time.time(),
            "fresh_tokens": 0,
            "cache_read_tokens": 0,
            "cache_write_tokens": 0,
            "total_input_tokens": estimated_tokens,
            "effective_limit": effective_limit,
            "pct_of_limit": (
                round(100 * estimated_tokens / effective_limit, 1)
                if effective_limit else None
            ),
            "estimated_tokens": estimated_tokens,
            "system_chars": system_chars,
            "conversation_chars": conversation_chars,
            "message_count": len(conversation) if conversation else None,
            "note": f"FAILED before any usage event [{origin}]: {error_message[:300]}",
            "is_estimated": True,
            "is_failure": True,
            "origin": origin,
        }
        rid = request_id or "legacy"
        _append_entry(conversation_id, rid, entry, force_persist=True)
        # A failed request is the one you most want the bytes for, so
        # capture it even when routine payload capture is off.
        capture_payload(
            conversation_id, iteration,
            request_id=rid,
            system_content=system_content,
            conversation=conversation,
            force=True,
            note=f"failure [{origin}]: {error_message[:300]}",
        )
    except Exception as e:  # noqa: BLE001 - diagnostics must never break a turn
        logger.debug(f"context_debug.record_failure failed (non-fatal): {e}")


def _measure_system_chars(system_content: Any) -> int:
    if isinstance(system_content, str):
        return len(system_content)
    if isinstance(system_content, list):
        return sum(len(b.get('text', '')) for b in system_content if isinstance(b, dict))
    return 0


def _measure_conversation_chars(conversation: Optional[List[Any]]) -> int:
    if not conversation:
        return 0
    total = 0
    for m in conversation:
        try:
            total += len(str(m.get('content', '') if isinstance(m, dict) else m))
        except Exception:  # noqa: BLE001
            pass
    return total


# ── payload capture ─────────────────────────────────────────────────────

def capture_payload(
    conversation_id: Optional[str],
    iteration: int,
    *,
    request_id: str,
    system_content: Any = None,
    conversation: Optional[List[Any]] = None,
    tools: Any = None,
    model_id: Optional[str] = None,
    force: bool = False,
    note: Optional[str] = None,
) -> None:
    """Write the full submitted payload for one iteration to disk.

    Captures the **pre-serialisation** structures — the ``system_content``
    and ``conversation`` objects as handed to the provider adapter — which
    is one hop from the wire and requires no per-provider tap.  Values
    that are not JSON-serialisable are coerced with ``repr`` rather than
    dropped, so a frame is always complete enough to read.

    Skipped unless payload capture is enabled, or ``force`` is set (used
    for failures, which are always worth the bytes).  Never raises.
    """
    if not conversation_id:
        return
    if not force and not is_payload_enabled():
        return
    try:
        req_dir = _payload_root() / _safe_name(conversation_id) / _safe_name(request_id)
        req_dir.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(req_dir.parent, 0o700)
            os.chmod(req_dir, 0o700)
        except OSError:
            pass

        frame = {
            "conversation_id": conversation_id,
            "request_id": request_id,
            "iteration": iteration,
            "timestamp": time.time(),
            "model_id": model_id,
            "note": note,
            "system_content": system_content,
            "conversation": conversation,
            "tools": _tool_names(tools),
        }
        path = req_dir / f"iter_{iteration:04d}.json"
        tmp = path.with_suffix(".tmp")
        # default=repr: never lose a frame to an exotic object.
        tmp.write_text(json.dumps(frame, indent=2, default=repr))
        try:
            os.chmod(tmp, 0o600)
        except OSError:
            pass
        os.replace(tmp, path)
        _prune_payloads(conversation_id, keep_request=request_id)
    except Exception as e:  # noqa: BLE001
        logger.debug(f"context_debug.capture_payload failed (non-fatal): {e}")


def _tool_names(tools: Any) -> Optional[List[str]]:
    """Record which tools were offered, not their full schemas."""
    if not tools:
        return None
    names: List[str] = []
    for t in tools:
        try:
            if isinstance(t, dict):
                names.append(str(t.get("name") or t.get("toolSpec", {}).get("name") or "?"))
            else:
                names.append(str(getattr(t, "name", t)))
        except Exception:  # noqa: BLE001
            names.append("?")
    return names


def _safe_name(value: str) -> str:
    """Filesystem-safe single path segment."""
    return "".join(c if (c.isalnum() or c in "-_") else "_" for c in str(value))[:64] or "unknown"


def _dir_bytes(path) -> int:
    total = 0
    try:
        for root, _dirs, files in os.walk(path):
            for f in files:
                try:
                    total += os.path.getsize(os.path.join(root, f))
                except OSError:
                    pass
    except OSError:
        pass
    return total


def _prune_payloads(conversation_id: str, *, keep_request: Optional[str] = None) -> None:
    """Enforce the rolling retention window.

    Three bounds, cheapest first: newest N requests per conversation,
    newest M conversations, then a total-bytes backstop.  A request dir
    containing a recorded failure is exempt from the per-conversation
    window — a failed round is the whole point of keeping frames.
    """
    root = _payload_root()
    conv_dir = root / _safe_name(conversation_id)

    failed_requests = set()
    with _lock:
        for group in (_history.get(conversation_id) or {}).values():
            if group.get("has_failure"):
                failed_requests.add(_safe_name(group["request_id"]))

    try:
        req_dirs = sorted(
            (d for d in conv_dir.iterdir() if d.is_dir()),
            key=lambda d: d.stat().st_mtime,
            reverse=True,
        )
        keep = {_safe_name(keep_request)} if keep_request else set()
        kept = 0
        for d in req_dirs:
            if d.name in keep or d.name in failed_requests:
                continue
            kept += 1
            if kept >= PAYLOAD_REQUESTS_RETAINED:
                shutil.rmtree(d, ignore_errors=True)
    except OSError:
        pass

    try:
        conv_dirs = sorted(
            (d for d in root.iterdir() if d.is_dir()),
            key=lambda d: d.stat().st_mtime,
            reverse=True,
        )
        for d in conv_dirs[PAYLOAD_CONVERSATIONS_RETAINED:]:
            if d != conv_dir:
                shutil.rmtree(d, ignore_errors=True)
        # Byte backstop: drop oldest conversations until under ceiling.
        while conv_dirs and _dir_bytes(root) > PAYLOAD_TOTAL_BYTES_CEILING:
            oldest = conv_dirs.pop()
            if oldest == conv_dir:
                break
            shutil.rmtree(oldest, ignore_errors=True)
    except OSError:
        pass


def list_payload_frames(conversation_id: str) -> List[Dict[str, Any]]:
    """Index the retained payload frames for a conversation (no contents).

    Returns newest-request-first, with per-frame byte sizes so the UI can
    show what is available to download without loading megabytes.
    """
    out: List[Dict[str, Any]] = []
    if not conversation_id:
        return out
    try:
        conv_dir = _payload_root() / _safe_name(conversation_id)
        if not conv_dir.is_dir():
            return out
        for req_dir in sorted(
            (d for d in conv_dir.iterdir() if d.is_dir()),
            key=lambda d: d.stat().st_mtime, reverse=True,
        ):
            frames = []
            for f in sorted(req_dir.glob("iter_*.json")):
                try:
                    frames.append({
                        "iteration": int(f.stem.split("_")[1]),
                        "bytes": f.stat().st_size,
                    })
                except (ValueError, IndexError, OSError):
                    continue
            out.append({
                "request_id": req_dir.name,
                "modified": req_dir.stat().st_mtime,
                "frames": frames,
                "total_bytes": sum(fr["bytes"] for fr in frames),
            })
    except Exception as e:  # noqa: BLE001
        logger.debug(f"context_debug.list_payload_frames failed (non-fatal): {e}")
    return out


def get_payload_frame(conversation_id: str, request_id: str, iteration: int) -> Optional[Dict[str, Any]]:
    """Load one payload frame verbatim.  Returns None when not retained."""
    try:
        path = (_payload_root() / _safe_name(conversation_id)
                / _safe_name(request_id) / f"iter_{int(iteration):04d}.json")
        if not path.is_file():
            return None
        return json.loads(path.read_text())
    except Exception as e:  # noqa: BLE001
        logger.debug(f"context_debug.get_payload_frame failed (non-fatal): {e}")
    return None


# ── reads ───────────────────────────────────────────────────────────────

def _persist(conversation_id: str, snapshot: Dict[str, Any]) -> None:
    try:
        path = _debug_dir() / f"{_safe_name(conversation_id)}.json"
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(
            {"conversation_id": conversation_id, **snapshot}, indent=2))
        os.replace(tmp, path)
    except Exception as e:  # noqa: BLE001
        logger.debug(f"context_debug._persist failed (non-fatal): {e}")


def get_history(conversation_id: str) -> List[Dict[str, Any]]:
    """Flat, seq-ordered iteration snapshots for a conversation.

    Retained for the existing panel: each entry now carries ``request_id``
    and ``seq`` so a consumer can group without a shape change.  Prefers
    the in-memory copy; falls back to the on-disk snapshot.  Returns an
    empty list — never raises — on any failure.
    """
    if not conversation_id:
        return []
    try:
        with _lock:
            if conversation_id in _history:
                return _snapshot_locked(conversation_id)["iterations"]
        path = _debug_dir() / f"{_safe_name(conversation_id)}.json"
        if path.exists():
            return json.loads(path.read_text()).get("iterations", [])
    except Exception as e:  # noqa: BLE001
        logger.debug(f"context_debug.get_history failed (non-fatal): {e}")
    return []


def get_requests(conversation_id: str) -> List[Dict[str, Any]]:
    """Iteration snapshots grouped by request ("round").

    Each group carries ``request_id``, ``started_ts``, ``evicted`` (how
    many entries the ring buffer dropped) and its ``iterations``.  This
    is the shape that makes a conversation with concurrent streams
    readable.
    """
    if not conversation_id:
        return []
    try:
        with _lock:
            if conversation_id in _history:
                return _snapshot_locked(conversation_id)["requests"]
        path = _debug_dir() / f"{_safe_name(conversation_id)}.json"
        if path.exists():
            data = json.loads(path.read_text())
            if data.get("requests"):
                return data["requests"]
            # Pre-request-identity snapshot on disk: present it as one
            # synthetic group rather than claiming no history exists.
            legacy = data.get("iterations", [])
            if legacy:
                return [{
                    "request_id": "legacy",
                    "started_ts": legacy[0].get("timestamp"),
                    "evicted": 0,
                    "has_failure": any(e.get("is_failure") for e in legacy),
                    "iterations": legacy,
                }]
    except Exception as e:  # noqa: BLE001
        logger.debug(f"context_debug.get_requests failed (non-fatal): {e}")
    return []


def clear_history(conversation_id: str) -> None:
    """Drop snapshots and payload frames for a conversation."""
    if not conversation_id:
        return
    try:
        with _lock:
            _history.pop(conversation_id, None)
            _seq_counters.pop(conversation_id, None)
        path = _debug_dir() / f"{_safe_name(conversation_id)}.json"
        if path.exists():
            path.unlink()
        conv_dir = _payload_root() / _safe_name(conversation_id)
        if conv_dir.is_dir():
            shutil.rmtree(conv_dir, ignore_errors=True)
    except Exception as e:  # noqa: BLE001
        logger.debug(f"context_debug.clear_history failed (non-fatal): {e}")


def reset_for_tests() -> None:
    """Drop all in-memory state and cached flags."""
    global _enabled_cache, _payload_enabled_cache
    with _lock:
        _history.clear()
        _seq_counters.clear()
    _enabled_cache = None
    _payload_enabled_cache = None
