"""
Per-window candidate cap scales with window size.

PER_WINDOW_CANDIDATE_CAP was a flat 3 regardless of how much conversation
a window covered.  Measured against real transcripts, 13 of 23 windows hit
that ceiling — so the cap was truncating the majority of windows, and it
truncates by array order rather than by quality, discarding whatever the
extractor happened to list last.

window_candidate_cap() replaces the flat value with a budget proportional
to the window's human-turn count (CANDIDATES_PER_EXCHANGE_BLOCK per
EXCHANGE_BLOCK_TURNS turns), floored at the original PER_WINDOW_CANDIDATE_CAP
so a short window is never squeezed below the old behaviour.
"""
import math

import pytest

from app.memory.extractor import (
    CANDIDATES_PER_EXCHANGE_BLOCK,
    EXCHANGE_BLOCK_TURNS,
    PER_WINDOW_CANDIDATE_CAP,
    WINDOW_TURN_COUNT,
    window_candidate_cap,
)


# (human turns in window, expected cap)
#
# Expected values are the explicit arithmetic, not a re-derivation of the
# implementation: ceil(turns * 10 / 6), floored at 3.
SCALING_CASES = [
    (2, 4),    # ceil(20/6) = 4
    (3, 5),    # ceil(30/6) = 5
    (6, 10),   # one full exchange block
    (8, 14),   # ceil(80/6) = 14 — a full WINDOW_TURN_COUNT window
    (12, 20),  # two full exchange blocks
]


@pytest.mark.parametrize("turns,expected", SCALING_CASES)
def test_scales_with_turn_count(turns, expected):
    assert window_candidate_cap(turns) == expected


@pytest.mark.parametrize("turns", [0, 1])
def test_floor_protects_short_windows(turns):
    # ceil(1*10/6) = 2, below the floor of 3.  A 1-turn window can still
    # carry a genuine fact — the salience gate already proved it has signal —
    # so the floor keeps it from being squeezed under the useful minimum.
    # Only 0 and 1 turns fall below: at 2 turns ceil(20/6) = 4 clears it.
    assert window_candidate_cap(turns) == PER_WINDOW_CANDIDATE_CAP


def test_two_turns_already_clears_the_floor():
    assert window_candidate_cap(2) == 4


def test_never_below_the_legacy_flat_cap():
    # The change must be purely additive in headroom: no turn count may
    # produce a cap tighter than the flat 3 it replaces, or this becomes a
    # regression for some window sizes.
    for turns in range(0, 40):
        assert window_candidate_cap(turns) >= PER_WINDOW_CANDIDATE_CAP


def test_monotonic_in_turn_count():
    # More conversation may never yield less headroom.
    caps = [window_candidate_cap(t) for t in range(0, 40)]
    assert caps == sorted(caps)


def test_matches_stated_rate_at_block_boundaries():
    # The rate is the user-facing contract ("10 per every 6 exchanges"), so
    # pin it at exact multiples where there is no ceiling remainder.
    for blocks in (1, 2, 3, 5):
        turns = blocks * EXCHANGE_BLOCK_TURNS
        assert window_candidate_cap(turns) == blocks * CANDIDATES_PER_EXCHANGE_BLOCK


def test_full_window_gets_meaningful_headroom():
    # A full window is WINDOW_TURN_COUNT human turns.  Under the old flat cap
    # it was allowed 3 candidates; the whole point of the change is that a
    # dense full window is no longer the most-truncated case.
    assert window_candidate_cap(WINDOW_TURN_COUNT) > PER_WINDOW_CANDIDATE_CAP


def test_negative_turns_degrade_to_floor():
    # Defensive: callers derive the turn count from a window walk, so a
    # negative value would be a bug upstream — return the floor rather than
    # a negative budget that would silently drop every candidate.
    assert window_candidate_cap(-1) == PER_WINDOW_CANDIDATE_CAP


def test_formula_is_ceiling_not_floor():
    # A remainder must round UP, so a partial block still earns its share.
    # floor(20/6) = 3 would collapse into the floor and hide the scaling.
    assert window_candidate_cap(2) == math.ceil(
        2 * CANDIDATES_PER_EXCHANGE_BLOCK / EXCHANGE_BLOCK_TURNS
    )
