"""
Session-scoped shingle index for hallucination detection.

Stores fingerprints of every real tool result emitted to the model in a
session, then lets detectors check whether the model's assistant text is
reproducing prior tool output (parroting) rather than issuing a new
tool_use block.

Two complementary signals are tracked per tool result:
  * n-gram shingles (word-level, default n=5) -- catches paraphrased
    reproductions that share local word-order with the original
  * line hashes (normalized whitespace) -- catches verbatim line-level
    copies that span too few words for shingle overlap to trigger

Memory is bounded per result and per session. The index is thread-safe
but is intended for single-process use; distributed deployments would
need an external backing store.

See .ziya/hallucination-detection-design.md for the overall design.
"""
from __future__ import annotations

import hashlib
import re
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass


# Configuration defaults. These are overridable per-instance; callers
# that need different thresholds construct a custom ShingleIndex.
DEFAULT_SHINGLE_SIZE = 5
DEFAULT_MAX_SHINGLES_PER_RESULT = 200
DEFAULT_MAX_RESULTS_PER_SESSION = 100
DEFAULT_MIN_LINE_LENGTH = 20
DEFAULT_MIN_RESULT_LENGTH = 100

# Detection thresholds. A match is reported when the probe text shares
# at least LOW_CONFIDENCE matches with any registered fingerprint; the
# match is tagged 'high' when it also meets HIGH_CONFIDENCE on either
# signal.
SHINGLE_OVERLAP_HIGH_CONFIDENCE = 5
SHINGLE_OVERLAP_LOW_CONFIDENCE = 3
LINE_MATCH_HIGH_CONFIDENCE = 2
LINE_MATCH_LOW_CONFIDENCE = 1

_WORD_SPLIT_RE = re.compile(r'\S+')


@dataclass(frozen=True)
class ToolResultFingerprint:
    """Immutable fingerprint of a single registered tool result."""
    tool_use_id: str
    tool_name: str
    shingles: frozenset[int]
    line_hashes: frozenset[int]
    registered_at: float
    result_length: int


@dataclass(frozen=True)
class ShingleMatch:
    """Detection result when probe text matches a registered fingerprint."""
    matched_tool_use_id: str
    matched_tool_name: str
    shingle_overlap: int
    line_matches: int
    confidence: str  # 'high' or 'low'
    registered_at: float


def _hash_token(token: str) -> int:
    """Stable 64-bit hash via blake2b. Process-independent (not salted)."""
    digest = hashlib.blake2b(token.encode('utf-8'), digest_size=8).digest()
    return int.from_bytes(digest, 'big')


def _compute_shingles(text: str, size: int, max_count: int) -> frozenset[int]:
    """Word-level n-gram shingles, lowercased, bounded count."""
    tokens = _WORD_SPLIT_RE.findall(text.lower())
    if len(tokens) < size:
        return frozenset()
    shingles: set[int] = set()
    for i in range(len(tokens) - size + 1):
        shingle = ' '.join(tokens[i:i + size])
        shingles.add(_hash_token(shingle))
        if len(shingles) >= max_count:
            break
    return frozenset(shingles)


def _compute_line_hashes(text: str, min_length: int) -> frozenset[int]:
    """Hash each significant line after whitespace normalization."""
    hashes: set[int] = set()
    for raw_line in text.splitlines():
        stripped = raw_line.strip()
        if len(stripped) < min_length:
            continue
        normalized = ' '.join(stripped.split())
        hashes.add(_hash_token(normalized))
    return frozenset(hashes)


class ShingleIndex:
    """
    Per-session store of tool-result fingerprints with detection.

    Sessions are keyed by conversation_id. Within a session, tool
    results are keyed by tool_use_id and retained in insertion order,
    bounded by max_results_per_session with LRU eviction.
    """

    def __init__(
        self,
        shingle_size: int = DEFAULT_SHINGLE_SIZE,
        max_shingles_per_result: int = DEFAULT_MAX_SHINGLES_PER_RESULT,
        max_results_per_session: int = DEFAULT_MAX_RESULTS_PER_SESSION,
        min_line_length: int = DEFAULT_MIN_LINE_LENGTH,
        min_result_length: int = DEFAULT_MIN_RESULT_LENGTH,
    ) -> None:
        self._shingle_size = shingle_size
        self._max_shingles = max_shingles_per_result
        self._max_results = max_results_per_session
        self._min_line_length = min_line_length
        self._min_result_length = min_result_length
        self._sessions: dict[str, OrderedDict[str, ToolResultFingerprint]] = {}
        self._lock = threading.RLock()

    def register(
        self,
        conversation_id: str,
        tool_use_id: str,
        tool_name: str,
        result_text: str,
    ) -> bool:
        """
        Register a tool result in the session index.

        Returns True if the result was indexed, False if skipped (empty
        or below min_result_length, or produced no fingerprints).
        """
        if not conversation_id or not tool_use_id:
            return False
        if not result_text or len(result_text) < self._min_result_length:
            return False

        shingles = _compute_shingles(
            result_text, self._shingle_size, self._max_shingles
        )
        line_hashes = _compute_line_hashes(result_text, self._min_line_length)
        if not shingles and not line_hashes:
            return False

        fingerprint = ToolResultFingerprint(
            tool_use_id=tool_use_id,
            tool_name=tool_name or 'unknown',
            shingles=shingles,
            line_hashes=line_hashes,
            registered_at=time.time(),
            result_length=len(result_text),
        )

        with self._lock:
            session = self._sessions.setdefault(conversation_id, OrderedDict())
            if tool_use_id in session:
                # Re-registration: drop old entry so the replacement
                # enters at the LRU tail.
                del session[tool_use_id]
            session[tool_use_id] = fingerprint
            while len(session) > self._max_results:
                session.popitem(last=False)
        return True

    def check(
        self,
        conversation_id: str,
        text: str,
        skip_after_timestamp: float | None = None,
    ) -> ShingleMatch | None:
        """
        Check probe text against all session fingerprints.

        Returns the strongest match (highest confidence tier, then
        highest line-match count, then highest shingle overlap) that
        meets the low-confidence threshold, or None.

        ``skip_after_timestamp`` excludes fingerprints whose
        ``registered_at`` is >= the given timestamp. Used by the
        streaming detector to avoid flagging the model for legitimately
        summarizing a tool result it just received in the same
        iteration -- the fingerprint was registered mid-turn, so the
        assistant text quoting it is narration, not parroting.
        A value of ``None`` or ``0`` disables the filter.
        """
        if not conversation_id or not text:
            return None

        with self._lock:
            session = self._sessions.get(conversation_id)
            if not session:
                return None
            fingerprints = list(session.values())

        if skip_after_timestamp:
            fingerprints = [
                fp for fp in fingerprints
                if fp.registered_at < skip_after_timestamp
            ]
            if not fingerprints:
                return None

        # No practical cap on probe-side shingles; bounded by text length.
        text_shingles = _compute_shingles(
            text, self._shingle_size, max_count=10_000
        )
        text_line_hashes = _compute_line_hashes(text, self._min_line_length)
        if not text_shingles and not text_line_hashes:
            return None

        best: ShingleMatch | None = None
        for fp in fingerprints:
            shingle_overlap = len(text_shingles & fp.shingles)
            line_matches = len(text_line_hashes & fp.line_hashes)

            if (
                shingle_overlap < SHINGLE_OVERLAP_LOW_CONFIDENCE
                and line_matches < LINE_MATCH_LOW_CONFIDENCE
            ):
                continue

            high = (
                shingle_overlap >= SHINGLE_OVERLAP_HIGH_CONFIDENCE
                or line_matches >= LINE_MATCH_HIGH_CONFIDENCE
            )
            match = ShingleMatch(
                matched_tool_use_id=fp.tool_use_id,
                matched_tool_name=fp.tool_name,
                shingle_overlap=shingle_overlap,
                line_matches=line_matches,
                confidence='high' if high else 'low',
                registered_at=fp.registered_at,
            )
            if best is None or _match_score(match) > _match_score(best):
                best = match

        return best

    def clear_session(self, conversation_id: str) -> None:
        with self._lock:
            self._sessions.pop(conversation_id, None)

    def session_size(self, conversation_id: str) -> int:
        with self._lock:
            session = self._sessions.get(conversation_id)
            return len(session) if session else 0


def _match_score(m: ShingleMatch) -> tuple[int, int, int]:
    """Ordering key: confidence tier, then line matches, then shingle overlap."""
    tier = 1 if m.confidence == 'high' else 0
    return (tier, m.line_matches, m.shingle_overlap)


# Module-level default instance so callers don't have to thread an
# index object through unrelated code paths. A custom ShingleIndex can
# still be constructed for tests or specialized deployments.
_default_index = ShingleIndex()


def get_default_index() -> ShingleIndex:
    return _default_index


def register_tool_result(
    conversation_id: str,
    tool_use_id: str,
    tool_name: str,
    result_text: str,
) -> bool:
    return _default_index.register(
        conversation_id, tool_use_id, tool_name, result_text
    )


def check_for_parroting(
    conversation_id: str,
    text: str,
    skip_after_timestamp: float | None = None,
) -> ShingleMatch | None:
    return _default_index.check(
        conversation_id, text, skip_after_timestamp=skip_after_timestamp
    )


def clear_session(conversation_id: str) -> None:
    _default_index.clear_session(conversation_id)
