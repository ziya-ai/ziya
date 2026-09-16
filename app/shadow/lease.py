"""Control-lease state machine for shadow line control (design doc §6.1).

A lease is the grant that lets one chat conversation drive a session's
PTY.  This module is the pure state machine — no socket, no PTY, no
threads — so it is exhaustively unit-testable with a synthetic clock.
The socket server owns exactly one ``Lease`` slot per session and calls
into it; the frontend keystroke handler (a later phase) calls ``grant``.

Lifecycle (§6.1):

1. **acquire** — the controller requests ``mode`` (line|screen) and
   ``restriction`` (strict|gated|unrestricted), clamped to the session
   ceiling (minimum wins).  One controller per session: a live lease held
   by another conversation blocks a new acquire (``lease_held``).
2. **grant** — the human at the terminal presses a key (interactive), or
   the grant is implicit at spawn for a headless session (§6.2).  Until
   granted, the lease is *pending* and drives nothing.
3. **heartbeat** — the controller pings; the lease lives as long as its
   heartbeat is fresh (``HEARTBEAT_TIMEOUT_S``).  A missed heartbeat (the
   chat process died) expires it — the dead-man switch.
4. **release / revoke** — explicit teardown from either side.

Pause (§6.1 human precedence) is modelled as a flag here; the buffered
keystroke handoff that sets it lives in the frontend (a later phase).
"""
import secrets
import time
from dataclasses import dataclass, field
from typing import Optional, Tuple

HEARTBEAT_TIMEOUT_S = 10.0
VALID_MODES = ("line", "screen")
# supervised: every command (allowed or not) asks the human.  strict: policy-
# only, no prompts — the headless tier, not offered at a human terminal.
VALID_RESTRICTIONS = ("supervised", "strict", "gated", "unrestricted")

# Ceiling ordering for the minimum-wins clamp.  A lease restriction may
# never be looser than the session ceiling.
_RESTRICTION_RANK = {"supervised": 0, "strict": 1, "gated": 2, "unrestricted": 3}
# Session ceiling vocabulary (registry.control_ceiling) maps to the
# loosest restriction a lease on that session may hold.
_CEILING_MAX = {"none": None, "supervised": "supervised", "gated": "gated",
                "unrestricted": "unrestricted"}


def clamp_restriction(requested: str, ceiling: str) -> str:
    """Clamp a requested restriction to the session ceiling (minimum wins).

    ``ceiling`` is the registry value (none|gated|unrestricted).  Returns
    the effective restriction.  Raises ValueError for ``none`` (no lease
    is possible) or an unknown value — the caller turns that into
    ``control_disabled``.
    """
    cap = _CEILING_MAX.get(ceiling)
    if cap is None:
        raise ValueError(f"session ceiling {ceiling!r} permits no control lease")
    if requested not in _RESTRICTION_RANK:
        raise ValueError(f"invalid restriction: {requested!r}")
    return requested if _RESTRICTION_RANK[requested] <= _RESTRICTION_RANK[cap] else cap


@dataclass
class Lease:
    """One control lease.  Timestamps are monotonic seconds."""
    lease_id: str
    conversation_id: str
    mode: str
    restriction: str
    policy_set: str
    created_at: float
    last_heartbeat: float
    granted: bool = False
    paused: bool = False
    # Why the lease ended, once it has: release|revoke|heartbeat_timeout|
    # superseded.  None while live or pending.
    ended: Optional[str] = None

    def is_expired(self, now: float) -> bool:
        return (now - self.last_heartbeat) > HEARTBEAT_TIMEOUT_S

    def is_live(self, now: float) -> bool:
        """Pending or active, but not ended and not heartbeat-expired."""
        return self.ended is None and not self.is_expired(now)

    def is_active(self, now: float) -> bool:
        """Granted and live — the only state in which input is accepted."""
        return self.granted and self.is_live(now)

    def touch(self, now: float) -> None:
        self.last_heartbeat = now


def new_lease(conversation_id: str, mode: str, restriction: str,
              policy_set: str, *, granted: bool = False,
              now: Optional[float] = None) -> Lease:
    if mode not in VALID_MODES:
        raise ValueError(f"invalid mode: {mode!r}")
    if restriction not in VALID_RESTRICTIONS:
        raise ValueError(f"invalid restriction: {restriction!r}")
    t = time.monotonic() if now is None else now
    return Lease(
        lease_id=secrets.token_hex(4),
        conversation_id=conversation_id,
        mode=mode,
        restriction=restriction,
        policy_set=policy_set,
        created_at=t,
        last_heartbeat=t,
        granted=granted,
    )


class LeaseSlot:
    """The single per-session lease holder (§6.1: one controller).

    Thread-safety is the caller's job (the socket server already holds a
    lock around entry mutation); this class is pure logic over ``now`` so
    tests drive it with a synthetic clock.
    """

    def __init__(self):
        self.lease: Optional[Lease] = None

    def current(self, now: float) -> Optional[Lease]:
        """The live lease, or None (reaping an expired one as a side effect)."""
        ls = self.lease
        if ls is None:
            return None
        if ls.ended is not None:
            return None
        if ls.is_expired(now):
            ls.ended = "heartbeat_timeout"
            return None
        return ls

    def acquire(self, conversation_id: str, mode: str, restriction: str,
                policy_set: str, ceiling: str, *, implicit_grant: bool = False,
                now: Optional[float] = None) -> Tuple[Optional[Lease], Optional[str]]:
        """Request a lease.  Returns (lease, error_code).

        - ``control_disabled`` if the ceiling permits no lease.
        - ``lease_held`` if a live lease is held by another conversation.
        - Re-acquire by the same conversation supersedes its own lease
          (e.g. to change restriction) and requires a fresh grant unless
          ``implicit_grant``.
        """
        t = time.monotonic() if now is None else now
        try:
            effective = clamp_restriction(restriction, ceiling)
        except ValueError:
            return None, "control_disabled"

        held = self.current(t)
        if held is not None and held.conversation_id != conversation_id:
            return None, "lease_held"
        if held is not None and held.conversation_id == conversation_id:
            held.ended = "superseded"

        lease = new_lease(conversation_id, mode, effective, policy_set,
                          granted=implicit_grant, now=t)
        self.lease = lease
        return lease, None

    def grant(self, lease_id: str, now: Optional[float] = None) -> bool:
        """Human grant at the terminal: flip pending → active.  True on success."""
        t = time.monotonic() if now is None else now
        ls = self.current(t)
        if ls is None or ls.lease_id != lease_id:
            return False
        ls.granted = True
        ls.touch(t)
        return True

    def heartbeat(self, lease_id: str, conversation_id: str,
                  now: Optional[float] = None) -> bool:
        t = time.monotonic() if now is None else now
        ls = self.current(t)
        if ls is None or ls.lease_id != lease_id or ls.conversation_id != conversation_id:
            return False
        ls.touch(t)
        return True

    def release(self, lease_id: str, reason: str = "release",
                now: Optional[float] = None) -> bool:
        t = time.monotonic() if now is None else now
        ls = self.current(t)
        if ls is None or ls.lease_id != lease_id:
            return False
        ls.ended = reason
        return True

    def revoke(self, now: Optional[float] = None) -> bool:
        """Shadow-side teardown (menu key), regardless of lease_id."""
        t = time.monotonic() if now is None else now
        ls = self.current(t)
        if ls is None:
            return False
        ls.ended = "revoke"
        return True
