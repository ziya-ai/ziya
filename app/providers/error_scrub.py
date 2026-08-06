"""Normalize provider error strings before substring classification.

Every provider's ``_classify_error`` is a ladder of ``in`` tests against
``str(exc)``.  For the SDK-based providers that string is the *repr of the
whole error envelope*, which embeds a server-generated ``request_id``:

    Error code: 401 - {'type': 'error', 'request_id':
    'req_fd7uushnpeyhohlgyt65xgi2w6ydnejgkfw6kpt6ibpdrv3ygliq', 'error':
    {'type': 'authentication_error', 'message': 'Signature expired: ...'}}

That id is opaque random alphanumerics, so it can contain the very tokens
the ladder matches on.  A request_id containing ``429`` makes
``"429" in error_str`` true and any error — a fatal 400, an auth failure —
is misclassified as THROTTLE, then retried with escalating backoff up to
80s before failing anyway.  ``rate`` (inside e.g. ``req_ratelimitx``) and
``too many`` are the same hazard against the lowercased form.

The probability per request is low; the consequence is a diagnosis that
points at entirely the wrong subsystem, and it is unreproducible because
the id is different every time.  Scrubbing the field costs one regex.

Deliberately scoped to the ``request_id`` FIELD rather than to random
hex-like runs: a genuine ``Error code: 429`` in the status position, or a
``rate limit`` in the human-readable message, must still classify.  The
field's own value is the only span with no diagnostic meaning.
"""

from __future__ import annotations

import re

# ``"request_id": "<value>"`` in any of the spellings observed across the
# Anthropic/OpenAI SDK reprs and AWS error payloads: quoted or bare key,
# snake/kebab/flat case, ``:`` or ``=`` separator, single or double quotes.
_REQUEST_ID_FIELD = re.compile(
    r"""["']?request[_-]?id["']?\s*[:=]\s*["'][^"']*["']""",
    re.IGNORECASE,
)


def scrub_request_id(error_str: str) -> str:
    """Return ``error_str`` with any ``request_id`` field value removed.

    Classification-only: the ORIGINAL string is still what gets surfaced to
    the user and written to logs, so the request_id remains available for
    support escalation.  Only the substring matching is protected.
    """
    if not error_str:
        return error_str
    return _REQUEST_ID_FIELD.sub("", error_str)


__all__ = ["scrub_request_id"]
