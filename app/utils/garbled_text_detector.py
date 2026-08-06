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


@dataclass
class GarbledSpan:
    """One contiguous region judged to be obfuscation-like."""
    start: int
    end: int
    upper_frac: float
    vowelless_frac: float

    @property
    def length(self) -> int:
        return self.end - self.start

    def __repr__(self) -> str:  # pragma: no cover - diagnostic only
        return (f"GarbledSpan({self.start}:{self.end} len={self.length} "
                f"upper={self.upper_frac:.3f} vless={self.vowelless_frac:.3f})")


def score_window(seg: str) -> Optional[Tuple[float, float]]:
    """Return ``(upper_frac, vowelless_frac)`` or None if under-evidenced."""
    letters = [c for c in seg if c.isalpha()]
    if len(letters) < MIN_LETTERS:
        return None
    upper_frac = sum(1 for c in letters if c.isupper()) / len(letters)

    words = [w for w in _WORD.findall(seg) if not _HEX_WORD.match(w)]
    if len(words) < MIN_WORDS:
        return None
    vowelless = sum(1 for w in words if len(w) >= 4 and not _VOWEL.search(w))
    return upper_frac, vowelless / len(words)


def find_garbled_spans(text: str) -> List[GarbledSpan]:
    """Locate obfuscation-like regions, merging overlapping windows."""
    if not text or len(text) < WINDOW // 2:
        return []

    hits: List[GarbledSpan] = []
    limit = max(1, len(text) - WINDOW + 1)
    for i in range(0, limit, STRIDE):
        scored = score_window(text[i:i + WINDOW])
        if not scored:
            continue
        upper_frac, vowelless_frac = scored
        if upper_frac >= UPPER_THRESHOLD and vowelless_frac >= VOWELLESS_THRESHOLD:
            hits.append(GarbledSpan(i, min(i + WINDOW, len(text)),
                                    upper_frac, vowelless_frac))

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
