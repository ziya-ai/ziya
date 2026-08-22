"""Status reporting for applied ephemeral session grants (ASR F-004).

The manager stashes an applied session grant as a raw JSON string in
``MCPManager._session_grants[server]`` and forwards it into every shell
subprocess spawn for the current server session, where it is re-verified.
Until now nothing REPORTED that state: after "Apply now" succeeded, the
staging banner disappeared and the config UI looked identical to an
unescalated one, so a user could not tell a temporary grant was live (or
that a restart would silently void it).

This module is the pure, light-import core of that report, factored out of
``app/routes/mcp_routes.py`` so it can be unit-tested without importing the
routes module (which drags in the full agent stack, ~20s). The routes layer
calls :func:`session_grant_status` with attributes read off the manager.

The report is ADVISORY (UX only): the shell subprocess remains the
enforcement authority and re-verifies the grant at every spawn. But the
advisory check runs the same ``verify_session_grant`` the subprocess runs,
against the same nonce and trust anchors, so the indicator cannot claim
"active" for a grant the subprocess would clamp — a stale grant from a
previous server session, a grant minted for another server sharing
``~/.ziya``, or a forged/malformed record all report as absent.
"""

from __future__ import annotations

import json
from typing import Any, Dict, Optional


def session_grant_status(
    grant_json: Optional[str],
    current_nonce: Optional[str],
    ephemeral_pubkey_b64: Optional[str] = None,
    public_key_path: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Describe the applied session grant, or None when there is none.

    Args:
        grant_json: the raw grant record string the manager holds
            (``MCPManager._session_grants.get(server)``), or None.
        current_nonce: the manager's current server-start nonce.
        ephemeral_pubkey_b64: the manager's per-process public key (b64),
            required to validate ``cli-ephemeral`` grants.
        public_key_path: override for the root public key (tests only).

    Returns:
        ``{"active": True, "provider": ..., "grantedBy": ..., "grantedAt": ...,
        "delta": {field: [values]}}`` when a grant is held AND verifies for
        the current session; ``None`` otherwise. Never raises: any parse or
        verification failure reports None (no grant is *shown* that would not
        be *honored*).
    """
    if not grant_json or not current_nonce:
        return None
    try:
        record = json.loads(grant_json)
    except (ValueError, TypeError, json.JSONDecodeError):
        return None
    if not isinstance(record, dict):
        return None
    delta = record.get("delta")
    if not isinstance(delta, dict) or not delta:
        return None
    try:
        from app.config.scope_canonical import verify_session_grant
        if not verify_session_grant(
            delta,
            grant_json,
            current_nonce,
            public_key_path=public_key_path,
            ephemeral_pubkey_b64=ephemeral_pubkey_b64,
        ):
            return None
    except Exception:  # noqa: BLE001 — advisory surface must never raise
        return None
    return {
        "active": True,
        "provider": record.get("provider"),
        "grantedBy": record.get("granted_by"),
        "grantedAt": record.get("granted_at"),
        "delta": {
            k: (v if isinstance(v, list) else [str(v)])
            for k, v in delta.items()
        },
    }
