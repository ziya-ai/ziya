"""
Salience patterns must match with OR without apostrophes.

Why this is load-bearing: _count_salience_hits() gates extraction per
window — a window scoring 0 is skipped with no extraction call at all.
The patterns were written with literal apostrophes ("doesn't work",
"let's go with"), and measurement showed 8/8 affected phrases failed on
unapostrophized input ("doesnt work", "lets go with"), which is how a
large fraction of real typing arrives.  Corrections and negative
constraints are the two highest-value categories in the pattern set, so
the failure was concentrated in exactly the wrong place.

These assert BOTH spellings, so a future regex edit that reintroduces a
literal apostrophe fails here rather than silently narrowing the gate.
"""
import pytest

from app.memory.extractor import _SALIENCE_PATTERNS, _count_salience_hits


# (with-apostrophe, without-apostrophe) — both must match.
#
# we'll / we're are deliberately ABSENT: their bare forms are the ordinary
# words "well" and "were", so apostrophe-optionality there produces false
# positives.  See test_bare_well_and_were_are_not_decisions below.
APOSTROPHE_PAIRS = [
    ("that's wrong", "thats wrong"),
    ("let's go with the probationary store", "lets go with the probationary store"),
    ("that doesn't work", "that doesnt work"),
    ("it won't work", "it wont work"),
    ("we can't use that", "we cant use that"),
    ("don't use flock here", "dont use flock here"),
    ("that didn't work", "that didnt work"),
    ("won't fly", "wont fly"),
    ("won't help", "wont help"),
    ("that's a bad idea", "thats a bad idea"),
]


@pytest.mark.parametrize("with_ap,without_ap", APOSTROPHE_PAIRS)
def test_matches_with_apostrophe(with_ap, without_ap):
    assert _SALIENCE_PATTERNS.search(with_ap), f"regressed: {with_ap!r}"


@pytest.mark.parametrize("with_ap,without_ap", APOSTROPHE_PAIRS)
def test_matches_without_apostrophe(with_ap, without_ap):
    # The actual fix.  Before apostrophes were made optional, every one of
    # these returned zero matches.
    assert _SALIENCE_PATTERNS.search(without_ap), (
        f"apostrophe-less form not matched: {without_ap!r} "
        f"(literal apostrophe reintroduced?)"
    )


def test_casual_decision_phrasing_is_salient():
    # "lets say 30 min, 6h" — a real decision statement that scored 0
    # before "let'?s say" was added.  Decisions arriving casually are still
    # decisions.
    assert _count_salience_hits(
        [{"role": "user", "content": "as for the interval, lets say 30 min, 6h"}]
    ) > 0


def test_window_with_only_apostropheless_signal_is_not_skipped():
    # End-to-end shape of the bug: a window whose ONLY durable signal is
    # unapostrophized must not score 0, because 0 means the window is
    # dropped before any extraction call is made.
    window = [
        {"role": "user", "content": "the sweep runs every thirty minutes"},
        {"role": "assistant", "content": "Understood."},
        {"role": "user", "content": "dont use the memories store for that"},
    ]
    assert _count_salience_hits(window) > 0


def test_non_salient_window_still_scores_zero():
    # Negative control: the gate must still discriminate.  If this ever
    # passes, the patterns have been widened into matching everything and
    # the gate is no longer a gate.
    window = [
        {"role": "user", "content": "ok"},
        {"role": "assistant", "content": "Done."},
        {"role": "user", "content": "thanks, looks good"},
    ]
    assert _count_salience_hits(window) == 0


# ── Apostrophe-optionality must NOT swallow real words ─────────────
#
# Making the apostrophe optional is safe for contractions whose bare form
# is not a word ("doesnt", "wont", "thats", "lets" as a decision verb).
# It is NOT safe for we'll / we're, whose bare forms are "well" and
# "were" — both common enough that the pattern started scoring ordinary
# prose as a decision.  These pin that exclusion.

NOT_SALIENT = [
    "well use whatever you think is best there",
    "it works well",
    "we were going to lunch",
    "the well is dry",
]

# "they were going with the other approach" is deliberately NOT in the list
# above: it matches on `going with`, a separate pre-existing decision
# alternation, so it cannot discriminate the we're/were question this block
# exists to pin.  A negative control that fails for an unrelated reason
# tests nothing.


def test_bare_were_alone_is_not_a_decision():
    # Isolates the actual risk: `were` with no other trigger present.
    assert not _SALIENCE_PATTERNS.search("they were unhappy with the result")


@pytest.mark.parametrize("text", NOT_SALIENT)
def test_bare_well_and_were_are_not_decisions(text):
    assert not _SALIENCE_PATTERNS.search(text), (
        f"false positive: {text!r} — was we'll/we're made apostrophe-optional? "
        f"the bare forms 'well' and 'were' are ordinary words"
    )


CONTRACTED_STILL_MATCHES = [
    "we'll use the event log",
    "we're going with 30 minutes",
]


@pytest.mark.parametrize("text", CONTRACTED_STILL_MATCHES)
def test_apostrophised_forms_still_match(text):
    # The exclusion above must not cost the legitimate contracted form.
    assert _SALIENCE_PATTERNS.search(text), f"regressed: {text!r}"
