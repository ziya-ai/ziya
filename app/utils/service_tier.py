"""Bedrock service-tier selection for Task Card runs.

Amazon Bedrock prices on-demand inference in tiers: ``priority`` (premium),
``default`` (standard) and ``flex`` (discounted, higher latency, same quota).
Task Card runs are the batch-like workload the Flex tier is priced for, so
while a run is active every Bedrock request carries the requested tier
unless the operator has disabled it.

Wire formats (verified against botocore 1.42.81 and live endpoints):

* ``invoke_model[_with_response_stream]`` — kwarg ``serviceTier="flex"``,
  sent as the ``X-Amzn-Bedrock-Service-Tier`` header.
* ``converse[_stream]`` — kwarg ``serviceTier={"type": "flex"}``.
* ``bedrock-mantle`` (Anthropic Messages and OpenAI Responses paths) —
  probed 2026-09-04: only ``default``/``auto`` are accepted; ``flex`` and
  ``priority`` are rejected with HTTP 400 and the SigV4 header is silently
  ignored.  Those providers therefore do not consult this module.

Support is per model, not per account: on bedrock-runtime, gpt-oss,
DeepSeek v3.2, Qwen3 and MiniMax accept ``flex`` today while Claude, Nova
and Llama reject it with ``ValidationException: The provided service tier
is not supported for this model``.  Rather than maintain a support matrix
that AWS changes without notice, callers send the tier optimistically and
use :func:`is_service_tier_unsupported_error` + :func:`mark_unsupported`
to retry once without it and remember the answer for the process
lifetime, so a rejecting model pays the extra round-trip once.
"""

from __future__ import annotations

import contextvars
import os
import threading
from typing import Any, Dict, Optional, Set, Tuple

from app.context import get_task_service_tier

ENV_VAR = "ZIYA_TASK_SERVICE_TIER"
DEFAULT_TASK_TIER = "flex"
VALID_TIERS = ("priority", "default", "flex", "reserved")
_DISABLE_VALUES = ("", "off", "none", "default", "standard", "0", "false")

# (model_id, tier) pairs the service has rejected. Process-lifetime; a
# restart re-probes, which is the desired behaviour when AWS enables a
# tier for a model that previously rejected it.
_unsupported: Set[Tuple[str, str]] = set()
_lock = threading.Lock()

# Tier the most recent provider call in this context actually ran at, as
# reported by :func:`note_call`.  Read by the usage meter when it closes
# an iteration, so the ledger records the RESOLVED tier (what was billed)
# rather than the REQUESTED one (the ContextVar above, which a model may
# have rejected).  Cleared by the meter at the start of each iteration.
_resolved_tier: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "resolved_service_tier", default=None
)


def resolve_task_tier_from_env(env: Optional[Dict[str, str]] = None) -> Optional[str]:
    """Return the tier task runs should request, or None when disabled.

    Unset → ``flex``.  ``default``/``off``/``none``/empty → None (send
    nothing, so the request is billed at the standard tier exactly as
    before this feature existed).  Any other recognised tier name is
    passed through; unrecognised values disable the feature and are
    reported by the caller rather than silently coerced.
    """
    env = os.environ if env is None else env
    raw = env.get(ENV_VAR)
    if raw is None:
        return DEFAULT_TASK_TIER
    value = raw.strip().lower()
    if value in _DISABLE_VALUES:
        return None
    if value in VALID_TIERS:
        return value
    return None


def is_valid_tier_setting(raw: Optional[str]) -> bool:
    """True if ``raw`` is a value :func:`resolve_task_tier_from_env` understands."""
    if raw is None:
        return True
    value = raw.strip().lower()
    return value in _DISABLE_VALUES or value in VALID_TIERS


def requested_tier(model_id: str) -> Optional[str]:
    """The tier to send for ``model_id`` on this request, or None.

    None when no task run is active, when the run has the tier disabled,
    or when this model has already rejected the tier in this process.
    """
    tier = get_task_service_tier()
    if not tier:
        return None
    with _lock:
        if (model_id, tier) in _unsupported:
            return None
    return tier


def invoke_kwargs(model_id: str) -> Dict[str, Any]:
    """Extra kwargs for ``invoke_model[_with_response_stream]``."""
    tier = requested_tier(model_id)
    return {"serviceTier": tier} if tier else {}


def converse_kwargs(model_id: str) -> Dict[str, Any]:
    """Extra kwargs for ``converse[_stream]``."""
    tier = requested_tier(model_id)
    return {"serviceTier": {"type": tier}} if tier else {}


def is_service_tier_unsupported_error(error_text: str) -> bool:
    """True if ``error_text`` is the service rejecting the tier itself.

    Matches the bedrock-runtime ValidationException wording and both
    mantle variants, so a caller can distinguish "drop the tier and
    retry" from every other ValidationException (which must surface).
    """
    text = (error_text or "").lower()
    return (
        "service tier is not supported" in text
        or "unsupported service_tier" in text
        or "not supported for 'service_tier'" in text
    )


def mark_unsupported(model_id: str, tier: str) -> None:
    """Remember that ``model_id`` rejected ``tier`` for the rest of this process."""
    with _lock:
        _unsupported.add((model_id, tier))


def is_marked_unsupported(model_id: str, tier: str) -> bool:
    with _lock:
        return (model_id, tier) in _unsupported


def reset_unsupported_cache() -> None:
    """Test hook: forget every recorded rejection."""
    with _lock:
        _unsupported.clear()


def resolved_tier_from_response(response: Any) -> Optional[str]:
    """Extract the tier the service reports it actually used, if visible.

    ``invoke_model*`` echoes it in the ``x-amzn-bedrock-service-tier``
    response header; ``converse`` returns a top-level ``serviceTier``
    structure.  Returns None when neither is present so callers can log
    "requested X, resolved unknown" rather than assume the discount.
    """
    if not isinstance(response, dict):
        return None
    st = response.get("serviceTier")
    if isinstance(st, dict) and st.get("type"):
        return str(st["type"])
    headers = (response.get("ResponseMetadata") or {}).get("HTTPHeaders") or {}
    for k, v in headers.items():
        if k.lower() == "x-amzn-bedrock-service-tier":
            return str(v)
    return None


def normalize_tier(tier: Optional[str]) -> str:
    """Map a wire tier name onto the ledger's vocabulary.  Bedrock calls
    the standard tier ``default``; the pricing catalog calls it
    ``standard`` (design/CostModel.md).  Unknown strings pass through
    unchanged so the pricer can refuse to price them."""
    if not tier:
        return "standard"
    t = str(tier).strip().lower()
    return "standard" if t in ("default", "standard", "on_demand", "on-demand") else t


def note_call(model_id: str, sent_tier: Any, response: Any) -> str:
    """Record, for the usage ledger, the tier a completed provider call ran at.

    Precedence: the tier the service echoes back (header or ``serviceTier``
    structure) > the tier actually sent on the successful request (None
    after a retry-at-standard) > standard.  Providers call this once per
    successful call, AFTER any tier-rejection retry.  ``sent_tier`` may be
    the invoke string or the converse ``{"type": ...}`` structure.

    Sets a ContextVar; the provider ``stream()`` async generator runs in
    the executor's context (async generators have no context of their
    own), so the executor's meter sees it.
    """
    if isinstance(sent_tier, dict):
        sent_tier = sent_tier.get("type")
    tier = normalize_tier(resolved_tier_from_response(response) or sent_tier)
    _resolved_tier.set(tier)
    return tier


def clear_resolved_tier() -> None:
    """Forget the previous call's resolved tier (start of an iteration)."""
    _resolved_tier.set(None)


def billed_tier(model_id: str) -> str:
    """Best available answer to "what tier was this model billed at": the
    resolved tier if a provider reported one, else what would be sent now
    (which is None — standard — once the model has rejected the tier),
    else standard."""
    resolved = _resolved_tier.get()
    if resolved:
        return resolved
    return normalize_tier(requested_tier(model_id))
