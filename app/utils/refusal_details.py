"""
Refusal detail: what a provider said about WHY it refused, and whether the
refusal-recovery ladder may retry it.

Anthropic-family responses carry ``delta.stop_details = {"type": "refusal",
"category": ..., "explanation": ...}`` beside ``stop_reason: "refusal"``.
Observed categories:

* ``reasoning_extraction`` -- the payload-size-dependent verdict the ladder
  was built for (fable5 2026-08; the field was null when the ladder landed on
  2026-08-11 and populated by 2026-08-20; fable-5-2 2026-11-10).  Retrying
  with a legitimately smaller request is measured to help.
* ``cyber`` -- a prompt-SHAPE verdict (enumerated-rule scaffolds, opus5);
  sending less does not change it.

The ladder therefore runs only when the category is absent (older API shape,
or a provider that reuses ``refusal`` without details -- the original case)
or listed in :data:`RETRYABLE_REFUSAL_CATEGORIES`.  Any other named category
ends the turn with the provider's explanation: automatically resubmitting a
content or security refusal in reduced form is an attempt to get it past the
classifier, which Ziya does not do.
"""
from __future__ import annotations

from typing import Any, Optional, Tuple

RETRYABLE_REFUSAL_CATEGORIES = frozenset({"reasoning_extraction"})


def _text(value: Any) -> Optional[str]:
    # Strings only: an SDK object or a test MagicMock must not turn into
    # "<MagicMock ...>" in a banner.
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def stop_details_fields(stop_details: Any) -> Tuple[Optional[str], Optional[str]]:
    """``(category, explanation)`` from a dict (Bedrock JSON) or an object
    (Anthropic SDK).  ``(None, None)`` when absent or unusable."""
    if stop_details is None:
        return None, None
    if isinstance(stop_details, dict):
        return _text(stop_details.get("category")), _text(stop_details.get("explanation"))
    return (_text(getattr(stop_details, "category", None)),
            _text(getattr(stop_details, "explanation", None)))


def refusal_is_recoverable(category: Optional[str]) -> bool:
    """Whether the refusal-recovery ladder may retry a refusal of *category*."""
    return not category or category in RETRYABLE_REFUSAL_CATEGORIES
