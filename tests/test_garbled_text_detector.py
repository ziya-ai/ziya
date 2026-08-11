"""Threshold tests for the garbled-text detector.

This module gates a DESTRUCTIVE transform: a flagged span is replaced by a
placeholder before the request is sent, so a false positive silently deletes
legitimate content from the user's context.  It shipped with no tests, and
the docstring's validation claims ("1 false positive across 1,535 repo
files") were not reproducible.

Two bugs motivate the fixtures below, both found by measurement rather than
inspection:

1. A 1500-char window of real CPSS switch-driver source scored
   ``upper=0.764 vowelless=0.314`` and was being redacted from every
   request.  The entire vowel-less score came from TWO distinct tokens --
   ``CPSS`` and ``DXCH``, each repeated 24 times -- because ``_WORD``
   splits ``CPSS_DXCH_*`` identifiers on the underscore.  The diversity
   signal exists to separate that from genuine obfuscation.

2. The first diversity threshold was validated against a fixture built by
   repeating ONE sentence 14 times.  Enciphering that yields only ~6
   distinct tokens, so it scored div~0.10 -- below the gate -- and the
   "fix" silently destroyed true-positive detection.  Every cipher fixture
   here therefore uses NON-REPEATING prose, which is what real obfuscated
   text looks like.  A repeating-prose case is kept as an xfail to pin that
   known blind spot rather than pretend it does not exist.
"""
import base64
import random
import string

import pytest

from app.utils.garbled_text_detector import (
    DIVERSITY_THRESHOLD,
    GarbledSpan,
    mask_encoded_runs,
    MIN_LETTERS,
    MIN_WORDS,
    UPPER_THRESHOLD,
    VOWELLESS_THRESHOLD,
    WINDOW,
    find_garbled_spans,
    score_window,
)


# --------------------------------------------------------------- fixtures

# Non-repeating prose. Length matters: enciphering drops letters, so the
# source must be comfortably longer than WINDOW to still fill a window.
_PROSE = (
    "Engineering teams frequently underestimate how much operational "
    "complexity accumulates when a distributed platform grows beyond a "
    "handful of services. Every additional component introduces "
    "configuration, monitoring obligations, deployment coordination, and "
    "failure modes that interact with neighbours in surprising ways. "
    "Documentation drifts away from reality, runbooks reference hostnames "
    "that no longer exist, and dashboards quietly stop reporting after a "
    "metric namespace changes. Investigating an incident then requires "
    "piecing together evidence from logs whose retention windows differ, "
    "traces sampled at inconsistent rates, and alarms whose thresholds "
    "nobody remembers tuning. Rebuilding confidence demands deliberate "
    "investment: consolidating telemetry, deleting redundant abstractions, "
    "writing tests that exercise realistic failure scenarios, and "
    "rehearsing recovery procedures regularly enough that muscle memory "
    "forms before the pager fires unexpectedly again. Teams that skip this "
    "work discover the cost during the next unplanned outage instead."
)


def _caesar(text: str, shift: int, drop: str) -> str:
    """Upper-case Caesar shift with some letters deleted.

    Mimics the observed PDF-paste corruption: case destroyed, letters
    missing, residue reading like a substitution cipher.
    """
    out = []
    for ch in text:
        if ch.lower() in drop:
            continue
        if ch.isalpha():
            out.append(chr((ord(ch.upper()) - 65 + shift) % 26 + 65))
        else:
            out.append(ch)
    return "".join(out)


# Stand-in for the real switch source that was being redacted. Verified to
# reproduce the production signature: synthetic (0.765, 0.353, 0.028) vs
# real (0.764, 0.314, 0.042). Kept synthetic so no proprietary source is
# committed, while still failing the old two-signal gate.
_SCREAMING_IDENTIFIERS = (
    "#define CPSS_DXCH_PCL_OFFSET_L4_E 8\n"
    "#define CPSS_DXCH_TTI_KEY_UDB_E 17\n"
    "#define CPSS_DXCH_PCL_RULE_FORMAT_E 3\n"
    "static const CPSS_DXCH_TTI_UDB_CONFIG_T cfg[] = {\n"
    "    {19, CPSS_DXCH_PCL_OFFSET_L4_E, 8, 0xf0},\n"
    "    {20, CPSS_DXCH_TTI_KEY_UDB_E, 12, 0x0f},\n"
) * 12

_ENV_VARS = (
    "ZIYA_DUMP_REQUEST_PARTS ZIYA_LOAD_INTERNAL_PLUGINS AWS_REGION "
    "AWS_PROFILE LD_LIBRARY_PATH PKG_CONFIG_PATH XDG_RUNTIME_DIR "
) * 24

_SQL = (
    "SELECT COUNT DISTINCT FROM WHERE GROUP BY HAVING ORDER INNER JOIN "
    "UNION ALL CREATE INDEX ALTER TABLE PRIMARY KEY FOREIGN REFERENCES "
) * 20


def _passes_old_gate(seg: str) -> bool:
    """Whether the pre-diversity two-signal gate would have flagged."""
    scored = score_window(seg)
    if not scored:
        return False
    upper, vowelless, _ = scored
    return upper >= UPPER_THRESHOLD and vowelless >= VOWELLESS_THRESHOLD


# ------------------------------------------------- false positives (regression)

@pytest.mark.parametrize("name,text", [
    ("screaming_identifiers", _SCREAMING_IDENTIFIERS),
    ("env_vars", _ENV_VARS),
    ("sql_keywords", _SQL),
    ("plain_prose", _PROSE * 3),
])
def test_legitimate_content_is_not_flagged(name, text):
    assert find_garbled_spans(text) == [], f"{name} was flagged"


def test_screaming_identifiers_would_have_failed_the_old_gate():
    """Pin the actual regression, not just the absence of a hit.

    Without this, the false-positive test above could pass for the wrong
    reason (e.g. a fixture that never tripped the old gate either) and the
    diversity signal could be deleted with the suite still green.
    """
    window = _SCREAMING_IDENTIFIERS[:WINDOW]
    assert _passes_old_gate(window), "fixture no longer reproduces the bug"
    upper, vowelless, diversity = score_window(window)
    assert diversity < DIVERSITY_THRESHOLD
    assert find_garbled_spans(_SCREAMING_IDENTIFIERS) == []


# -------------------------------------------------- true positives (must flag)

@pytest.mark.parametrize("shift,drop", [
    (21, "lsuw"),   # the shift/drop combination observed in production
    (7, "eiu"),
    (5, "aei"),
    (4, "aeo"),
    (10, "eiu"),
])
def test_enciphered_prose_is_flagged(shift, drop):
    """Shift choice matters and is not arbitrary.

    A Caesar shift only looks vowel-less if the letters that MAP ONTO
    vowels are rare in the plaintext.  Shift 13 sends N,R,V,B,H -> vowels,
    and English is dense in N/R/H, so the ciphertext REGAINS vowels
    (vless=0.069, under the gate) and is legitimately undetectable by this
    heuristic.  That is a property of the cipher, not a detector defect, so
    only shifts whose vowel sources are rare letters belong here.
    """
    garbled = _caesar(_PROSE, shift, drop)
    assert len(garbled) >= WINDOW // 2, "fixture too short to score"
    assert find_garbled_spans(garbled), (
        f"shift={shift} drop={drop!r} not flagged: "
        f"{score_window(garbled[:WINDOW])}"
    )


def test_base32_blob_is_flagged():
    random.seed(1234)
    blob = "".join(
        random.choice(string.ascii_uppercase + "234567") if i % 9 else " "
        for i in range(2 * WINDOW)
    )
    assert find_garbled_spans(blob)


# ------------------------------------------------- encoded payloads (images)

def _image_base64(exponent: float, size: int = 30000) -> str:
    """base64 of image-like bytes: skewed, not uniform.

    A rendered score is mostly white with black glyphs, so its deflated
    bytes cluster low. Uniform random bytes score upper=0.53 (below the
    gate) and never reproduced the bug; exponent 2.0 measures
    upper=0.64 vowelless=0.10 diversity=1.00, which trips all three.
    """
    random.seed(7)
    return base64.b64encode(
        bytes(int(255 * (random.random() ** exponent)) for _ in range(size))
    ).decode()


def test_image_base64_would_have_failed_the_old_gate():
    """Pin the regression itself, not merely the absence of a hit.

    Without this, the exemption tests below could pass for the wrong reason
    (a fixture that never tripped the gate) and the masking could be removed
    with the suite still green.
    """
    blob = _image_base64(2.0)
    unmasked = [
        score_window(blob[i:i + WINDOW])
        for i in range(0, max(1, len(blob) - WINDOW + 1), WINDOW // 2)
    ]
    tripped = [
        s for s in unmasked
        if s and s[0] >= UPPER_THRESHOLD and s[1] >= VOWELLESS_THRESHOLD
        and s[2] >= DIVERSITY_THRESHOLD
    ]
    assert tripped, "fixture no longer reproduces the image false positive"
    # Diversity is useless here: base64 is maximally varied by construction.
    assert all(s[2] > 0.9 for s in tripped)
    assert find_garbled_spans(blob) == []


@pytest.mark.parametrize("label,payload", [
    ("raw_base64", _image_base64(2.0)),
    ("data_uri", '<img src="data:image/png;base64,'
                 + _image_base64(2.0) + '" alt="Rendered diagram" />'),
    ("image_between_prose", "The score renders as follows.\n"
                            + _image_base64(2.0) + "\nEight staves, F# major."),
    ("base64url_alphabet", _image_base64(2.0).replace("+", "-").replace("/", "_")),
])
def test_encoded_payloads_are_not_flagged(label, payload):
    """An inline render must survive intact.

    Splicing a placeholder into a data URI produces an invalid image, and
    the provider then refuses the whole request -- strictly worse than the
    refusal this module exists to prevent.
    """
    assert find_garbled_spans(payload) == [], f"{label} was flagged"


def test_garble_adjacent_to_an_image_is_still_flagged():
    """Masking must not become a way to smuggle garble past the detector."""
    payload = _caesar(_PROSE, 21, "lsuw") + "\n" + _image_base64(2.0)
    spans = find_garbled_spans(payload)
    assert spans, "garble alongside an image was missed"
    # Only the prose region, not the image tail.
    assert min(s.start for s in spans) < len(_caesar(_PROSE, 21, "lsuw"))


def test_mask_encoded_runs_preserves_offsets():
    """Offsets are load-bearing: the caller slices the ORIGINAL text.

    If masking changed length, every span after the first image would be
    applied at the wrong position -- corrupting text far from any image.
    """
    blob = _image_base64(2.0, size=1000)
    text = "prefix " + blob + " suffix"
    masked, runs = mask_encoded_runs(text)
    assert runs == 1
    assert len(masked) == len(text)
    assert masked.startswith("prefix ") and masked.endswith(" suffix")
    assert masked[7:7 + len(blob)].strip() == ""


def test_mask_encoded_runs_leaves_prose_untouched():
    """Prose breaks on spaces, so it can never form a 200-char run."""
    masked, runs = mask_encoded_runs(_PROSE * 3)
    assert runs == 0 and masked == _PROSE * 3


@pytest.mark.xfail(reason="known blind spot: low token diversity", strict=True)
def test_enciphered_repeating_prose_is_flagged():
    """Pins a real limitation of the diversity signal.

    Enciphering ONE repeated sentence yields few distinct tokens, so it
    scores like code and is missed.  Documented as xfail(strict) so that if
    a future change starts catching it the test fails loudly and this note
    gets revisited, rather than the gap being rediscovered by a refusal.
    """
    single = (
        "Please arrive at the front entrance and check in with the "
        "receptionist who will direct you to the correct room upstairs. "
    )
    assert find_garbled_spans(_caesar(single * 16, 21, "lsuw"))


# ------------------------------------------------------------ score_window

def test_score_window_returns_three_signals():
    scored = score_window(_caesar(_PROSE, 21, "lsuw")[:WINDOW])
    assert scored is not None and len(scored) == 3
    assert all(0.0 <= v <= 1.0 for v in scored)


def test_score_window_requires_minimum_evidence():
    assert score_window("") is None
    assert score_window("A" * (MIN_LETTERS - 1)) is None
    # Enough letters, too few distinct words: one long run is not evidence.
    assert score_window("QWRTP " * (MIN_WORDS - 1)) is None


def test_diversity_is_zero_when_no_vowelless_words():
    scored = score_window(_PROSE[:WINDOW])
    assert scored is not None
    _, vowelless, diversity = scored
    assert vowelless == 0.0 and diversity == 0.0


def test_hex_words_do_not_register():
    """Hex dumps are legitimately vowel-less and must be excluded."""
    hexdump = ("DEADBEEF CAFEBABE 0123456789ABCDEF FEEDFACE " * 40)
    scored = score_window(hexdump[:WINDOW])
    if scored is not None:
        assert scored[1] == 0.0
    assert find_garbled_spans(hexdump) == []


def test_short_text_is_skipped_entirely():
    assert find_garbled_spans("x" * (WINDOW // 2 - 1)) == []


# -------------------------------------------------------------- GarbledSpan

def test_garbled_span_diversity_defaults_to_flagging():
    """Four-arg construction must remain valid and must not silently pass.

    Defaulting to 1.0 keeps any pre-existing caller flagging; defaulting to
    0.0 would make it stop, which is the failure mode that is hard to see.
    """
    span = GarbledSpan(0, WINDOW, 0.9, 0.3)
    assert span.diversity == 1.0
    assert span.diversity >= DIVERSITY_THRESHOLD
    assert span.length == WINDOW


def test_overlapping_windows_merge_with_max_signals():
    garbled = _caesar(_PROSE, 21, "lsuw")
    spans = find_garbled_spans(garbled)
    assert len(spans) == 1, f"expected one merged span, got {spans}"
    assert spans[0].start == 0
    assert spans[0].end <= len(garbled)


def test_clean_text_around_garble_is_preserved():
    """The span must not swallow the whole message.

    The garbled region must exceed WINDOW.  Detection scores fixed 1500-char
    windows, so a shorter run of garble can never fill one -- every window
    overlapping it also contains clean prose, which dilutes the score below
    the gate.  A 926-char fixture here failed for exactly that reason, which
    is a limitation of window-based scoring rather than a bug: sub-window
    garble is invisible to this detector by construction.
    """
    clean = _PROSE * 2
    garbled = _caesar(_PROSE * 3, 21, "lsuw")
    assert len(garbled) > WINDOW, "garble must exceed one window"
    spans = find_garbled_spans(clean + garbled + clean)
    assert spans, "garble inside clean text was missed"
    # Windows are strided, so the reported span edges are quantised and can
    # overhang the true boundary by up to one window on each side.
    assert spans[0].start >= len(clean) - WINDOW, "span began too early"
    assert spans[-1].end <= len(clean) + len(garbled) + WINDOW, \
        "span ran too far past the garble"
    total = sum(s.length for s in spans)
    assert total < len(garbled) + 2 * WINDOW, "span covered the clean regions"


# --------------------------------------------------------------- thresholds

def test_thresholds_are_ordered_sanely():
    assert 0.0 < VOWELLESS_THRESHOLD < DIVERSITY_THRESHOLD < UPPER_THRESHOLD < 1.0


def test_separation_margin_between_classes():
    """The gate is hand-tuned; assert the margin it rests on still exists."""
    worst_true = min(
        score_window(_caesar(_PROSE, sh, dr)[:WINDOW])[2]
        for sh, dr in ((21, "lsuw"), (7, "eiu"), (5, "aei"), (13, "aeo"))
    )
    worst_false = max(
        score_window(t[:WINDOW])[2]
        for t in (_SCREAMING_IDENTIFIERS, _ENV_VARS)
        if score_window(t[:WINDOW]) is not None
    )
    assert worst_false < DIVERSITY_THRESHOLD < worst_true
    assert worst_true > worst_false * 4, (
        f"margin collapsed: false={worst_false:.3f} true={worst_true:.3f}"
    )
