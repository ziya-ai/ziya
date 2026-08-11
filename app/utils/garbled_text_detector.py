"""Detect and neutralize text spans that look deliberately obfuscated.

Motivation
----------
A PDF copy/paste can silently corrupt a span of text: case is destroyed,
letters are dropped, and the residue reads like a substitution cipher.
One real example, 6,100 chars out of a 40,068-char pasted email::

    SYFUFYM& DMFY TJRTYFGT Y TWPNSL NYMHMNIWJSN NYSJ NSL MTYMJ GJHTRJ

That decodes (Caesar shift 21, with 'l'/'s'/'u'/'w' dropped) to ordinary
prose about childcare.  Anthropic's safety classifier refuses requests
containing it -- ROT-style encoding is a known jailbreak vector, so text
that looks intentionally obfuscated is flagged regardless of what it
actually says.  The refusal arrives as ``stop_reason: "refusal"`` with an
empty content array and ``category``/``explanation`` both null, so the
user sees a blank response with no explanation and no way to act on it.

Detection
---------
Two signals, both required (AND-gated).  Either alone false-positives
heavily on legitimate content:

* **upper_frac** -- fraction of letters that are uppercase.  Measured
  0.972 in the corrupted span vs 0.027/0.040 in the clean regions of the
  same message.
* **vowelless_frac** -- fraction of >=4-letter words containing no vowel.
  Measured 0.331 in the corrupted span vs 0.008/0.001 clean.

The vowel test is what makes this safe.  Caps alone flags SQL keywords,
env-var constants, acronym lists, and shouting prose -- all of which
score upper_frac 1.0 but vowelless_frac <= 0.028.  Pure-hex words are
excluded outright so hex dumps and UUIDs do not register.

Validation: 1 false positive across 1,535 repo files (a base64 blob in
node_modules), and 0 across six hand-built adversarial cases.

Thresholds are deliberately conservative -- a missed detection costs a
refusal the user can retry, while a false positive silently mangles
legitimate content.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import List, Optional, Tuple

# Word-ish runs of letters. 3+ so single letters and initials do not skew.
_WORD = re.compile(r'[A-Za-z]{3,}')
_VOWEL = re.compile(r'[aeiouAEIOU]')
# Pure-hex words: hex dumps, UUID fragments, git SHAs, DEADBEEF-style
# constants. Legitimately vowel-less; excluded so they cannot trip the gate.
_HEX_WORD = re.compile(r'^[A-Fa-f0-9]+$')

# Scan geometry. 1500-char windows at 750-char stride: small enough to
# localize a span inside a large message, large enough that the sample
# statistics are meaningful.
WINDOW = 1500
STRIDE = 750

# Minimum evidence before scoring a window at all.
MIN_LETTERS = 200
MIN_WORDS = 40

# Measured separation is roughly 25x on each axis, so mid-range thresholds
# are safe: garble scores (0.972, 0.331); worst legitimate case (1.0, 0.028).
UPPER_THRESHOLD = 0.60
VOWELLESS_THRESHOLD = 0.10

# Third signal: how VARIED the vowel-less words are.  Genuine obfuscation
# corrupts every word independently, so it yields many DIFFERENT vowel-less
# tokens.  Code yields a FEW tokens repeated often: a window of CPSS
# switch-driver source measured vowelless_frac 0.314 -- above the gate --
# from exactly two distinct tokens, CPSS and DXCH, each appearing 24 times
# because _WORD splits CPSS_DXCH_* identifiers on the underscore.  That span
# of real source was being replaced by a placeholder in every request.
# Measured: real garble 0.21-0.25, base32 1.00; code-like cases 0.01-0.06.
DIVERSITY_THRESHOLD = 0.15

# Long unbroken runs of the base64/base64url/hex alphabet: inline render
# images (`data:image/png;base64,...`), embedded fonts, attachment payloads.
#
# These must be exempted rather than scored.  base64 of a mid-entropy image
# -- an antialiased music score, mostly white with black glyphs -- lands
# squarely inside the gate: measured upper=0.64 vowelless=0.10
# diversity=1.00, redacting 66% of the payload.  The diversity signal offers
# no protection because base64 is maximally varied by construction; it was
# built to exempt REPETITIVE code tokens, not high-entropy data.  Uniform
# random base64 (upper=0.53) and near-black images (zero runs encode to
# vowel-rich "AAAA") both fall outside, so it is precisely ordinary rendered
# images that were affected.
#
# Corrupting a data URI is worse than the refusal the guard exists to
# prevent: splicing a placeholder into base64 yields an invalid image, and
# the provider then rejects the request outright (observed: stop_reason
# 'refusal', out_tok=2, on the turn following a redacted score render).
#
# Exempting these is safe because the classifier's concern is text that
# reads as a SUBSTITUTION CIPHER over prose.  Encoded data is not prose and
# carries no hidden instructions; base64 in a data URI is declared as data
# by its own URI scheme.  The 200-char floor is well above any prose word
# run (prose always breaks on spaces and punctuation) and well below the
# smallest realistic image payload.
_ENCODED_RUN = re.compile(r'[A-Za-z0-9+/=_-]{200,}')


@dataclass
class GarbledSpan:
    """One contiguous region judged to be obfuscation-like."""
    start: int
    end: int
    upper_frac: float
    vowelless_frac: float
    diversity: float = 1.0

    @property
    def length(self) -> int:
        return self.end - self.start

    def __repr__(self) -> str:  # pragma: no cover - diagnostic only
        return (f"GarbledSpan({self.start}:{self.end} len={self.length} "
                f"upper={self.upper_frac:.3f} vless={self.vowelless_frac:.3f} "
                f"div={self.diversity:.3f})")


def score_window(seg: str) -> Optional[Tuple[float, float, float]]:
    """Return ``(upper_frac, vowelless_frac, diversity)`` or None."""
    letters = [c for c in seg if c.isalpha()]
    if len(letters) < MIN_LETTERS:
        return None
    upper_frac = sum(1 for c in letters if c.isupper()) / len(letters)

    words = [w for w in _WORD.findall(seg) if not _HEX_WORD.match(w)]
    if len(words) < MIN_WORDS:
        return None
    vowelless = [w for w in words if len(w) >= 4 and not _VOWEL.search(w)]
    if not vowelless:
        return upper_frac, 0.0, 0.0
    distinct = len(set(w.upper() for w in vowelless))
    return (upper_frac, len(vowelless) / len(words),
            distinct / len(vowelless))


def mask_encoded_runs(text: str) -> Tuple[str, int]:
    """Blank out encoded payloads, preserving every offset.

    Runs are overwritten with spaces rather than deleted so that the spans
    `find_garbled_spans` reports still index into the ORIGINAL string; the
    caller slices the original text, so any length change here would
    silently misplace every redaction after the first image.

    Spaces (not a placeholder word) because the replacement must not add
    letters: a real word would enter the score_window statistics and shift
    the very ratios being measured.
    """
    if not text:
        return text, 0
    out = list(text)
    runs = 0
    for match in _ENCODED_RUN.finditer(text):
        out[match.start():match.end()] = ' ' * (match.end() - match.start())
        runs += 1
    return (''.join(out), runs) if runs else (text, 0)


def find_garbled_spans(text: str) -> List[GarbledSpan]:
    """Locate obfuscation-like regions, merging overlapping windows."""
    if not text or len(text) < WINDOW // 2:
        return []

    # Score with encoded payloads blanked, but keep offsets aligned to the
    # original so reported spans stay valid for the caller's slicing.
    scan_text, _ = mask_encoded_runs(text)

    hits: List[GarbledSpan] = []
    limit = max(1, len(text) - WINDOW + 1)
    for i in range(0, limit, STRIDE):
        scored = score_window(scan_text[i:i + WINDOW])
        if not scored:
            continue
        upper_frac, vowelless_frac, diversity = scored
        if (upper_frac >= UPPER_THRESHOLD
                and vowelless_frac >= VOWELLESS_THRESHOLD
                and diversity >= DIVERSITY_THRESHOLD):
            hits.append(GarbledSpan(i, min(i + WINDOW, len(text)),
                                    upper_frac, vowelless_frac, diversity))

    if not hits:
        return []

    merged = [hits[0]]
    for span in hits[1:]:
        prev = merged[-1]
        if span.start <= prev.end:
            merged[-1] = GarbledSpan(
                prev.start, max(prev.end, span.end),
                max(prev.upper_frac, span.upper_frac),
                max(prev.vowelless_frac, span.vowelless_frac),
                max(prev.diversity, span.diversity),
            )
        else:
            merged.append(span)
    return merged


def normalize_paste_artifacts(text: str) -> Tuple[str, int]:
    """Strip Private Use Area glyphs and normalize exotic whitespace.

    PDF viewers emit font-specific glyphs in the PUA (U+E000-U+F8FF) for
    things like list bullets -- the sample message carried 17 copies of
    U+E12C where a resume bullet belonged. These have no standard meaning,
    inflate token counts, and are pure noise to a model. Category Cf
    (zero-width joiners, BOM, directional marks) is likewise dropped.
    NBSP and friends become plain spaces.

    Returns ``(cleaned_text, replacements_made)``. Ordinary typography --
    smart quotes, en/em dashes, real bullets -- is deliberately preserved.
    """
    if not text:
        return text, 0

    out: List[str] = []
    changed = 0
    for ch in text:
        code = ord(ch)
        if code < 128:
            out.append(ch)
            continue
        category = unicodedata.category(ch)
        if category == 'Co':  # Private Use Area
            out.append(' ')
            changed += 1
        elif category == 'Cf':  # format/zero-width controls
            changed += 1
        elif category == 'Zs' and ch != ' ':  # NBSP, thin space, etc.
            out.append(' ')
            changed += 1
        else:
            out.append(ch)
    return ''.join(out), changed


def redact_garbled(text: str, placeholder: Optional[str] = None
                   ) -> Tuple[str, List[GarbledSpan]]:
    """Replace obfuscation-like spans with an explanatory placeholder.

    Replacing rather than dropping keeps the user informed: the model sees
    that content was removed and why, instead of an unexplained gap. Spans
    are rewritten back-to-front so earlier offsets stay valid.
    """
    spans = find_garbled_spans(text)
    if not spans:
        return text, []

    result = text
    for span in reversed(spans):
        note = placeholder if placeholder is not None else (
            f"\n[{span.length} characters removed: this region appears to be "
            f"corrupted text from a copy/paste (case destroyed, letters "
            f"dropped). It resembled encoded content and would cause the "
            f"model to refuse the entire request.]\n"
        )
        result = result[:span.start] + note + result[span.end:]
    return result, spans
