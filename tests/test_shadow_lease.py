"""Lease state machine (§6.1) — pure logic with a synthetic clock."""
import pytest

from app.shadow.lease import (
    Lease, LeaseSlot, clamp_restriction, new_lease, HEARTBEAT_TIMEOUT_S,
)


# -- clamp (minimum wins) ------------------------------------------------------

@pytest.mark.parametrize("requested,ceiling,expected", [
    ("unrestricted", "gated", "gated"),        # clamped down to ceiling
    ("gated", "unrestricted", "gated"),        # below ceiling, unchanged
    ("strict", "unrestricted", "strict"),
    ("unrestricted", "unrestricted", "unrestricted"),
    ("gated", "gated", "gated"),
])
def test_clamp_minimum_wins(requested, ceiling, expected):
    assert clamp_restriction(requested, ceiling) == expected


def test_clamp_none_ceiling_refuses():
    with pytest.raises(ValueError):
        clamp_restriction("gated", "none")


# -- slot: acquire / exclusivity ----------------------------------------------

def test_acquire_grants_headless_immediately():
    slot = LeaseSlot()
    lease, err = slot.acquire("convA", "line", "gated", "none", "gated",
                              implicit_grant=True, now=100.0)
    assert err is None and lease.granted is True
    assert lease.is_active(now=100.0)


def test_acquire_interactive_is_pending_until_grant():
    slot = LeaseSlot()
    lease, err = slot.acquire("convA", "line", "gated", "none", "gated", now=100.0)
    assert err is None and lease.granted is False
    assert not lease.is_active(now=100.0)      # pending: no input yet
    assert lease.is_live(now=100.0)
    assert slot.grant(lease.lease_id, now=101.0) is True
    assert slot.current(now=101.0).is_active(now=101.0)


def test_second_conversation_blocked_while_live():
    slot = LeaseSlot()
    slot.acquire("convA", "line", "gated", "none", "gated", implicit_grant=True, now=100.0)
    lease, err = slot.acquire("convB", "line", "gated", "none", "gated", now=101.0)
    assert lease is None and err == "lease_held"


def test_reacquire_by_same_conversation_supersedes():
    slot = LeaseSlot()
    a, _ = slot.acquire("convA", "line", "gated", "none", "gated", implicit_grant=True, now=100.0)
    b, err = slot.acquire("convA", "line", "strict", "none", "gated", implicit_grant=True, now=101.0)
    assert err is None and b.lease_id != a.lease_id
    assert a.ended == "superseded"
    assert slot.current(now=101.0).lease_id == b.lease_id


def test_acquire_clamps_restriction_to_ceiling():
    slot = LeaseSlot()
    lease, err = slot.acquire("convA", "line", "unrestricted", "none", "gated",
                              implicit_grant=True, now=100.0)
    assert err is None and lease.restriction == "gated"


def test_acquire_none_ceiling_disabled():
    slot = LeaseSlot()
    lease, err = slot.acquire("convA", "line", "gated", "none", "none", now=100.0)
    assert lease is None and err == "control_disabled"


# -- heartbeat / expiry (dead-man switch) -------------------------------------

def test_heartbeat_keeps_lease_alive():
    slot = LeaseSlot()
    lease, _ = slot.acquire("convA", "line", "gated", "none", "gated", implicit_grant=True, now=100.0)
    # Just before timeout, a heartbeat refreshes it.
    t = 100.0 + HEARTBEAT_TIMEOUT_S - 0.5
    assert slot.heartbeat(lease.lease_id, "convA", now=t) is True
    assert slot.current(now=t + HEARTBEAT_TIMEOUT_S - 0.5) is not None


def test_missed_heartbeat_expires_lease():
    slot = LeaseSlot()
    lease, _ = slot.acquire("convA", "line", "gated", "none", "gated", implicit_grant=True, now=100.0)
    dead = 100.0 + HEARTBEAT_TIMEOUT_S + 0.1
    assert slot.current(now=dead) is None            # reaped
    assert lease.ended == "heartbeat_timeout"
    # A new conversation can now take the session.
    lease2, err = slot.acquire("convB", "line", "gated", "none", "gated", now=dead + 1)
    assert err is None and lease2.conversation_id == "convB"


def test_heartbeat_wrong_conversation_rejected():
    slot = LeaseSlot()
    lease, _ = slot.acquire("convA", "line", "gated", "none", "gated", implicit_grant=True, now=100.0)
    assert slot.heartbeat(lease.lease_id, "convB", now=101.0) is False


# -- release / revoke ----------------------------------------------------------

def test_release_by_lease_id():
    slot = LeaseSlot()
    lease, _ = slot.acquire("convA", "line", "gated", "none", "gated", implicit_grant=True, now=100.0)
    assert slot.release(lease.lease_id, now=101.0) is True
    assert slot.current(now=101.0) is None
    assert lease.ended == "release"


def test_revoke_regardless_of_id():
    slot = LeaseSlot()
    slot.acquire("convA", "line", "gated", "none", "gated", implicit_grant=True, now=100.0)
    assert slot.revoke(now=101.0) is True
    assert slot.current(now=101.0) is None


def test_grant_only_matches_current_lease():
    slot = LeaseSlot()
    lease, _ = slot.acquire("convA", "line", "gated", "none", "gated", now=100.0)
    assert slot.grant("deadbeef", now=101.0) is False   # wrong id
    assert slot.grant(lease.lease_id, now=101.0) is True


def test_supervised_is_the_narrowest_rung_and_clamps():
    from app.shadow.lease import clamp_restriction
    # supervised < strict < gated < unrestricted; ceiling caps every request.
    assert clamp_restriction("strict", "supervised") == "supervised"
    assert clamp_restriction("unrestricted", "supervised") == "supervised"
    assert clamp_restriction("supervised", "unrestricted") == "supervised"
    assert clamp_restriction("gated", "gated") == "gated"


def test_supervised_policy_confirms_even_allowed_commands():
    from app.shadow.policy import resolve_policy, CONFIRM, RUN
    sup = resolve_policy("builtin", "supervised")
    assert sup.decide("ls -la")[0] == CONFIRM
    assert sup.decide("rm -rf x")[0] == CONFIRM
    assert resolve_policy("builtin", "gated").decide("ls -la") == (RUN, "")
